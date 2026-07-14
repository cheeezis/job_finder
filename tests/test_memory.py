"""Tests for lifecycle metadata in the job memory."""

import json
import tempfile
import unittest
from pathlib import Path

from job_agent.memory import load_memory, save_memory, update_memory
from job_agent.models import Job, JobSource, WorkflowStatus


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

        self.assertEqual(stats, {"new": 1, "known": 0})
        self.assertTrue(job.is_new)
        self.assertIsNotNone(job.first_seen_at)
        self.assertEqual(job.first_seen_at, job.last_seen_at)
        self.assertEqual(memory[job.id]["workflow_status"], "new")

    def test_known_job_keeps_first_seen_and_manual_status(self):
        memory = {}
        first_job = make_job()
        update_memory([first_job], memory)
        memory[first_job.id]["workflow_status"] = "interesting"

        known_job = make_job()
        stats = update_memory([known_job], memory)

        self.assertEqual(stats, {"new": 0, "known": 1})
        self.assertFalse(known_job.is_new)
        self.assertEqual(known_job.first_seen_at, first_job.first_seen_at)
        self.assertEqual(known_job.workflow_status, WorkflowStatus.INTERESTING)

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
