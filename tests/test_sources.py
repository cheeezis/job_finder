"""Tests for search planning and shared job-board infrastructure."""

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import ANY, Mock, patch
from urllib.error import HTTPError

from job_finder.config import (
    COMMUTER_SEARCH_RADIUS_KM,
    LOCAL_SEARCH_POSTAL_CODE,
    STEPSTONE_SEARCH_RADIUS_KM,
    STEPSTONE_SEARCH_LOCATIONS,
    STEPSTONE_SEARCH_TERMS,
)
from job_finder.models import Job, JobSource, WorkMode
from job_finder.sources import (
    arbeitsagentur,
    get_in_it,
    stepstone,
)
from job_finder.sources.common import (
    save_detail_cache,
)
from job_finder.sources.stepstone import build_search_url


class CommuterSearchTests(unittest.TestCase):
    def test_arbeitsagentur_search_can_target_one_commuter_city(self):
        url = arbeitsagentur.build_search_url(
            "Junior IT",
            location="Beispielstadt",
            radius=COMMUTER_SEARCH_RADIUS_KM,
        )

        self.assertIn("wo=Beispielstadt", url)
        self.assertIn(f"umkreis={COMMUTER_SEARCH_RADIUS_KM}", url)

    def test_get_in_it_uses_reduced_terms_for_commuter_cities(self):
        with (
            patch.object(get_in_it, "GET_IN_IT_SEARCH_TERMS", []),
            patch.object(get_in_it, "GET_IN_IT_SEARCH_LOCATIONS", []),
            patch.object(get_in_it, "COMMUTER_SEARCH_TERMS", ["Junior Developer"]),
            patch.object(get_in_it, "COMMUTER_SEARCH_LOCATIONS", ["Beispielstadt"]),
        ):
            searches = list(get_in_it.build_api_searches())

        self.assertTrue(searches)
        self.assertTrue(
            all(item["location"] == "Beispielstadt" for item in searches)
        )

class StepStoneSearchTests(unittest.TestCase):
    def test_search_plan_uses_sixteen_role_families(self):
        self.assertEqual(len(STEPSTONE_SEARCH_TERMS), 16)
        self.assertEqual(
            len(STEPSTONE_SEARCH_TERMS) * len(STEPSTONE_SEARCH_LOCATIONS),
            32,
        )

    def test_local_search_uses_postcode_and_radius(self):
        url = build_search_url("Python Developer", LOCAL_SEARCH_POSTAL_CODE, page=2)

        self.assertEqual(
            url,
            "https://www.stepstone.de/jobs/Python-Developer/"
            f"in-{LOCAL_SEARCH_POSTAL_CODE}?page=2"
            f"&radius={STEPSTONE_SEARCH_RADIUS_KM}",
        )

    def test_remote_search_does_not_add_local_radius(self):
        url = build_search_url("Python Developer", "Remote")

        self.assertEqual(
            url,
            "https://www.stepstone.de/jobs/Python-Developer/in-Remote?page=1",
        )

class StepStonePaginationTests(unittest.TestCase):
    def test_search_reads_pages_until_stepstone_returns_no_links(self):
        first = "https://www.stepstone.de/stellenangebote--first.html"
        second = "https://www.stepstone.de/stellenangebote--second.html"
        client = Mock()
        client.get.side_effect = [
            f'<a href="{first}">Erste</a>',
            f'<a href="{second}">Zweite</a>',
            "<html>Keine weiteren Stellen</html>",
        ]

        with (
            patch.object(stepstone, "STEPSTONE_SEARCH_TERMS", ["Python"]),
            patch.object(stepstone, "STEPSTONE_SEARCH_LOCATIONS", ["Remote"]),
        ):
            links = stepstone.search_links(client)

        self.assertEqual(links, [first, second])
        self.assertEqual(client.get.call_count, 3)
        self.assertIn("page=3", client.get.call_args.args[0])

    def test_search_reports_repeated_page_as_stop_reason(self):
        url = "https://www.stepstone.de/stellenangebote--same.html"
        client = Mock()
        client.get.side_effect = [
            f'<a href="{url}">Stelle</a>',
            f'<a href="{url}">Stelle</a>',
        ]

        with (
            patch.object(stepstone, "STEPSTONE_SEARCH_TERMS", ["Python"]),
            patch.object(stepstone, "STEPSTONE_SEARCH_LOCATIONS", ["Remote"]),
            patch("builtins.print") as print_output,
        ):
            links = stepstone.search_links(client)

        self.assertEqual(links, [url])
        self.assertIn("1 Wiederholung", print_output.call_args.args[0])

