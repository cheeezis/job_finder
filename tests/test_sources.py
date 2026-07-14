"""Tests for source-specific search configuration and caching."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import ANY, Mock, patch
from urllib.error import HTTPError

from job_agent.config import STEPSTONE_SEARCH_LOCATIONS, STEPSTONE_SEARCH_TERMS
from job_agent.sources import stepstone
from job_agent.sources.stepstone import build_search_url


class StepStoneSearchTests(unittest.TestCase):
    def test_search_plan_uses_sixteen_role_families(self):
        self.assertEqual(len(STEPSTONE_SEARCH_TERMS), 16)
        self.assertEqual(
            len(STEPSTONE_SEARCH_TERMS) * len(STEPSTONE_SEARCH_LOCATIONS),
            32,
        )

    def test_local_search_uses_postcode_and_radius(self):
        url = build_search_url("Python Developer", "12345", page=2)

        self.assertEqual(
            url,
            "https://www.stepstone.de/jobs/Python-Developer/"
            "in-12345?page=2&radius=30",
        )

    def test_remote_search_does_not_add_local_radius(self):
        url = build_search_url("Python Developer", "Remote")

        self.assertEqual(
            url,
            "https://www.stepstone.de/jobs/Python-Developer/in-Remote?page=1",
        )


class StepStoneCacheTests(unittest.TestCase):
    def test_fetch_jobs_only_downloads_uncached_details(self):
        cached_url = "https://www.stepstone.de/stellenangebote--cached.html"
        new_url = "https://www.stepstone.de/stellenangebote--new.html"
        cached_job = self.make_job("Cached", cached_url)
        new_job = self.make_job("New", new_url)

        with tempfile.TemporaryDirectory() as directory:
            cache_path = Path(directory) / "stepstone.json"
            cache_path.write_text(
                json.dumps(
                    {
                        "version": stepstone.CACHE_VERSION,
                        "last_links": [cached_url],
                        "jobs": {cached_url: cached_job},
                    }
                ),
                encoding="utf-8",
            )

            with (
                patch.object(
                    stepstone,
                    "search_links",
                    return_value=[cached_url, new_url],
                ),
                patch.object(stepstone, "fetch_job", return_value=new_job) as fetch_job,
            ):
                jobs = stepstone.fetch_jobs(
                    cache_path=cache_path,
                    imported_jobs_path=Path(directory) / "missing.json",
                    client=Mock(),
                )

            self.assertEqual(jobs, [cached_job, new_job])
            fetch_job.assert_called_once_with(new_url, ANY)

    def test_blocked_search_returns_last_cached_result(self):
        url = "https://www.stepstone.de/stellenangebote--cached.html"
        job = self.make_job("Cached", url)

        with tempfile.TemporaryDirectory() as directory:
            cache_path = Path(directory) / "stepstone.json"
            cache_path.write_text(
                json.dumps(
                    {
                        "version": stepstone.CACHE_VERSION,
                        "last_links": [url],
                        "jobs": {url: job},
                    }
                ),
                encoding="utf-8",
            )

            with patch.object(
                stepstone,
                "search_links",
                side_effect=stepstone.StepStoneBlockedError(403, "search-url"),
            ):
                jobs = stepstone.fetch_jobs(
                    cache_path=cache_path,
                    imported_jobs_path=Path(directory) / "missing.json",
                    client=Mock(),
                )

        self.assertEqual(jobs, [job])

    def test_existing_imported_jobs_seed_an_empty_cache(self):
        url = "https://www.stepstone.de/stellenangebote--existing.html?tracking=1"
        job = self.make_job("Existing", url)

        with tempfile.TemporaryDirectory() as directory:
            imported_path = Path(directory) / "jobs_imported.json"
            imported_path.write_text(json.dumps([job]), encoding="utf-8")
            cache = {
                "version": stepstone.CACHE_VERSION,
                "last_links": [],
                "jobs": {},
            }

            added = stepstone.seed_cache_from_imported_jobs(cache, imported_path)

        self.assertEqual(added, 1)
        self.assertIn(stepstone.normalize_detail_url(url), cache["jobs"])

    def test_detail_block_stops_new_requests_but_keeps_later_cached_jobs(self):
        blocked_url = "https://www.stepstone.de/stellenangebote--blocked.html"
        cached_url = "https://www.stepstone.de/stellenangebote--cached.html"
        uncached_url = "https://www.stepstone.de/stellenangebote--uncached.html"
        cached_job = self.make_job("Cached", cached_url)

        with tempfile.TemporaryDirectory() as directory:
            cache_path = Path(directory) / "stepstone.json"
            cache_path.write_text(
                json.dumps(
                    {
                        "version": stepstone.CACHE_VERSION,
                        "last_links": [],
                        "jobs": {cached_url: cached_job},
                    }
                ),
                encoding="utf-8",
            )

            links = [blocked_url, cached_url, uncached_url]
            with (
                patch.object(stepstone, "search_links", return_value=links),
                patch.object(
                    stepstone,
                    "fetch_job",
                    side_effect=stepstone.StepStoneBlockedError(429, blocked_url),
                ) as fetch_job,
            ):
                jobs = stepstone.fetch_jobs(
                    cache_path=cache_path,
                    imported_jobs_path=Path(directory) / "missing.json",
                    client=Mock(),
                )

        self.assertEqual(jobs, [cached_job])
        fetch_job.assert_called_once_with(blocked_url, ANY)

    @staticmethod
    def make_job(title, url):
        return {
            "title": title,
            "company": "Example GmbH",
            "location": "Remote",
            "remote": "100%",
            "description": "Python",
            "url": url,
            "external_url": url,
            "source": "stepstone",
        }


class StepStoneHttpClientTests(unittest.TestCase):
    def test_waits_between_requests(self):
        sleeper = Mock()
        client = stepstone.StepStoneHttpClient(delay=1.5, sleeper=sleeper)

        with patch.object(stepstone, "fetch_text", side_effect=["first", "second"]):
            client.get("https://example.test/one")
            client.get("https://example.test/two")

        sleeper.assert_called_once_with(1.5)

    def test_raises_dedicated_error_for_access_limits(self):
        client = stepstone.StepStoneHttpClient(delay=0, sleeper=Mock())
        error = HTTPError("https://example.test", 429, "limited", {}, None)

        with (
            patch.object(stepstone, "fetch_text", side_effect=error),
            self.assertRaises(stepstone.StepStoneBlockedError),
        ):
            client.get("https://example.test")


if __name__ == "__main__":
    unittest.main()
