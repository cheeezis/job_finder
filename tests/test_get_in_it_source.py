"""Tests for get-in-IT's staged search and detail loading."""

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from job_finder.models import Job, JobSource, WorkMode, WorkflowStatus
from job_finder.sources import get_in_it
from job_finder.sources.common import load_detail_cache, save_detail_cache


API_RECORD = {
    "id": 311970,
    "title": "(Junior) DevOps Software Engineer (m/w/d)",
    "url": "/jobsuche/p311970",
    "homeOffice": True,
    "careers": [{"name": "System Engineering / Admin"}],
    "locations": [{"name": "München"}],
    "company": {"title": "Reply Deutschland SE"},
}


class GetInItSourceTests(unittest.TestCase):
    def detailed_job(self, fetched_at):
        return Job(
            id="get_in_it:311970",
            title="(Junior) DevOps Software Engineer (m/w/d)",
            company="Reply Deutschland SE",
            locations=["München"],
            sources=[
                JobSource(
                    source="get_in_it",
                    source_id="311970",
                    url="https://www.get-in-it.de/jobsuche/p311970",
                )
            ],
            description_raw="Python und Azure",
            description_clean="Python und Azure",
            work_mode=WorkMode.HYBRID,
            fetched_at=fetched_at,
        )

    def test_api_record_becomes_permissive_remote_summary(self):
        job = get_in_it.summary_job_from_record(API_RECORD)

        self.assertEqual(job.id, "get_in_it:311970")
        self.assertEqual(job.company, "Reply Deutschland SE")
        self.assertEqual(job.locations, ["München"])
        self.assertIn("System Engineering", job.description_clean)
        self.assertIs(job.work_mode, WorkMode.REMOTE)
        self.assertEqual(job.remote_percentage, 100)

    def test_fresh_cached_detail_is_reused_without_page_request(self):
        now = datetime(2026, 9, 8, 12, tzinfo=timezone.utc)
        cached = self.detailed_job(now - timedelta(days=2))
        with tempfile.TemporaryDirectory() as directory:
            cache_path = Path(directory) / "get_in_it.json"
            save_detail_cache(cache_path, {cached.primary_url: cached})
            with (
                patch.object(get_in_it, "collect_records", return_value=[API_RECORD]),
                patch.object(get_in_it, "fetch_job") as fetch_job,
            ):
                jobs = get_in_it.fetch_jobs(cache_path=cache_path, now=now)
                enriched = get_in_it.enrich_candidate_jobs(
                    jobs,
                    {jobs[0].id},
                    cache_path=cache_path,
                    now=now,
                )

        self.assertEqual(enriched, 0)
        self.assertEqual(jobs[0].description_clean, "Python und Azure")
        self.assertIs(jobs[0].work_mode, WorkMode.HYBRID)
        fetch_job.assert_not_called()

    def test_only_stale_prefiltered_candidate_gets_full_details(self):
        now = datetime(2026, 9, 8, 12, tzinfo=timezone.utc)
        stale = self.detailed_job(now - timedelta(days=8))
        detailed = self.detailed_job(now)
        detailed.description_clean = "Python, Azure und Kubernetes"
        excluded_record = {
            **API_RECORD,
            "id": 311971,
            "title": "Senior Sales Manager",
            "url": "/jobsuche/p311971",
        }
        with tempfile.TemporaryDirectory() as directory:
            cache_path = Path(directory) / "get_in_it.json"
            save_detail_cache(cache_path, {stale.primary_url: stale})
            with patch.object(
                get_in_it,
                "fetch_job",
                return_value=detailed,
            ) as fetch_job:
                jobs = get_in_it.jobs_from_records(
                    [API_RECORD, excluded_record],
                    cache_path,
                    now=now,
                )
                jobs[0].is_new = True
                jobs[0].workflow_status = WorkflowStatus.INTERESTING
                enriched = get_in_it.enrich_candidate_jobs(
                    jobs,
                    {jobs[0].id},
                    cache_path=cache_path,
                    now=now,
                )
                cache = load_detail_cache(cache_path)

        self.assertEqual(enriched, 1)
        self.assertEqual(jobs[0].description_clean, "Python, Azure und Kubernetes")
        self.assertTrue(jobs[0].is_new)
        self.assertIs(jobs[0].workflow_status, WorkflowStatus.INTERESTING)
        self.assertEqual(jobs[1].description_clean, "System Engineering / Admin")
        self.assertIn(detailed.primary_url, cache)
        fetch_job.assert_called_once_with(detailed.primary_url)


if __name__ == "__main__":
    unittest.main()