class StepStoneCacheTests(unittest.TestCase):
    def test_saved_cache_contains_only_reusable_source_fields(self):
        url = "https://www.stepstone.de/stellenangebote--cached.html"
        job = self.make_job("Cached", url)

        with tempfile.TemporaryDirectory() as directory:
            cache_path = Path(directory) / "stepstone.json"
            stepstone.save_cache(
                cache_path,
                {
                    "version": stepstone.CACHE_VERSION,
                    "last_links": [url],
                    "jobs": {url: job},
                },
            )
            saved_job = json.loads(cache_path.read_text(encoding="utf-8"))["jobs"][url]

        self.assertIn("description_clean", saved_job)
        self.assertNotIn("rule_score", saved_job)
        self.assertNotIn("workflow_status", saved_job)

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
                        "jobs": {cached_url: cached_job.to_dict()},
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
                        "jobs": {url: job.to_dict()},
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
                    client=Mock(),
                )

        self.assertEqual(jobs, [job])

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
                        "jobs": {cached_url: cached_job.to_dict()},
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
                    client=Mock(),
                )

        self.assertEqual(jobs, [cached_job])
        fetch_job.assert_called_once_with(blocked_url, ANY)

    def test_stale_cached_detail_is_refreshed_and_marked_changed(self):
        url = "https://www.stepstone.de/stellenangebote--cached.html"
        cached_job = self.make_job("Cached", url)
        cached_job.fetched_at = datetime.now(timezone.utc) - timedelta(days=8)
        refreshed_job = self.make_job("Changed", url)

        with tempfile.TemporaryDirectory() as directory:
            cache_path = Path(directory) / "stepstone.json"
            cache_path.write_text(
                json.dumps(
                    {
                        "version": stepstone.CACHE_VERSION,
                        "last_links": [url],
                        "jobs": {url: cached_job.to_dict()},
                    }
                ),
                encoding="utf-8",
            )
            with (
                patch.object(stepstone, "search_links", return_value=[url]),
                patch.object(
                    stepstone,
                    "fetch_job",
                    return_value=refreshed_job,
                ) as fetch_job,
            ):
                jobs = stepstone.fetch_jobs(
                    cache_path=cache_path,
                    client=Mock(),
                )

        self.assertEqual(jobs, [refreshed_job])
        self.assertTrue(jobs[0].content_changed)
        fetch_job.assert_called_once_with(url, ANY)

    @staticmethod
    def make_job(title, url):
        return Job(
            id=f"stepstone:{title.lower()}",
            title=title,
            company="Example GmbH",
            locations=["Remote"],
            sources=[JobSource(source="stepstone", url=url)],
            description_raw="Python",
            description_clean="Python",
            work_mode=WorkMode.REMOTE,
            remote_percentage=100,
            fetched_at=datetime.now(timezone.utc),
        )

