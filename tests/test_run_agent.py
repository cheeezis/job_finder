"""Tests for top-level source isolation in the productive runner."""

import unittest
from types import SimpleNamespace

from job_agent.models import Job, JobSource
from run_agent import build_run_summary, canonical_url, collect_jobs, format_duration


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

        jobs, status, reports = collect_jobs([failing, working])

        self.assertEqual([job.id for job in jobs], ["working:1"])
        self.assertEqual(status, {"broken": False, "working": True})
        self.assertEqual(
            reports,
            [
                {"name": "broken", "status": "failed", "jobs": 0},
                {"name": "working", "status": "success", "jobs": 1},
            ],
        )

    def test_empty_source_is_not_trusted_for_inactive_tracking(self):
        empty = SimpleNamespace(SOURCE_NAME="empty", fetch_jobs=lambda: [])

        jobs, status, reports = collect_jobs([empty])

        self.assertEqual(jobs, [])
        self.assertEqual(status, {"empty": False})
        self.assertEqual(reports, [{"name": "empty", "status": "empty", "jobs": 0}])

    def test_run_summary_tracks_source_counts_and_evaluation_outcomes(self):
        job = make_job("working:1")
        job.is_new = True
        results = {
            "included": [
                {
                    "llm_result": {"recommendation": "strong_match"},
                },
                {
                    "llm_result": {"recommendation": "not_recommended"},
                },
            ],
            "excluded": [{"id": "excluded:1"}],
        }

        summary = build_run_summary(
            duration_seconds=125.4,
            jobs=[job],
            results=results,
            memory_stats={"new": 1, "known": 0},
            llm_stats={"analyzed": 1, "cached": 1, "failed": 0},
            source_reports=[
                {"name": "working", "status": "success", "jobs": 1},
                {"name": "broken", "status": "failed", "jobs": 0},
            ],
        )

        self.assertEqual(summary["duration"], "2 Min. 05 Sek.")
        self.assertEqual(summary["recommended"], 1)
        self.assertEqual(summary["not_recommended"], 1)
        self.assertEqual(summary["sources"][0]["new"], 1)
        self.assertEqual(summary["sources"][1]["status"], "failed")
        self.assertEqual(format_duration(5), "5 Sek.")

    def test_canonical_url_keeps_jumo_job_offer_id(self):
        first = canonical_url(
            "https://jobs.jumo.de/engage/jobexchange/showJobOfferDetail.do?"
            "jobOfferId=first&j=jobexchange"
        )
        second = canonical_url(
            "https://jobs.jumo.de/engage/jobexchange/showJobOfferDetail.do?"
            "jobOfferId=second&j=jobexchange"
        )

        self.assertNotEqual(first, second)


if __name__ == "__main__":
    unittest.main()
