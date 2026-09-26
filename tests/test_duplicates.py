"""Tests for joining jobs the user decided more than once."""

import os
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from job_finder.persistence.database import memory_scope
from job_finder.persistence.fact_sheets import fact_sheets, save_fact_sheet
from job_finder.workflow.duplicates import merge_duplicate_decisions
from job_finder.workflow.memory import load_memory, save_memory


def decided(status, source, place, first_seen, **extra):
    """A listing of the same job on one portal, decided on its own card."""
    return {
        "title": "Python Backend Developer (m/w/d)",
        "company": "IT Studio Rech GmbH",
        "locations": [place],
        "first_seen_at": f"2026-09-{first_seen}T08:00:00+00:00",
        "workflow_status": status,
        "workflow_history": [{"status": status, "occurred_on": f"2026-09-{first_seen}"}],
        "source_urls": [f"https://{source}.test/1"],
        "source_names": [source],
        **extra,
    }


class MergeDuplicatesTests(unittest.TestCase):
    def setUp(self):
        if os.environ.get("JOBFINDER_TEST_MODE") != "1":
            self.skipTest("Use scripts/test_postgres.py with an isolated test database")
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "state.sqlite3"

    def tearDown(self):
        self.directory.cleanup()

    def test_the_best_decision_keeps_the_job_and_its_copies_join_it(self):
        # As on 25.09.2026: interesting via Arbeitnow, the Remotely copy declined as a duplicate.
        memory = {
            "remotely:1": decided("ignored", "remotely", "Würzburg", "22"),
            "arbeitnow:1": decided("interesting", "arbeitnow", "Würzburg", "23"),
            "other:1": {**decided("ignored", "other", "Fulda", "20"), "title": "Tester"},
        }
        save_memory(memory, self.path)

        planned = merge_duplicate_decisions(path=self.path)

        self.assertEqual(load_memory(self.path), memory)
        self.assertEqual(planned["groups"], 1)
        self.assertEqual(
            planned["merges"][0]["kept"], {"id": "arbeitnow:1", "status": "interesting"}
        )
        self.assertEqual(
            planned["merges"][0]["merged"], [{"id": "remotely:1", "status": "ignored"}]
        )

        applied = merge_duplicate_decisions(apply=True, path=self.path)

        self.assertTrue(applied["applied"])
        joined = load_memory(self.path)
        self.assertEqual(sorted(joined), ["arbeitnow:1", "other:1"])
        kept = joined["arbeitnow:1"]
        self.assertEqual(kept["workflow_status"], "interesting")
        self.assertEqual(kept["workflow_history"], memory["arbeitnow:1"]["workflow_history"])
        self.assertEqual(
            kept["source_urls"], ["https://arbeitnow.test/1", "https://remotely.test/1"]
        )
        self.assertEqual(kept["source_names"], ["arbeitnow", "remotely"])
        self.assertEqual(kept["first_seen_at"], memory["remotely:1"]["first_seen_at"])

    def test_notes_and_a_fact_sheet_move_to_the_kept_job(self):
        memory = {
            "arbeitnow:1": decided("interesting", "arbeitnow", "Würzburg", "23"),
            "remotely:1": decided(
                "ignored", "remotely", "Remote", "25", review_note="Gleiche Stelle"
            ),
        }
        save_memory(memory, self.path)
        scope = memory_scope(self.path)
        save_fact_sheet("remotely:1", "gpt-5-mini", {"kurzgrund": "x"}, Decimal("0.04"), scope)

        merge_duplicate_decisions(apply=True, path=self.path)

        self.assertEqual(load_memory(self.path)["arbeitnow:1"]["review_note"], "Gleiche Stelle")
        sheets = fact_sheets(scope=scope)
        self.assertEqual(list(sheets), ["arbeitnow:1"])
        self.assertEqual(sheets["arbeitnow:1"]["fact_sheet"], {"kurzgrund": "x"})

    def test_undecided_copies_and_two_applications_are_left_alone(self):
        memory = {
            "arbeitnow:1": decided("applied", "arbeitnow", "Würzburg", "23"),
            "remotely:1": decided("rejected", "remotely", "Würzburg", "24"),
            "stepstone:1": {
                **decided("new", "stepstone", "Würzburg", "25"),
                "workflow_history": [],
            },
        }
        memory["remotely:1"]["workflow_history"].insert(
            0, {"status": "applied", "occurred_on": "2026-09-24"}
        )
        save_memory(memory, self.path)

        result = merge_duplicate_decisions(apply=True, path=self.path)

        self.assertEqual(result["groups"], 0)
        self.assertEqual(
            result["left_alone_with_two_applications"], [["arbeitnow:1", "remotely:1"]]
        )
        self.assertEqual(load_memory(self.path), memory)


if __name__ == "__main__":
    unittest.main()
