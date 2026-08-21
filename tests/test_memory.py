"""Tests for lifecycle metadata in the job memory."""

import json
import tempfile
import unittest
from pathlib import Path

from job_finder.memory import load_memory, save_memory, update_memory
from job_finder.models import Job, JobSource, WorkflowStatus


def make_job():
    return Job(
        id="test:123",
        title="Junior Python Developer",
        company="Example GmbH",
        locations=["Fulda"],
        sources=[
            JobSource(
                source="test",
                source_id="123",
                url="https://example.test/jobs/123",
            )
        ],
        description_raw="Python",
        description_clean="Python",
    )


class MemoryTests(unittest.TestCase):
    def test_new_job_receives_first_and_last_seen_timestamps(self):
        memory = {}
        job = make_job()

        stats = update_memory([job], memory)

        self.assertEqual(
            stats,
            {"new": 1, "known": 0, "inactive": 0, "reactivated": 0},
        )
        self.assertTrue(job.is_new)
        self.assertIsNotNone(job.first_seen_at)
        self.assertEqual(job.first_seen_at, job.last_seen_at)
        self.assertEqual(memory[job.id]["workflow_status"], "new")

    def test_known_job_keeps_first_seen_and_manual_status(self):
        memory = {}
        first_job = make_job()
        update_memory([first_job], memory)
        memory[first_job.id]["workflow_status"] = "interesting"
        memory[first_job.id]["workflow_history"] = [
            {"status": "applied", "occurred_on": "2026-08-01"}
        ]

        known_job = make_job()
        stats = update_memory([known_job], memory)

        self.assertEqual(
            stats,
            {"new": 0, "known": 1, "inactive": 0, "reactivated": 0},
        )
        self.assertFalse(known_job.is_new)
        self.assertEqual(known_job.first_seen_at, first_job.first_seen_at)
        self.assertEqual(known_job.workflow_status, WorkflowStatus.INTERESTING)
        self.assertEqual(
            memory[first_job.id]["workflow_history"],
            [{"status": "applied", "occurred_on": "2026-08-01"}],
        )

    def test_job_becomes_inactive_after_three_successful_missed_runs(self):
        memory = {}
        update_memory([make_job()], memory)

        first = update_memory([], memory, successful_sources={"test"})
        second = update_memory([], memory, successful_sources={"test"})
        third = update_memory([], memory, successful_sources={"test"})

        self.assertEqual(first["inactive"], 0)
        self.assertEqual(second["inactive"], 0)
        self.assertEqual(third["inactive"], 1)
        self.assertFalse(memory["test:123"]["active"])
        self.assertEqual(memory["test:123"]["missed_runs"], 3)

    def test_failed_source_does_not_count_as_a_missed_run(self):
        memory = {}
        update_memory([make_job()], memory)

        update_memory([], memory, successful_sources={"other"})

        self.assertEqual(memory["test:123"]["missed_runs"], 0)
        self.assertTrue(memory["test:123"]["active"])

    def test_returning_job_is_reactivated_without_losing_status(self):
        memory = {}
        update_memory([make_job()], memory)
        memory["test:123"].update(
            {"active": False, "missed_runs": 3, "workflow_status": "interesting"}
        )

        job = make_job()
        stats = update_memory([job], memory, successful_sources={"test"})

        self.assertEqual(stats["reactivated"], 1)
        self.assertTrue(memory["test:123"]["active"])
        self.assertEqual(memory["test:123"]["missed_runs"], 0)
        self.assertEqual(job.workflow_status, WorkflowStatus.INTERESTING)

    def test_known_source_url_reuses_reviewed_canonical_job(self):
        job = make_job()
        old_id = "stepstone:456"
        memory = {
            old_id: {
                "title": job.title,
                "company": job.company,
                "first_seen_at": "2026-07-01T08:00:00+00:00",
                "last_seen_at": "2026-08-01T08:00:00+00:00",
                "workflow_status": "applied",
                "workflow_history": [
                    {"status": "applied", "occurred_on": "2026-08-01"}
                ],
                "source_urls": [
                    "https://stepstone.test/jobs/456",
                    job.primary_url,
                ],
                "source_names": ["stepstone", "test"],
                "missed_runs": 2,
                "active": True,
            },
            job.id: {
                "title": job.title,
                "company": job.company,
                "first_seen_at": "2026-08-10T08:00:00+00:00",
                "last_seen_at": "2026-08-10T08:00:00+00:00",
                "workflow_status": "new",
                "source_urls": [job.primary_url],
                "source_names": ["test"],
                "missed_runs": 0,
                "active": True,
            },
        }

        stats = update_memory([job], memory, successful_sources={"test"})

        self.assertEqual(
            stats,
            {"new": 0, "known": 1, "inactive": 0, "reactivated": 0},
        )
        self.assertEqual(job.id, old_id)
        self.assertEqual(job.workflow_status, WorkflowStatus.APPLIED)
        self.assertNotIn("test:123", memory)
        self.assertEqual(
            memory[old_id]["workflow_history"],
            [{"status": "applied", "occurred_on": "2026-08-01"}],
        )
        self.assertEqual(
            memory[old_id]["source_urls"],
            ["https://stepstone.test/jobs/456", job.primary_url],
        )

    def test_application_wins_over_conflicting_review_entry(self):
        job = make_job()
        memory = {
            "stepstone:456": {
                "first_seen_at": "2026-07-01T08:00:00+00:00",
                "last_seen_at": "2026-08-01T08:00:00+00:00",
                "workflow_status": "applied",
                "source_urls": [job.primary_url],
                "source_names": ["stepstone"],
                "missed_runs": 0,
                "active": True,
            },
            job.id: {
                "first_seen_at": "2026-08-10T08:00:00+00:00",
                "last_seen_at": "2026-08-10T08:00:00+00:00",
                "workflow_status": "ignored",
                "source_urls": [job.primary_url],
                "source_names": ["test"],
                "missed_runs": 0,
                "active": True,
            },
        }

        update_memory([job], memory)

        self.assertEqual(job.id, "stepstone:456")
        self.assertEqual(job.workflow_status, WorkflowStatus.APPLIED)
        self.assertIn("stepstone:456", memory)
        self.assertIn("test:123", memory)

    def test_memory_file_has_an_explicit_version(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "seen_jobs.json"
            save_memory({"test:123": {"workflow_status": "new"}}, path)

            values = json.loads(path.read_text(encoding="utf-8"))
            restored = load_memory(path)

        self.assertEqual(values["version"], 2)
        self.assertIn("test:123", restored)

    def test_old_memory_format_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "seen_jobs.json"
            path.write_text(json.dumps({"old-url": {}}), encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "alte Format"):
                load_memory(path)


if __name__ == "__main__":
    unittest.main()
