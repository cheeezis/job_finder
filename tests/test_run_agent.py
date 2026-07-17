"""Tests for top-level source isolation in the productive runner."""

import unittest
from types import SimpleNamespace

from job_agent.models import Job, JobSource
from run_agent import collect_jobs


def make_job(job_id):
    return Job(
        id=job_id,
        title="Junior Developer",
        company="Example GmbH",
        locations=["Fulda"],
        sources=[JobSource(source=job_id.split(":")[0], url=f"https://example.test/{job_id}")],
        description_raw="Python",
        description_clean="Python",
    )


class RunAgentTests(unittest.TestCase):
    def test_failed_source_does_not_stop_following_sources(self):
        failing = SimpleNamespace(
            SOURCE_NAME="broken",
            fetch_jobs=lambda: (_ for _ in ()).throw(RuntimeError("kaputt")),
        )
        working = SimpleNamespace(
            SOURCE_NAME="working",
            fetch_jobs=lambda: [make_job("working:1")],
        )

        jobs, status = collect_jobs([failing, working])

        self.assertEqual([job.id for job in jobs], ["working:1"])
        self.assertEqual(status, {"broken": False, "working": True})

    def test_empty_source_is_not_trusted_for_inactive_tracking(self):
        empty = SimpleNamespace(SOURCE_NAME="empty", fetch_jobs=lambda: [])

        jobs, status = collect_jobs([empty])

        self.assertEqual(jobs, [])
        self.assertEqual(status, {"empty": False})


if __name__ == "__main__":
    unittest.main()