class SharedDetailCacheTests(unittest.TestCase):
    def test_saved_cache_contains_only_reusable_source_fields(self):
        now = datetime(2026, 7, 17, 12, tzinfo=timezone.utc)
        url = "https://example.test/get-in-it/1"
        job = self.make_job(get_in_it.SOURCE_NAME, url, now)

        with tempfile.TemporaryDirectory() as directory:
            cache_path = Path(directory) / "details.json"
            save_detail_cache(cache_path, {url: job})
            saved_job = json.loads(cache_path.read_text(encoding="utf-8"))["jobs"][url]

        self.assertIn("fetched_at", saved_job)
        self.assertNotIn("llm_score", saved_job)
        self.assertNotIn("first_seen_at", saved_job)

    def test_fresh_details_are_reused_by_arbeitsagentur_and_get_in_it(self):
        now = datetime(2026, 7, 17, 12, tzinfo=timezone.utc)
        sources = [
            (arbeitsagentur, "https://example.test/arbeitsagentur/1"),
            (get_in_it, "https://example.test/get-in-it/1"),
        ]

        for source, url in sources:
            with self.subTest(source=source.SOURCE_NAME):
                cached_job = self.make_job(source.SOURCE_NAME, url, now)
                with tempfile.TemporaryDirectory() as directory:
                    cache_path = Path(directory) / "details.json"
                    save_detail_cache(cache_path, {url: cached_job})
                    with (
                        patch.object(source, "collect_links", return_value=[url]),
                        patch.object(source, "fetch_job") as fetch_job,
                    ):
                        jobs = source.fetch_jobs(cache_path=cache_path, now=now)

                self.assertEqual(jobs, [cached_job])
                self.assertFalse(jobs[0].content_changed)
                fetch_job.assert_not_called()

    def test_stale_changed_detail_is_downloaded_and_marked(self):
        now = datetime(2026, 7, 17, 12, tzinfo=timezone.utc)
        url = "https://example.test/get-in-it/1"
        cached_job = self.make_job(
            get_in_it.SOURCE_NAME,
            url,
            now - timedelta(days=8),
        )
        refreshed_job = self.make_job(get_in_it.SOURCE_NAME, url, now)
        refreshed_job.description_clean = "Python und neue Cloud-Aufgaben"

        with tempfile.TemporaryDirectory() as directory:
            cache_path = Path(directory) / "details.json"
            save_detail_cache(cache_path, {url: cached_job})
            with (
                patch.object(get_in_it, "collect_links", return_value=[url]),
                patch.object(
                    get_in_it,
                    "fetch_job",
                    return_value=refreshed_job,
                ) as fetch_job,
            ):
                jobs = get_in_it.fetch_jobs(cache_path=cache_path, now=now)

        self.assertEqual(jobs, [refreshed_job])
        self.assertTrue(jobs[0].content_changed)
        fetch_job.assert_called_once_with(url)

    def test_failed_refresh_falls_back_to_stale_detail(self):
        now = datetime(2026, 7, 17, 12, tzinfo=timezone.utc)
        url = "https://example.test/arbeitsagentur/1"
        cached_job = self.make_job(
            arbeitsagentur.SOURCE_NAME,
            url,
            now - timedelta(days=8),
        )

        with tempfile.TemporaryDirectory() as directory:
            cache_path = Path(directory) / "details.json"
            save_detail_cache(cache_path, {url: cached_job})
            with (
                patch.object(arbeitsagentur, "collect_links", return_value=[url]),
                patch.object(
                    arbeitsagentur,
                    "fetch_job",
                    side_effect=RuntimeError("nicht erreichbar"),
                ),
            ):
                jobs = arbeitsagentur.fetch_jobs(cache_path=cache_path, now=now)

        self.assertEqual(jobs, [cached_job])
        self.assertFalse(jobs[0].content_changed)

    @staticmethod
    def make_job(source, url, fetched_at):
        return Job(
            id=f"{source}:1",
            title="Python Developer",
            company="Example GmbH",
            locations=["Remote"],
            sources=[JobSource(source=source, url=url)],
            description_raw="Python",
            description_clean="Python",
            work_mode=WorkMode.REMOTE,
            remote_percentage=100,
            fetched_at=fetched_at,
        )

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
