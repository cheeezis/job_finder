"""Tests for the Arbeitnow source adapter."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError

from job_finder.sources import arbeitnow
from job_finder.sources.common import (
    load_detail_cache,
    save_detail_cache,
)


class ArbeitnowTests(unittest.TestCase):
    def test_collect_records_paginates_and_removes_repeated_slugs(self):
        pages = [
            {
                "data": [{"slug": "one"}, {"slug": "two"}],
                "links": {"next": "page-2"},
            },
            {
                "data": [{"slug": "two"}, {"slug": "three"}],
                "links": {"next": None},
            },
        ]

        with (
            patch.object(arbeitnow, "fetch_json", side_effect=pages) as fetch,
            patch.object(arbeitnow.time, "sleep") as sleep,
        ):
            records = arbeitnow.collect_records()

        self.assertEqual([record["slug"] for record in records], ["one", "two", "three"])
        self.assertEqual(fetch.call_count, 2)
        sleep.assert_called_once_with(arbeitnow.REQUEST_PAUSE_SECONDS)

    def test_fetch_jobs_marks_changed_api_records(self):
        url = "https://www.arbeitnow.com/jobs/example/one"
        old = arbeitnow.job_from_record(
            {
                "slug": "one",
                "company_name": "Example GmbH",
                "title": "Junior Developer",
                "description": "<p>Python</p>",
                "remote": True,
                "url": url,
                "location": "Fulda",
            }
        )

        with tempfile.TemporaryDirectory() as directory:
            cache_path = Path(directory) / "arbeitnow.json"
            save_detail_cache(cache_path, {url: old})
            changed = {
                "slug": "one",
                "company_name": "Example GmbH",
                "title": "Junior Developer",
                "description": "<p>Python und APIs</p>",
                "remote": True,
                "url": url,
                "location": "Fulda",
            }
            with patch.object(arbeitnow, "collect_records", return_value=[changed]):
                jobs = arbeitnow.fetch_jobs(cache_path=cache_path)

        self.assertEqual(len(jobs), 1)
        self.assertTrue(jobs[0].content_changed)
        self.assertIn("Python und APIs", jobs[0].description_clean)
        self.assertIsNone(jobs[0].sources[0].application_url)

    def test_fetch_jobs_keeps_original_link_and_bridges_api_text_variants(self):
        url = "https://www.arbeitnow.com/jobs/example/mediated"
        placeholder = "Find Jobs in Germany on Arbeitnow"
        original_text = "Original Python job " * 20
        application_url = "https://company.test/jobs/mediated"
        enriched = arbeitnow.job_from_record(
            {
                "slug": "mediated",
                "company_name": "Example GmbH",
                "title": "Junior Developer",
                "description": placeholder,
                "remote": True,
                "url": url,
                "location": "Fulda",
            }
        )
        enriched.description_raw = f"<main>{original_text}</main>"
        enriched.description_clean = original_text
        enriched.sources[0].application_url = application_url
        portal_text = "Current Arbeitnow portal text with Python. " * 10
        full_text_record = {
            "slug": "mediated",
            "company_name": "Example GmbH",
            "title": "Junior Developer",
            "description": f"<p>{portal_text}</p>",
            "remote": True,
            "url": url,
            "location": "Fulda",
        }
        placeholder_record = dict(full_text_record, description=placeholder)

        with tempfile.TemporaryDirectory() as directory:
            cache_path = Path(directory) / "arbeitnow.json"
            save_detail_cache(cache_path, {url: enriched})
            with patch.object(
                arbeitnow,
                "collect_records",
                return_value=[full_text_record],
            ):
                full_text_jobs = arbeitnow.fetch_jobs(cache_path=cache_path)
            with patch.object(
                arbeitnow,
                "collect_records",
                return_value=[placeholder_record],
            ):
                placeholder_jobs = arbeitnow.fetch_jobs(cache_path=cache_path)
            with patch.object(arbeitnow, "fetch_text_with_final_url") as fetch:
                enriched_count = arbeitnow.enrich_candidate_jobs(
                    placeholder_jobs,
                    {placeholder_jobs[0].id},
                    cache_path=cache_path,
                )

        self.assertEqual(full_text_jobs[0].description_clean, portal_text.strip())
        self.assertTrue(full_text_jobs[0].content_changed)
        self.assertEqual(placeholder_jobs[0].description_clean, portal_text.strip())
        self.assertFalse(placeholder_jobs[0].content_changed)
        for job in (full_text_jobs[0], placeholder_jobs[0]):
            self.assertEqual(job.sources[0].application_url, application_url)
        self.assertEqual(enriched_count, 0)
        fetch.assert_not_called()

    def test_rate_limited_api_uses_recent_cache(self):
        url = "https://www.arbeitnow.com/jobs/example/cached"
        cached = arbeitnow.job_from_record(
            {
                "slug": "cached",
                "company_name": "Example GmbH",
                "title": "Junior Developer",
                "description": "<p>Python</p>",
                "remote": True,
                "url": url,
                "location": "Fulda",
            }
        )
        limited = HTTPError(arbeitnow.API_URL, 429, "Too Many Requests", None, None)

        with tempfile.TemporaryDirectory() as directory:
            cache_path = Path(directory) / "arbeitnow.json"
            save_detail_cache(cache_path, {url: cached})
            with patch.object(arbeitnow, "collect_records", side_effect=limited):
                jobs = arbeitnow.fetch_jobs(cache_path=cache_path)

        self.assertEqual([job.id for job in jobs], ["arbeitnow:cached"])
        self.assertFalse(jobs[0].content_changed)

    def test_fetch_jobs_reuses_enrichment_and_keeps_only_current_snapshot(self):
        current_url = "https://www.arbeitnow.com/jobs/example/current"
        removed_url = "https://www.arbeitnow.com/jobs/example/removed"
        placeholder = "Find Jobs in Germany on Arbeitnow"
        enriched = arbeitnow.job_from_record(
            {
                "slug": "current",
                "company_name": "Example GmbH",
                "title": "Junior Developer",
                "description": placeholder,
                "remote": True,
                "url": current_url,
                "location": "Fulda",
            }
        )
        enriched.description_raw = "<main>Original Python job</main>"
        enriched.description_clean = "Original Python job " * 20
        enriched.sources[0].application_url = "https://company.test/jobs/current"
        removed = arbeitnow.job_from_record(
            {
                "slug": "removed",
                "company_name": "Old GmbH",
                "title": "Old Developer",
                "description": "Old job",
                "remote": True,
                "url": removed_url,
                "location": "Berlin",
            }
        )
        current_record = {
            "slug": "current",
            "company_name": "Example GmbH",
            "title": "Junior Developer",
            "description": placeholder,
            "remote": True,
            "url": current_url,
            "location": "Fulda",
        }

        with tempfile.TemporaryDirectory() as directory:
            cache_path = Path(directory) / "arbeitnow.json"
            save_detail_cache(
                cache_path,
                {current_url: enriched, removed_url: removed},
            )
            with patch.object(
                arbeitnow,
                "collect_records",
                return_value=[current_record],
            ):
                jobs = arbeitnow.fetch_jobs(cache_path=cache_path)
            saved = load_detail_cache(cache_path)

        self.assertEqual(len(jobs), 1)
        self.assertIn("Original Python job", jobs[0].description_clean)
        self.assertEqual(
            jobs[0].sources[0].application_url,
            "https://company.test/jobs/current",
        )
        self.assertEqual(list(saved), [current_url])
        self.assertFalse(jobs[0].content_changed)

    def test_direct_description_never_requests_original_page(self):
        job = arbeitnow.job_from_record(
            {
                "slug": "direct",
                "company_name": "Example GmbH",
                "title": "Junior Developer",
                "description": "A complete direct job description with Python and APIs.",
                "remote": True,
                "url": "https://www.arbeitnow.com/jobs/example/direct",
                "location": "Fulda",
            }
        )

        with tempfile.TemporaryDirectory() as directory:
            cache_path = Path(directory) / "arbeitnow.json"
            with patch.object(arbeitnow, "fetch_text_with_final_url") as fetch:
                count = arbeitnow.enrich_candidate_jobs(
                    [job],
                    {job.id},
                    cache_path=cache_path,
                )

        self.assertEqual(count, 0)
        fetch.assert_not_called()

    def test_missing_cached_application_is_not_reused(self):
        record = {
            "slug": "missing",
            "company_name": "Example GmbH",
            "title": "Junior Developer",
            "description": "Find Jobs in Germany on Arbeitnow",
            "remote": True,
            "url": "https://www.arbeitnow.com/jobs/example/missing",
            "location": "Fulda",
        }
        previous = arbeitnow.job_from_record(record)
        previous.description_clean = "Cached but invalid description " * 20
        previous.sources[0].application_url = (
            "https://company.test/jobs/missing?not_found=true"
        )
        current = arbeitnow.job_from_record(record)

        reused = arbeitnow.reuse_cached_enrichment(current, previous)

        self.assertFalse(reused)
        self.assertIsNone(current.sources[0].application_url)
        self.assertTrue(arbeitnow.is_placeholder_description(current.description_clean))

    def test_candidate_enrichment_keeps_original_application_url_and_text(self):
        job = arbeitnow.job_from_record(
            {
                "slug": "one",
                "company_name": "Example GmbH",
                "title": "Junior Developer",
                "description": "Find Jobs in Germany on Arbeitnow",
                "remote": True,
                "url": "https://www.arbeitnow.com/jobs/example/one",
                "location": "Fulda",
            }
        )
        html = '<meta property="og:description" content="' + ("Python APIs " * 30) + '">'

        with tempfile.TemporaryDirectory() as directory:
            cache_path = Path(directory) / "arbeitnow.json"
            with patch.object(
                arbeitnow,
                "fetch_text_with_final_url",
                return_value=("https://company.test/jobs/one", html),
            ):
                count = arbeitnow.enrich_candidate_jobs(
                    [job], {job.id}, cache_path=cache_path
                )

        self.assertEqual(count, 1)
        self.assertEqual(job.sources[0].application_url, "https://company.test/jobs/one")
        self.assertIn("Python APIs", job.description_clean)

    def test_external_description_prefers_structured_job_posting(self):
        structured_description = "Structured Python job " * 30
        html = (
            '<meta content="Short portal summary" property="og:description">'
            '<script type="application/ld+json">'
            + json.dumps(
                {
                    "@type": "JobPosting",
                    "description": f"<p>{structured_description}</p>",
                }
            )
            + "</script>"
        )

        self.assertEqual(
            arbeitnow.external_description(html),
            structured_description.strip(),
        )

    def test_failed_enrichment_does_not_keep_application_url(self):
        job = arbeitnow.job_from_record(
            {
                "slug": "missing",
                "company_name": "Example GmbH",
                "title": "Junior Developer",
                "description": "Find Jobs in Germany on Arbeitnow",
                "remote": True,
                "url": "https://www.arbeitnow.com/jobs/example/missing",
                "location": "Fulda",
            }
        )
        html = '<meta property="og:description" content="' + ("Python " * 40) + '">'

        with tempfile.TemporaryDirectory() as directory:
            cache_path = Path(directory) / "arbeitnow.json"
            with patch.object(
                arbeitnow,
                "fetch_text_with_final_url",
                return_value=("https://company.test/jobs?not_found=true", html),
            ):
                count = arbeitnow.enrich_candidate_jobs(
                    [job],
                    {job.id},
                    cache_path=cache_path,
                )
            with patch.object(
                arbeitnow,
                "fetch_text_with_final_url",
                return_value=(
                    "https://company.test/jobs/missing",
                    '<meta property="og:description" content="Too short">',
                ),
            ):
                short_count = arbeitnow.enrich_candidate_jobs(
                    [job],
                    {job.id},
                    cache_path=cache_path,
                )

        self.assertEqual(count, 0)
        self.assertEqual(short_count, 0)
        self.assertIsNone(job.sources[0].application_url)
        self.assertTrue(arbeitnow.is_placeholder_description(job.description_clean))

if __name__ == "__main__":
    unittest.main()
