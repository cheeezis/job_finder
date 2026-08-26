"""Tests for top-level source isolation in the productive runner."""

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from job_finder.models import Job, JobSource
from run_finder import (
    build_run_summary,
    canonical_url,
    collect_jobs,
    format_duration,
    run_pipeline,
    source_error_label,
)


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


class RunFinderTests(unittest.TestCase):
    def test_pipeline_persists_jobs_after_arbeitnow_enrichment(self):
        job = make_job("arbeitnow:1")
        results = {"included": [{"id": job.id}], "excluded": []}

        def enrich(jobs, _candidate_ids):
            jobs[0].description_clean = "Originalbeschreibung"
            return 1

        with tempfile.TemporaryDirectory() as directory:
            jobs_file = Path(directory) / "jobs.json"
            with (
                patch("run_finder.JOBS_FILE", jobs_file),
                patch("run_finder.create_backup"),
                patch("run_finder.collect_jobs", return_value=([job], [{"name": "arbeitnow", "status": "success", "jobs": 1}])),
                patch("run_finder.load_memory", return_value={}),
                patch("run_finder.save_memory"),
                patch("run_finder.update_memory", return_value={"new": 1, "known": 0, "inactive": 0, "reactivated": 0}),
                patch("run_finder.score_jobs", return_value=results) as score_jobs,
                patch("run_finder.arbeitnow.enrich_candidate_jobs", side_effect=enrich),
                patch("run_finder.write_recommendations"),
                patch("run_finder.process_notifications", return_value={"queued": 0, "ready": 0, "sent": 0, "failed": 0, "configuration_error": None}),
            ):
                output = io.StringIO()
                with redirect_stdout(output):
                    run_pipeline(SimpleNamespace(notify=False))
            persisted = json.loads(jobs_file.read_text(encoding="utf-8"))

        self.assertEqual(persisted[0]["description_clean"], "Originalbeschreibung")
        self.assertEqual(score_jobs.call_count, 2)
        self.assertIn("3/4 Vorfilter", output.getvalue())
        self.assertNotIn("Junior Developer", output.getvalue())

    def test_failed_source_does_not_stop_following_sources(self):
        failing = SimpleNamespace(SOURCE_NAME="broken", fetch_jobs=lambda: (_ for _ in ()).throw(RuntimeError("kaputt")))
        working = SimpleNamespace(SOURCE_NAME="working", fetch_jobs=lambda: [make_job("working:1")])
        jobs, reports = collect_jobs([failing, working])
        self.assertEqual([job.id for job in jobs], ["working:1"])
        self.assertEqual(reports[0]["error"], "RuntimeError")
        self.assertEqual(reports[1]["status"], "success")

    def test_empty_source_is_not_trusted_for_inactive_tracking(self):
        jobs, reports = collect_jobs([SimpleNamespace(SOURCE_NAME="empty", fetch_jobs=lambda: [])])
        self.assertEqual(jobs, [])
        self.assertEqual(reports, [{"name": "empty", "status": "empty", "jobs": 0}])

    def test_run_summary_tracks_source_counts_and_review_updates(self):
        job = make_job("working:1")
        job.is_new = True
        summary = build_run_summary(
            duration_seconds=125.4,
            jobs=[job],
            results={"included": [{"is_new": True}, {"content_changed": False}], "excluded": [{}]},
            memory_stats={"new": 1, "known": 0},
            source_reports=[
                {"name": "working", "status": "success", "jobs": 1},
                {"name": "broken", "status": "failed", "jobs": 0},
            ],
        )
        self.assertEqual(summary["duration"], "2 Min. 05 Sek.")
        self.assertEqual(summary["review_updates"], 1)
        self.assertEqual(summary["sources"][0]["new"], 1)
        self.assertEqual(format_duration(5), "5 Sek.")

    def test_canonical_url_keeps_jumo_job_offer_id(self):
        first = canonical_url("https://jobs.jumo.de/engage/jobexchange/showJobOfferDetail.do?jobOfferId=first&j=jobexchange")
        second = canonical_url("https://jobs.jumo.de/engage/jobexchange/showJobOfferDetail.do?jobOfferId=second&j=jobexchange")
        self.assertNotEqual(first, second)

    def test_source_error_label_uses_http_status_without_printing_urls(self):
        self.assertEqual(source_error_label(SimpleNamespace(code=429)), "HTTP 429")


if __name__ == "__main__":
    unittest.main()
