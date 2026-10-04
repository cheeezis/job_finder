"""Tests for the StudySmarter source adapter."""

import tempfile
import unittest
from copy import deepcopy
from datetime import UTC, date, datetime
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError

from job_finder.models import WorkflowStatus, WorkMode
from job_finder.sources import studysmarter
from job_finder.sources.common import fetch_diagnostics, load_detail_cache, reset_fetch_diagnostics, save_detail_cache
from job_finder.workflow.memory import update_memory


class StudySmarterTests(unittest.TestCase):
    JOB_URL = "https://talents.studysmarter.de/companies/example/junior-python-developer-12345678/"
    # The API spells the city segment "koeln"; the site only routes "koln".
    CITY_LINK = "https://talents.studysmarter.de/companies/example/koeln/junior-python-developer-12345678/"
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
        self.assertTrue(all(search.get("is_remote_position") == "completely" for search in searches[1:]))

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
        cached = studysmarter.enrich_summary_job(studysmarter.summary_job_from_record(record), self.JOB_HTML)
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
        self.assertIn("job_listing_category=software-entwicklung%2Cit-beratung", url)
        self.assertIn("page=2", url)

    def test_record_collection_paginates_and_deduplicates(self):
        pages = [
            {"data": [{"id": 1, "link": "https://example.test/1"}], "total_pages": 2},
            {
                "data": [{"id": 1, "link": "https://example.test/1"}, {"id": 2, "link": "https://example.test/2"}],
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

    def test_appended_employment_place_and_categories_leave_the_title(self):
        for title, clean in (
            (
                "Junior SAP Data Analyst Inhouse (m/w/d) Vollzeit | Fulda | Hybrides Arbeiten möglich Professionals",
                "Junior SAP Data Analyst Inhouse (m/w/d)",
            ),
            ("SAP SD Consultant (m/w/d) | S/4HANA", "SAP SD Consultant (m/w/d) | S/4HANA"),
            ("Senior ABAP Entwickler:in (m/w/d) | Vollzeit", "Senior ABAP Entwickler:in (m/w/d) | Vollzeit"),
        ):
            with self.subTest(title=title):
                job = studysmarter.summary_job_from_record({"id": 1, "link": self.JOB_URL, "title": title})
                self.assertEqual(job.title, clean)

    def test_a_board_named_in_place_of_the_employer_leaves_the_company_unknown(self):
        for company, expected in (
            ("JOIN", ""),
            ("Arbeitsagentur", ""),
            ("Remotely", ""),
            ("Example GmbH", "Example GmbH"),
        ):
            with self.subTest(company=company):
                record = {"id": 1, "link": self.JOB_URL, "title": "Junior Developer", "company_name": company}
                self.assertEqual(studysmarter.summary_job_from_record(record).company, expected)

    def test_cached_board_employer_stays_unknown_without_refetching_details(self):
        for board in (" JOIN ", "Arbeitsagentur", "rEmOtElY"):
            with self.subTest(board=board), tempfile.TemporaryDirectory() as directory:
                record = {
                    "id": 12345678,
                    "link": self.JOB_URL,
                    "title": "Junior Python Developer (m/w/d)",
                    "company_name": board,
                    "locations": ["Fulda"],
                }
                cached = studysmarter.enrich_summary_job(studysmarter.summary_job_from_record(record), self.JOB_HTML)
                cached.company = board
                cached.fetched_at = datetime(2026, 8, 25, tzinfo=UTC)
                original = deepcopy(cached)
                cache_path = Path(directory) / "studysmarter.json"
                save_detail_cache(cache_path, {self.JOB_URL: cached})
                with patch.object(studysmarter, "fetch_text") as fetch:
                    jobs = studysmarter.jobs_from_records([record], cache_path)
                    enriched = studysmarter.enrich_candidate_jobs(
                        jobs, {jobs[0].id}, cache_path, now=datetime(2026, 8, 27, tzinfo=UTC)
                    )
                self.assertEqual(jobs[0].company, "")
                self.assertEqual(jobs[0].description_clean, original.description_clean)
                self.assertEqual(jobs[0].fetched_at, original.fetched_at)
                self.assertEqual(enriched, 0)
                fetch.assert_not_called()

    def test_unknown_summary_keeps_a_known_cached_employer(self):
        for company in ("", "JOIN", "Arbeitsagentur", "Remotely"):
            with self.subTest(company=company):
                record = {"id": 12345678, "link": self.JOB_URL, "company_name": company}
                summary = studysmarter.summary_job_from_record(record)
                cached = studysmarter.enrich_summary_job(summary, self.JOB_HTML)
                refreshed = studysmarter.with_current_summary(cached, summary)
                self.assertEqual(refreshed.company, "Example GmbH")
                self.assertEqual(refreshed.description_clean, cached.description_clean)

    def test_detail_board_employer_uses_known_summary_or_stays_unknown(self):
        for board in ("JOIN", "Arbeitsagentur", "Remotely"):
            for summary_company, expected in ((board, ""), ("Example GmbH", "Example GmbH")):
                with self.subTest(board=board, summary_company=summary_company):
                    record = {"id": 12345678, "link": self.JOB_URL, "company_name": summary_company}
                    html = self.JOB_HTML.replace('"name": "Example GmbH"', f'"name": "{board}"')
                    job = studysmarter.enrich_summary_job(studysmarter.summary_job_from_record(record), html)
                    self.assertEqual(job.company, expected)
                    self.assertIn("Entwicklung mit Python", job.description_clean)

    def test_fresh_details_keep_board_names_out_of_the_saved_cache(self):
        record = {
            "id": 12345678,
            "link": self.JOB_URL,
            "title": "Junior Python Developer (m/w/d)",
            "company_name": "JOIN",
            "locations": ["Fulda"],
        }
        html = self.JOB_HTML.replace('"name": "Example GmbH"', '"name": "JOIN"')
        jobs = [studysmarter.summary_job_from_record(record)]
        with tempfile.TemporaryDirectory() as directory:
            cache_path = Path(directory) / "studysmarter.json"
            with patch.object(studysmarter, "fetch_text", return_value=html) as fetch:
                enriched = studysmarter.enrich_candidate_jobs(jobs, {jobs[0].id}, cache_path)
            cache = load_detail_cache(cache_path)
            next_jobs = studysmarter.jobs_from_records([record], cache_path)
        self.assertEqual(enriched, 1)
        fetch.assert_called_once_with(self.JOB_URL)
        self.assertEqual(jobs[0].company, "")
        self.assertEqual(cache[self.JOB_URL].company, "")
        self.assertEqual(next_jobs[0].company, "")

    def test_cached_board_listing_respects_application_history_and_repost_window(self):
        for status, previous_date, expected_merge in (
            (WorkflowStatus.INTERVIEW, date(2026, 6, 1), True),
            (WorkflowStatus.REJECTED, date(2026, 7, 26), True),
            (WorkflowStatus.REJECTED, date(2026, 7, 25), False),
        ):
            with self.subTest(status=status, previous_date=previous_date), tempfile.TemporaryDirectory() as directory:
                record = {
                    "id": 12345678,
                    "link": self.JOB_URL,
                    "title": "Junior Python Developer (m/w/d)",
                    "company_name": "JOIN",
                    "locations": ["Fulda"],
                    "posted": "2026-08-25",
                }
                cached = studysmarter.enrich_summary_job(studysmarter.summary_job_from_record(record), self.JOB_HTML)
                cached.company = "JOIN"
                cached.fetched_at = datetime(2026, 8, 25, tzinfo=UTC)
                cache_path = Path(directory) / "studysmarter.json"
                save_detail_cache(cache_path, {self.JOB_URL: cached})
                application = {
                    "title": record["title"],
                    "company": "Example GmbH",
                    "locations": ["Fulda"],
                    "workflow_status": status.value,
                    "published_at": previous_date.isoformat(),
                    "first_seen_at": previous_date.isoformat(),
                    "source_names": ["manual"],
                    "source_urls": ["https://example.test/application"],
                    "workflow_history": [{"status": status.value, "occurred_on": previous_date.isoformat()}],
                    "review_note": "Keep this note",
                    "application_documents": [{"id": "resume", "kind": "resume"}],
                }
                memory = {
                    "manual:application": deepcopy(application),
                    "studysmarter:12345678": {
                        "title": record["title"],
                        "company": "JOIN",
                        "locations": ["Fulda"],
                        "workflow_status": "new",
                        "first_seen_at": "2026-08-25T00:00:00+00:00",
                        "source_names": ["studysmarter"],
                        "source_urls": [self.JOB_URL],
                    },
                }
                jobs = studysmarter.jobs_from_records([record], cache_path)
                update_memory(jobs, memory)
                self.assertEqual(jobs[0].id, "manual:application" if expected_merge else "studysmarter:12345678")
                self.assertEqual("studysmarter:12345678" not in memory, expected_merge)
                if not expected_merge:
                    self.assertEqual(memory["studysmarter:12345678"]["workflow_status"], "new")
                    self.assertEqual(memory["studysmarter:12345678"]["company"], "")
                for key in ("company", "workflow_status", "workflow_history", "review_note", "application_documents"):
                    self.assertEqual(memory["manual:application"][key], application[key])

    def test_job_import_uses_remote_flag_and_ignores_predicted_salary(self):
        record = {
            "id": 12345678,
            "link": self.JOB_URL,
            "company_name": "Example GmbH",
            "is_remote_positions": "completely",
            "salary": {"salary_type": "ai_predicted"},
        }

        job = studysmarter.enrich_summary_job(studysmarter.summary_job_from_record(record), self.JOB_HTML)

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
            cached_job = studysmarter.enrich_summary_job(studysmarter.summary_job_from_record(record), self.JOB_HTML)
            cached_job.fetched_at = datetime(2026, 8, 25, tzinfo=UTC)
            save_detail_cache(cache_path, {self.JOB_URL: cached_job})

            with (
                patch.object(studysmarter, "collect_records", return_value=[record]),
                patch.object(studysmarter, "fetch_text") as fetch_text,
            ):
                jobs = studysmarter.fetch_jobs(cache_path, now=datetime(2026, 8, 27, tzinfo=UTC))
                enriched = studysmarter.enrich_candidate_jobs(
                    jobs, {jobs[0].id}, cache_path, now=datetime(2026, 8, 27, tzinfo=UTC)
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
            with patch.object(studysmarter, "fetch_text", return_value=self.JOB_HTML) as fetch_text:
                enriched = studysmarter.enrich_candidate_jobs(jobs, {jobs[0].id}, cache_path)
            cache = load_detail_cache(cache_path)

        self.assertEqual(enriched, 1)
        self.assertIn("Entwicklung mit Python", jobs[0].description_clean)
        self.assertTrue(jobs[0].is_new)
        self.assertIs(jobs[0].workflow_status, WorkflowStatus.INTERESTING)
        self.assertEqual(jobs[1].description_clean, "")
        self.assertEqual(list(cache), [self.JOB_URL])
        fetch_text.assert_called_once_with(self.JOB_URL)

    def test_unreachable_candidate_is_recorded_without_partial_source(self):
        record = {
            "id": 12345678,
            "title": "Junior Python Developer (m/w/d)",
            "company_name": "Example GmbH",
            "link": self.JOB_URL,
            "is_remote_positions": "completely",
        }
        jobs = [studysmarter.summary_job_from_record(record)]
        reset_fetch_diagnostics()

        with (
            tempfile.TemporaryDirectory() as directory,
            patch.object(studysmarter, "fetch_text", side_effect=HTTPError(self.JOB_URL, 404, "Not Found", {}, None)),
            patch("builtins.print"),
        ):
            enriched = studysmarter.enrich_candidate_jobs(jobs, {jobs[0].id}, Path(directory) / "studysmarter.json")

        self.assertEqual(enriched, 0)
        self.assertEqual(fetch_diagnostics(), {"failed_segments": 0, "failed_candidates": 1})

    def test_detail_url_drops_city_segment_and_keeps_plain_links(self):
        self.assertEqual(studysmarter.detail_url(self.CITY_LINK), self.JOB_URL)
        self.assertEqual(studysmarter.detail_url(self.JOB_URL), self.JOB_URL)

    def test_candidate_with_transliterated_city_link_gets_cached_details(self):
        record = {
            "id": 12345678,
            "title": "Junior Python Developer (m/w/d)",
            "company_name": "Example GmbH",
            "link": self.CITY_LINK,
            "locations": ["Köln"],
            "is_remote_positions": "completely",
        }

        def fetch_page(url):
            if "/koeln/" in url:
                raise HTTPError(url, 404, "Not Found", {}, None)
            return self.JOB_HTML

        with tempfile.TemporaryDirectory() as directory:
            cache_path = Path(directory) / "studysmarter.json"
            with (
                patch.object(studysmarter, "collect_records", return_value=[record]),
                patch.object(studysmarter, "fetch_text", side_effect=fetch_page),
            ):
                jobs = studysmarter.fetch_jobs(cache_path)
                enriched = studysmarter.enrich_candidate_jobs(jobs, {jobs[0].id}, cache_path)
                next_run = studysmarter.fetch_jobs(cache_path)

        self.assertEqual(enriched, 1)
        self.assertEqual(jobs[0].primary_url, self.JOB_URL)
        self.assertIn("Entwicklung mit Python", jobs[0].description_clean)
        self.assertIn("Entwicklung mit Python", next_run[0].description_clean)
