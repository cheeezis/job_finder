"""Correcting an application's title and company, and the links its page lists."""

import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from job_finder.models import Job, JobSource
from job_finder.workflow.applications import load_application_overview
from job_finder.workflow.memory import load_memory, save_memory, update_memory
from job_finder.workflow.review_actions import update_application_details


class ApplicationDetailsTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.memory_path = Path(directory.name) / "memory"
        self.recommendations_path = Path(directory.name) / "recommendations.json"
        self.recommendations_path.write_text('{"recommendations": []}', encoding="utf-8")
        save_memory(
            {
                "job:application": {
                    "title": "Junior IT-Administrator (m/w/d)",
                    "company": "",
                    "workflow_status": "applied",
                    "first_seen_at": "2026-10-01T08:00:00+00:00",
                    "source_urls": ["https://board.example/job/admin"],
                    "source_names": ["arbeitsagentur"],
                    "workflow_history": [{"status": "applied", "occurred_on": "2026-10-01"}],
                },
                "job:listing": {
                    "title": "Junior Admin",
                    "company": "Other GmbH",
                    "workflow_status": "new",
                    "first_seen_at": "2026-10-01T08:00:00+00:00",
                    "source_urls": ["https://board.example/job/other"],
                },
            },
            self.memory_path,
        )

    def overview(self, sheets=None):
        with patch("job_finder.workflow.applications.fact_sheets", return_value=sheets or {}):
            return load_application_overview(
                self.memory_path, as_of=date(2026, 10, 8), recommendations_path=self.recommendations_path
            )

    def test_corrected_title_and_company_are_saved_and_survive_a_later_run(self):
        result = update_application_details("job:application", " Junior  IT-Admin ", "Example GmbH", self.memory_path)

        self.assertEqual(result, {"title": "Junior IT-Admin", "company": "Example GmbH"})
        (application,) = self.overview()["applications"]
        self.assertEqual((application["title"], application["company"]), ("Junior IT-Admin", "Example GmbH"))

        memory = load_memory(self.memory_path)
        found_again = Job(
            id="job:application",
            title="Junior IT-Administrator (m/w/d)",
            company="",
            locations=[],
            sources=[JobSource("arbeitsagentur", "https://board.example/job/admin")],
            description_raw="Admin",
            description_clean="Admin",
        )
        update_memory([found_again], memory)
        self.assertEqual(
            (memory["job:application"]["title"], memory["job:application"]["company"]),
            ("Junior IT-Admin", "Example GmbH"),
        )

    def test_empty_values_and_jobs_without_application_are_refused(self):
        for title, company, message in (
            ("", "Example GmbH", "Titel"),
            ("Admin", "   ", "Firma"),
            ("Admin", "x" * 201, "Firma"),
        ):
            with self.subTest(title=title, company=company), self.assertRaisesRegex(ValueError, message):
                update_application_details("job:application", title, company, self.memory_path)
        with self.assertRaisesRegex(ValueError, "keine Bewerbung"):
            update_application_details("job:listing", "Admin", "Example GmbH", self.memory_path)
        self.assertEqual(load_memory(self.memory_path)["job:application"]["company"], "")

    def test_the_page_lists_the_agents_pages_beside_the_finders_listings(self):
        sheets = {
            "job:application": {
                "fact_sheet": {
                    "quellen": [
                        "https://example.com/about",
                        "https://board.example/job/admin",
                        "https://example.com/about",
                    ]
                }
            }
        }

        (application,) = self.overview(sheets)["applications"]

        self.assertEqual([link["url"] for link in application["source_links"]], ["https://board.example/job/admin"])
        # The listing the finder already shows is not repeated among the agent's pages.
        self.assertEqual(application["agent_sources"], ["https://example.com/about"])
        self.assertNotIn("linked_listings", application)


if __name__ == "__main__":
    unittest.main()
