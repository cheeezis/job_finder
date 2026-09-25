"""Tests for the agent's read-only past-decisions tool."""

import json
import os
import unittest
from datetime import date

from job_finder.agent.tools import MAX_NOTE_CHARS, past_decisions
from job_finder.persistence.database import transaction
from job_finder.persistence.postgres_store import write_memory


def decisions(arguments, current_job_id="job:current", rows=None):
    return json.loads(past_decisions(arguments, current_job_id, rows))


class PastDecisionsTests(unittest.TestCase):
    def test_company_matches_come_first_then_the_newest_title_matches(self):
        rows = [
            (
                "job:1",
                "SAP Consultant",
                "neylux GmbH",
                "ignored",
                None,
                "SAP-Kern",
                date(2026, 9, 1),
            ),
            ("job:2", "Java Developer", "SEITENBAU GmbH", "ignored", None, None, date(2026, 8, 1)),
            ("job:3", "SAP Berater", "Andere AG", "applied", "gut", None, date(2026, 9, 20)),
            ("job:current", "SAP Consultant", "SEITENBAU", "interesting", None, None, None),
        ]

        found = decisions({"company": "Seitenbau", "title_keywords": ["SAP"]}, rows=rows)

        self.assertEqual(
            [(entry["titel"], entry["treffer"]) for entry in found["entscheidungen"]],
            [("Java Developer", "Firma"), ("SAP Berater", "Titel"), ("SAP Consultant", "Titel")],
        )
        self.assertEqual(found["entscheidungen"][2]["entscheidung"], "Nicht interessant")
        self.assertEqual(found["entscheidungen"][2]["notiz"], "SAP-Kern")

    def test_short_words_do_not_match_everything_and_notes_are_cut(self):
        rows = [("job:1", "Sapient Developer", "Firma X", "ignored", None, "x" * 999, None)]

        self.assertEqual(
            decisions({"company": None, "title_keywords": ["SAP"]}, rows=rows),
            {"entscheidungen": []},
        )
        found = decisions({"company": "Firma X", "title_keywords": []}, rows=rows)
        self.assertEqual(len(found["entscheidungen"][0]["notiz"]), MAX_NOTE_CHARS)

    def test_unusable_arguments_get_an_error_the_model_can_read(self):
        for arguments in ({"company": None, "title_keywords": ["IT"]}, "kein Objekt", {}):
            with self.subTest(arguments=arguments):
                self.assertIn("fehler", decisions(arguments, rows=[]))


class PastDecisionsDatabaseTests(unittest.TestCase):
    def setUp(self):
        if os.environ.get("JOBFINDER_TEST_MODE") != "1":
            self.skipTest("Use scripts/test_postgres.py with an isolated test database")
        with transaction() as connection:
            self.assertTrue(connection.info.dbname.endswith("_test"))
            connection.execute("TRUNCATE job_state CASCADE")

    def test_only_the_users_own_decisions_are_read(self):
        def job(title, status, **extra):
            return {"title": title, "company": "SEITENBAU GmbH", "workflow_status": status, **extra}

        with transaction() as connection:
            write_memory(
                connection,
                "default",
                {},
                {
                    "job:decided": job(
                        "Java Developer",
                        "ignored",
                        review_note="Java zu stark",
                        workflow_history=[
                            {"status": "interesting", "occurred_on": "2026-09-01"},
                            {"status": "ignored", "occurred_on": "2026-09-10"},
                        ],
                    ),
                    "job:new": job("Python Developer", "new"),
                    "job:closed": job(
                        "Go Developer", "ignored", availability_checked_at="2026-09-12T08:00:00"
                    ),
                },
            )

        found = decisions({"company": "SEITENBAU", "title_keywords": []})

        self.assertEqual(
            found["entscheidungen"],
            [
                {
                    "titel": "Java Developer",
                    "firma": "SEITENBAU GmbH",
                    "entscheidung": "Nicht interessant",
                    "datum": "2026-09-10",
                    "bewertung": None,
                    "notiz": "Java zu stark",
                    "treffer": "Firma",
                }
            ],
        )


if __name__ == "__main__":
    unittest.main()
