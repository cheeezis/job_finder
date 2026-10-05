"""Persist explicit listing associations across review and subsequent crawls."""

import json
import tempfile
import unittest
from copy import deepcopy
from datetime import date
from pathlib import Path
from unittest.mock import patch

from job_finder.models import Job, JobSource
from job_finder.workflow.applications import load_application_overview
from job_finder.workflow.linked_listings import link_listing_to_application
from job_finder.workflow.memory import load_memory, save_memory, update_memory
from job_finder.workflow.review_data import load_review_jobs


class LinkedListingTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.memory_path = Path(self.directory.name) / "memory"
        self.recommendations_path = Path(self.directory.name) / "recommendations.json"
        self.application = {
            "title": "Junior Cloud Developer",
            "company": "Employer GmbH",
            "workflow_status": "applied",
            "first_seen_at": "2026-10-01T08:00:00+00:00",
            "source_urls": ["https://employer.example/job/cloud"],
            "source_names": ["original"],
            "locations": ["Fulda"],
            "salary_expectation_eur": 50_000,
            "review_note": "Bewerbungsnotiz",
            "workflow_history": [{"status": "applied", "occurred_on": "2026-10-01"}],
            "application_documents": [{"id": "cv", "name": "CV.pdf", "kind": "resume", "stored_name": "CV.pdf"}],
        }
        self.listing = {
            "title": "Junior Cloud Engineer",
            "company": "Recruiter GmbH",
            "workflow_status": "interesting",
            "source_urls": ["https://recruiter.example/job/cloud"],
            "source_names": ["studysmarter"],
            "locations": ["Fulda"],
            "review_note": "Notiz zur zusätzlichen Anzeige",
            "workflow_history": [{"status": "interesting", "occurred_on": "2026-10-05"}],
        }
        save_memory({"job:application": self.application, "job:listing": self.listing}, self.memory_path)
        self.recommendations_path.write_text(
            json.dumps(
                {
                    "recommendations": [
                        {
                            "id": "job:listing",
                            **self.listing,
                            "source_links": [{"url": self.listing["source_urls"][0], "source": "studysmarter"}],
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )

    def link(self):
        return link_listing_to_application("job:listing", "job:application", self.memory_path)

    def test_link_keeps_the_application_and_the_source_review_data(self):
        self.assertEqual(self.link(), {"job_id": "job:application"})
        memory = load_memory(self.memory_path)

        self.assertNotIn("job:listing", memory)
        target = memory["job:application"]
        for field in (
            "title",
            "company",
            "workflow_status",
            "salary_expectation_eur",
            "application_documents",
            "workflow_history",
            "review_note",
        ):
            self.assertEqual(target[field], self.application[field])
        self.assertEqual(target["linked_review_entries"]["job:listing"], self.listing)
        cards = load_review_jobs(self.recommendations_path, self.memory_path)
        self.assertEqual(
            [(card["id"], card["company"], card["workflow_status"]) for card in cards],
            [("job:application", "Employer GmbH", "applied")],
        )
        overview = load_application_overview(
            self.memory_path, as_of=date(2026, 10, 5), recommendations_path=self.recommendations_path
        )
        self.assertEqual(overview["statistics"]["total"], 1)
        self.assertEqual(
            {link["url"] for link in overview["applications"][0]["source_links"]},
            {self.application["source_urls"][0], self.listing["source_urls"][0]},
        )
        self.assertEqual(overview["applications"][0]["linked_listings"][0]["review_note"], self.listing["review_note"])

    def test_later_crawl_uses_the_explicit_alias_and_preserves_employer_identity(self):
        self.link()
        memory = load_memory(self.memory_path)
        listing = Job(
            id="job:listing",
            title=self.listing["title"],
            company=self.listing["company"],
            locations=["Fulda"],
            sources=[JobSource("studysmarter", "https://recruiter.example/job/new-url")],
            description_raw="Python",
            description_clean="Python",
        )

        update_memory([listing], memory)

        self.assertEqual(listing.id, "job:application")
        self.assertEqual(listing.company, self.application["company"])
        self.assertEqual(listing.title, self.application["title"])
        self.assertEqual(listing.workflow_status.value, "applied")
        self.assertNotIn("job:listing", memory)

    def test_repeated_link_is_idempotent_and_completed_applications_are_allowed(self):
        self.application["workflow_status"] = "rejected"
        save_memory({"job:application": self.application, "job:listing": self.listing}, self.memory_path)
        self.link()
        before = load_memory(self.memory_path)
        self.link()
        self.assertEqual(load_memory(self.memory_path), before)

    def test_invalid_links_and_two_applications_leave_every_record_unchanged(self):
        for source, target in (
            ("job:application", "job:listing"),
            ("missing", "job:application"),
            ("job:listing", "missing"),
            ("job:listing", "job:listing"),
        ):
            before = load_memory(self.memory_path)
            with self.subTest(source=source, target=target), self.assertRaises(ValueError):
                link_listing_to_application(source, target, self.memory_path)
            self.assertEqual(load_memory(self.memory_path), before)
        second_application = {**self.listing, "workflow_status": "applied"}
        save_memory({"job:application": self.application, "job:listing": second_application}, self.memory_path)
        before = load_memory(self.memory_path)
        with self.assertRaisesRegex(ValueError, "Zwei bestehende Bewerbungen"):
            self.link()
        self.assertEqual(load_memory(self.memory_path), before)

    def test_a_failed_write_rolls_back_the_association(self):
        before = deepcopy(load_memory(self.memory_path))
        with (
            patch("job_finder.workflow.memory.write_memory", side_effect=RuntimeError("write failed")),
            self.assertRaises(RuntimeError),
        ):
            self.link()
        self.assertEqual(load_memory(self.memory_path), before)
