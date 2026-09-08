"""Tests for the public GermanTechJobs XML feed adapter."""

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from job_finder.models import WorkMode
from job_finder.sources import german_tech_jobs


FEED = """<?xml version="1.0" encoding="UTF-8"?>
<jobs>
  <job id="feed-1" category="IT">
    <id><![CDATA[feed-1]]></id>
    <title><![CDATA[Junior Python Developer (m/w/d)]]></title>
    <link><![CDATA[https://germantechjobs.de/jobs/example]]></link>
    <apply_url><![CDATA[https://company.test/apply]]></apply_url>
    <country><![CDATA[Germany]]></country>
    <location><![CDATA[Full Remote]]></location>
    <city><![CDATA[Berlin]]></city>
    <salary><![CDATA[50.000 - 65.000 € per year]]></salary>
    <company-name><![CDATA[Example GmbH]]></company-name>
    <job-type><![CDATA[Full-Time]]></job-type>
    <pubdate><![CDATA[16.08.2026]]></pubdate>
    <description><![CDATA[<p>Python und APIs. Fully remote.</p>]]></description>
  </job>
</jobs>
"""


class GermanTechJobsTests(unittest.TestCase):
    def test_feed_maps_structured_salary_remote_and_application_url(self):
        fetched_at = datetime(2026, 8, 17, tzinfo=timezone.utc)

        jobs, invalid = german_tech_jobs.parse_feed(FEED, fetched_at)

        self.assertEqual(invalid, 0)
        self.assertEqual(len(jobs), 1)
        job = jobs[0]
        self.assertEqual(job.id, "german_tech_jobs:feed-1")
        self.assertEqual(job.company, "Example GmbH")
        self.assertEqual(job.locations, ["Full Remote"])
        self.assertIs(job.work_mode, WorkMode.REMOTE)
        self.assertEqual(job.remote_percentage, 100)
        self.assertEqual((job.salary_min_eur, job.salary_max_eur), (50_000, 65_000))
        self.assertEqual(job.published_at.isoformat(), "2026-08-16")
        self.assertEqual(job.employment_type, "Full-Time")
        self.assertEqual(job.sources[0].application_url, "https://company.test/apply")
        self.assertEqual(job.description_clean, "Python und APIs. Fully remote.")

    def test_invalid_feed_record_is_reported_without_losing_valid_jobs(self):
        jobs, invalid = german_tech_jobs.parse_feed(
            FEED.replace("</jobs>", "<job><title>Incomplete</title></job></jobs>")
        )

        self.assertEqual(len(jobs), 1)
        self.assertEqual(invalid, 1)

    def test_non_annual_or_non_euro_salary_is_not_imported(self):
        self.assertEqual(
            german_tech_jobs.annual_salary_eur("4.000 - 5.000 € per month"),
            (None, None),
        )
        self.assertEqual(
            german_tech_jobs.annual_salary_eur("60,000 - 80,000 USD per year"),
            (None, None),
        )

    def test_recent_cache_is_used_and_marked_after_feed_failure(self):
        now = datetime(2026, 8, 17, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory) / "feed.json"
            jobs, _invalid = german_tech_jobs.parse_feed(FEED, now)
            german_tech_jobs.save_feed_cache(cache, jobs, now)
            with patch.object(
                german_tech_jobs, "fetch_text", side_effect=OSError("offline")
            ):
                result = german_tech_jobs.fetch_jobs_with_report(cache, now=now)

        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["details"]["failed_segments"], 1)
        self.assertEqual(len(result["jobs"]), 1)
        self.assertTrue(result["jobs"][0].cache_stale)

    def test_cache_older_than_three_days_is_not_used(self):
        now = datetime(2026, 8, 17, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory) / "feed.json"
            jobs, _invalid = german_tech_jobs.parse_feed(FEED, now)
            german_tech_jobs.save_feed_cache(
                cache, jobs, now - timedelta(days=4)
            )
            with patch.object(
                german_tech_jobs, "fetch_text", side_effect=OSError("offline")
            ):
                with self.assertRaises(OSError):
                    german_tech_jobs.fetch_jobs_with_report(cache, now=now)


if __name__ == "__main__":
    unittest.main()
