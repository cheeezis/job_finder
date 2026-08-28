"""Tests for the StudySmarter source adapter."""

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from job_finder.models import WorkflowStatus, WorkMode
from job_finder.sources import studysmarter
from job_finder.sources.common import (
    load_detail_cache,
    save_detail_cache,
)


class StudySmarterTests(unittest.TestCase):
    JOB_URL = (
        "https://talents.studysmarter.de/companies/example/"
        "junior-python-developer-12345678/"
    )
    JOB_HTML = """
    <script type="application/ld+json">
    {
      "@context": "https://schema.org",
      "@type": "JobPosting",
      "title": "Junior Python Developer (m/w/d)",
      "description": "<p>Entwicklung mit Python und teilweise Homeoffice.</p>",
      "datePosted": "2026-08-20",
      "employmentType": "FULL_TIME",
      "hiringOrganization": {"@type": "Organization", "name": "Example GmbH"},
      "jobLocation": {
        "@type": "Place",
        "address": {"@type": "PostalAddress", "addressLocality": "Fulda"}
      },
      "estimatedSalary": {
        "@type": "MonetaryAmount",
        "currency": "EUR",
        "value": {"minValue": 50000, "maxValue": 60000, "unitText": "YEAR"}
      }
    }
    </script>
    """

    def test_search_plan_covers_local_radius_and_remote_entry_terms(self):
        searches = list(studysmarter.build_searches())

        self.assertEqual(len(searches), 1 + len(studysmarter.REMOTE_ENTRY_TERMS))
        self.assertEqual(
            searches[0],
            {
                "city": studysmarter.STUDYSMARTER_LOCAL_SEARCH_LOCATION,
                "radius": studysmarter.LOCAL_SEARCH_RADIUS_KM,
                "job_listing_category": ",".join(studysmarter.IT_CATEGORIES),
            },
        )
        self.assertTrue(
            all(
                search.get("is_remote_position") == "completely"
                for search in searches[1:]
            )
        )

    def test_current_search_metadata_replaces_stale_cached_prefilter_fields(self):
        record = {
            "id": 12345678,
            "link": self.JOB_URL,
            "title": "Junior Python Developer (m/w/d)",
            "company_name": "Example GmbH",
            "locations": ["Fulda"],
            "is_remote_positions": "partly",
            "job_types": [{"name": "Vollzeit"}],
            "posted": "2026-08-20",
        }
        cached = studysmarter.job_from_record(record, self.JOB_HTML)
        cached.title = "Senior Developer"
        cached.locations = ["München"]
        cached.work_mode = WorkMode.ONSITE
        cached.remote_percentage = 0

        with tempfile.TemporaryDirectory() as directory:
            cache_path = Path(directory) / "studysmarter.json"
            save_detail_cache(cache_path, {self.JOB_URL: cached})
            with patch.object(studysmarter, "collect_records", return_value=[record]):
                jobs = studysmarter.fetch_jobs(cache_path=cache_path)

        self.assertEqual(jobs[0].title, "Junior Python Developer (m/w/d)")
        self.assertEqual(jobs[0].locations, ["Fulda"])
        self.assertEqual(jobs[0].work_mode, WorkMode.HYBRID)
        self.assertIsNone(jobs[0].remote_percentage)
        self.assertEqual(jobs[0].description_clean, cached.description_clean)
        self.assertTrue(jobs[0].content_changed)

    def test_search_url_contains_filters_and_page(self):
        url = studysmarter.build_search_url(
            {
                "keyword": "Junior",
                "is_remote_position": "completely",
                "job_listing_category": "software-entwicklung,it-beratung",
            },
            page=2,
        )

        self.assertIn("keyword=Junior", url)
        self.assertIn("is_remote_position=completely", url)
        self.assertIn(
            "job_listing_category=software-entwicklung%2Cit-beratung",
            url,
        )
        self.assertIn("page=2", url)

    def test_record_collection_paginates_and_deduplicates(self):
        pages = [
            {"data": [{"id": 1, "link": "https://example.test/1"}], "total_pages": 2},
            {
                "data": [
                    {"id": 1, "link": "https://example.test/1"},
                    {"id": 2, "link": "https://example.test/2"},
                ],
                "total_pages": 2,
            },
        ]
        with (
            patch.object(studysmarter, "fetch_json", side_effect=pages) as fetch,
            patch.object(studysmarter.time, "sleep") as sleep,
        ):
            records = studysmarter.collect_records([{"keyword": "Junior"}])

        self.assertEqual([record["id"] for record in records], [1, 2])
        self.assertEqual(fetch.call_count, 2)
        sleep.assert_called_once_with(studysmarter.REQUEST_PAUSE_SECONDS)

    def test_job_import_uses_remote_flag_and_ignores_predicted_salary(self):
        record = {
            "id": 12345678,
            "link": self.JOB_URL,
            "company_name": "Example GmbH",
            "is_remote_positions": "completely",
            "salary": {"salary_type": "ai_predicted"},
        }

        job = studysmarter.job_from_record(record, self.JOB_HTML)

        self.assertEqual(job.id, "studysmarter:12345678")
        self.assertEqual(job.title, "Junior Python Developer (m/w/d)")
        self.assertEqual(job.company, "Example GmbH")
        self.assertEqual(job.locations, ["Fulda"])
        self.assertEqual(job.work_mode, WorkMode.REMOTE)
        self.assertEqual(job.remote_percentage, 100)
        self.assertIsNone(job.salary_min_eur)
        self.assertIsNone(job.salary_max_eur)

    def test_fresh_detail_cache_avoids_another_page_request(self):
        with tempfile.TemporaryDirectory() as directory:
            cache_path = Path(directory) / "studysmarter.json"
            record = {
                "id": 12345678,
                "link": self.JOB_URL,
                "company_name": "Example GmbH",
                "is_remote_positions": "completely",
            }
            cached_job = studysmarter.job_from_record(record, self.JOB_HTML)
            cached_job.fetched_at = datetime(2026, 8, 25, tzinfo=timezone.utc)
            save_detail_cache(cache_path, {self.JOB_URL: cached_job})

            with (
                patch.object(studysmarter, "collect_records", return_value=[record]),
                patch.object(studysmarter, "fetch_text") as fetch_text,
            ):
                jobs = studysmarter.fetch_jobs(
                    cache_path,
                    now=datetime(2026, 8, 27, tzinfo=timezone.utc),
                )
                enriched = studysmarter.enrich_candidate_jobs(
                    jobs,
                    {jobs[0].id},
                    cache_path,
                    now=datetime(2026, 8, 27, tzinfo=timezone.utc),
                )

        self.assertEqual([job.id for job in jobs], ["studysmarter:12345678"])
        self.assertEqual(enriched, 0)
        fetch_text.assert_not_called()

    def test_only_prefiltered_candidate_is_enriched_and_cached(self):
        candidate_record = {
            "id": 12345678,
            "title": "Junior Python Developer (m/w/d)",
            "company_name": "Example GmbH",
            "link": self.JOB_URL,
            "locations": ["Fulda"],
            "is_remote_positions": "partly",
        }
        excluded_record = {
            **candidate_record,
            "id": 87654321,
            "title": "Senior Sales Manager",
            "link": self.JOB_URL.replace("12345678", "87654321"),
        }
        jobs = [
            studysmarter.summary_job_from_record(candidate_record),
            studysmarter.summary_job_from_record(excluded_record),
        ]
        jobs[0].is_new = True
        jobs[0].workflow_status = WorkflowStatus.INTERESTING

        with tempfile.TemporaryDirectory() as directory:
            cache_path = Path(directory) / "studysmarter.json"
            with patch.object(
                studysmarter,
                "fetch_text",
                return_value=self.JOB_HTML,
            ) as fetch_text:
                enriched = studysmarter.enrich_candidate_jobs(
                    jobs,
                    {jobs[0].id},
                    cache_path,
                )
            cache = load_detail_cache(cache_path)

        self.assertEqual(enriched, 1)
        self.assertIn("Entwicklung mit Python", jobs[0].description_clean)
        self.assertTrue(jobs[0].is_new)
        self.assertIs(jobs[0].workflow_status, WorkflowStatus.INTERESTING)
        self.assertEqual(jobs[1].description_clean, "")
        self.assertEqual(list(cache), [self.JOB_URL])
        fetch_text.assert_called_once_with(self.JOB_URL)


if __name__ == "__main__":
    unittest.main()
