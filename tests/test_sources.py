"""Tests for search planning and shared job-board infrastructure."""

import json
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import ANY, Mock, patch
from urllib.error import HTTPError

from job_finder.matching.config import (
    COMMUTER_SEARCH_RADIUS_KM,
    local_search_postal_code,
    search_terms,
    stepstone_search_locations,
    stepstone_search_terms,
)
from job_finder.models import Job, JobSource, WorkMode
from job_finder.sources import arbeitsagentur, get_in_it, stepstone
from job_finder.sources.common import save_detail_cache
from job_finder.sources.stepstone import build_search_url


class CommuterSearchTests(unittest.TestCase):
    def test_arbeitsagentur_search_can_target_one_commuter_city(self):
        url = arbeitsagentur.build_search_url("Junior IT", location="Beispielstadt", radius=COMMUTER_SEARCH_RADIUS_KM)

        self.assertIn("wo=Beispielstadt", url)
        self.assertIn(f"umkreis={COMMUTER_SEARCH_RADIUS_KM}", url)

    def test_get_in_it_uses_reduced_terms_for_commuter_cities(self):
        with (
            patch.object(get_in_it, "search_terms", return_value=[]),
            patch.object(get_in_it, "search_locations", return_value=[]),
            patch.object(get_in_it, "commuter_search_terms", return_value=["Junior Developer"]),
            patch.object(get_in_it, "commuter_search_locations", return_value=["Beispielstadt"]),
        ):
            searches = list(get_in_it.build_api_searches())

        self.assertTrue(searches)
        self.assertTrue(all(item["location"] == "Beispielstadt" for item in searches))


class StepStoneSearchTests(unittest.TestCase):
    def test_search_plan_has_unique_roles_and_local_remote_scopes(self):
        terms, locations = stepstone_search_terms(), stepstone_search_locations()
        self.assertEqual(len(terms), len(set(terms)))
        self.assertIn("Remote", locations)
        self.assertIn(local_search_postal_code(), locations)
        for role in ("Data Analyst", "DevOps Engineer", "Software Test Engineer"):
            self.assertIn(role, terms)

    def test_search_urls_carry_no_query_parameters(self):
        # robots.txt disallows search URLs with parameters such as page or radius.
        self.assertEqual(
            build_search_url("Python Developer", local_search_postal_code()),
            f"https://www.stepstone.de/jobs/Python-Developer/in-{local_search_postal_code()}",
        )
        self.assertEqual(
            build_search_url("Python Developer", "Remote"), "https://www.stepstone.de/jobs/Python-Developer/in-Remote"
        )

    def test_default_terms_are_the_general_search_terms(self):
        self.assertEqual(stepstone_search_terms(), search_terms())


class StepStoneSearchPageTests(unittest.TestCase):
    def test_search_reads_only_the_first_page_of_each_query(self):
        first = "https://www.stepstone.de/stellenangebote--first.html"
        second = "https://www.stepstone.de/stellenangebote--second.html"
        client = Mock()
        client.get.side_effect = [f'<a href="{first}">Erste</a>', f'<a href="{second}">Zweite</a>']

        with (
            patch.object(stepstone, "stepstone_search_terms", return_value=["Python", "Data"]),
            patch.object(stepstone, "stepstone_search_locations", return_value=["Remote"]),
        ):
            links = stepstone.search_links(client)

        self.assertEqual(links, [first, second])
        self.assertEqual(client.get.call_count, 2)
        self.assertTrue(all("?" not in call.args[0] for call in client.get.call_args_list))

    def test_search_progress_replaces_stop_reason_summary(self):
        url = "https://www.stepstone.de/stellenangebote--same.html"
        client = Mock()
        client.get.side_effect = [f'<a href="{url}">Stelle</a>']

        with (
            patch.object(stepstone, "stepstone_search_terms", return_value=["Python"]),
            patch.object(stepstone, "stepstone_search_locations", return_value=["Remote"]),
            patch("builtins.print") as print_output,
        ):
            links = stepstone.search_links(client)

        self.assertEqual(links, [url])
        output = print_output.call_args.args[0]
        self.assertIn("StepStone Suche:", output)
        self.assertNotIn("1/1", output)
        self.assertIn("1 Seiten", output)
        self.assertIn("1 Anzeigen", output)
        self.assertNotIn("Stopps:", output)


class StepStoneCacheTests(unittest.TestCase):
    def test_saved_cache_contains_only_reusable_source_fields(self):
        url = "https://www.stepstone.de/stellenangebote--cached.html"
        job = self.make_job("Cached", url)

        with tempfile.TemporaryDirectory() as directory:
            cache_path = Path(directory) / "stepstone.json"
            stepstone.save_cache(
                cache_path, {"version": stepstone.CACHE_VERSION, "last_links": [url], "jobs": {url: job}}
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
            self.write_cache(cache_path, [cached_url], {cached_url: cached_job})

            with (
                patch.object(stepstone, "search_links", return_value=[cached_url, new_url]),
                patch.object(stepstone, "fetch_job", return_value=new_job) as fetch_job,
            ):
                jobs = stepstone.fetch_jobs(cache_path=cache_path, client=Mock())

            self.assertEqual(jobs, [cached_job, new_job])
            fetch_job.assert_called_once_with(new_url, ANY)

    def test_blocked_search_returns_last_cached_result(self):
        url = "https://www.stepstone.de/stellenangebote--cached.html"
        job = self.make_job("Cached", url)

        with tempfile.TemporaryDirectory() as directory:
            cache_path = Path(directory) / "stepstone.json"
            self.write_cache(cache_path, [url], {url: job})

            with patch.object(
                stepstone, "search_links", side_effect=stepstone.StepStoneBlockedError(403, "search-url")
            ):
                jobs = stepstone.fetch_jobs(cache_path=cache_path, client=Mock())

        self.assertEqual(jobs, [job])

    def test_detail_block_stops_new_requests_but_keeps_later_cached_jobs(self):
        blocked_url = "https://www.stepstone.de/stellenangebote--blocked.html"
        cached_url = "https://www.stepstone.de/stellenangebote--cached.html"
        uncached_url = "https://www.stepstone.de/stellenangebote--uncached.html"
        cached_job = self.make_job("Cached", cached_url)

        with tempfile.TemporaryDirectory() as directory:
            cache_path = Path(directory) / "stepstone.json"
            self.write_cache(cache_path, [], {cached_url: cached_job})

            links = [blocked_url, cached_url, uncached_url]
            with (
                patch.object(stepstone, "search_links", return_value=links),
                patch.object(
                    stepstone, "fetch_job", side_effect=stepstone.StepStoneBlockedError(429, blocked_url)
                ) as fetch_job,
            ):
                jobs = stepstone.fetch_jobs(cache_path=cache_path, client=Mock())

        self.assertEqual(jobs, [cached_job])
        fetch_job.assert_called_once_with(blocked_url, ANY)

    def test_stale_cached_detail_is_refreshed(self):
        url = "https://www.stepstone.de/stellenangebote--cached.html"
        cached_job = self.make_job("Cached", url)
        cached_job.fetched_at = datetime.now(UTC) - timedelta(days=8)
        refreshed_job = self.make_job("Changed", url)

        with tempfile.TemporaryDirectory() as directory:
            cache_path = Path(directory) / "stepstone.json"
            self.write_cache(cache_path, [url], {url: cached_job})
            with (
                patch.object(stepstone, "search_links", return_value=[url]),
                patch.object(stepstone, "fetch_job", return_value=refreshed_job) as fetch_job,
            ):
                jobs = stepstone.fetch_jobs(cache_path=cache_path, client=Mock())

        self.assertEqual(jobs, [refreshed_job])
        fetch_job.assert_called_once_with(url, ANY)

    @staticmethod
    def write_cache(path, last_links, jobs):
        """Write the raw cache file a previous run left, independent of the production writer."""
        jobs = {url: job.to_dict() for url, job in jobs.items()}
        document = {"version": stepstone.CACHE_VERSION, "last_links": last_links, "jobs": jobs}
        path.write_text(json.dumps(document), encoding="utf-8")

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
            fetched_at=datetime.now(UTC),
        )


class SharedDetailCacheTests(unittest.TestCase):
    def test_saved_cache_contains_only_reusable_source_fields(self):
        now = datetime(2026, 7, 17, 12, tzinfo=UTC)
        url = "https://www.get-in-it.de/jobsuche/p1"
        job = self.make_job(get_in_it.SOURCE_NAME, url, now)

        with tempfile.TemporaryDirectory() as directory:
            cache_path = Path(directory) / "details.json"
            save_detail_cache(cache_path, {url: job})
            saved_job = json.loads(cache_path.read_text(encoding="utf-8"))["jobs"][url]

        self.assertIn("fetched_at", saved_job)
        self.assertNotIn("llm_score", saved_job)
        self.assertNotIn("first_seen_at", saved_job)

    def test_fresh_details_are_reused_by_arbeitsagentur(self):
        now = datetime(2026, 7, 17, 12, tzinfo=UTC)
        sources = [(arbeitsagentur, "https://example.test/arbeitsagentur/1")]

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
                fetch_job.assert_not_called()

    def test_stale_detail_is_downloaded(self):
        now = datetime(2026, 7, 17, 12, tzinfo=UTC)
        url = "https://www.get-in-it.de/jobsuche/p1"
        cached_job = self.make_job(get_in_it.SOURCE_NAME, url, now - timedelta(days=8))
        refreshed_job = self.make_job(get_in_it.SOURCE_NAME, url, now)
        refreshed_job.description_clean = "Python und neue Cloud-Aufgaben"

        with tempfile.TemporaryDirectory() as directory:
            cache_path = Path(directory) / "details.json"
            save_detail_cache(cache_path, {url: cached_job})
            with (
                patch.object(
                    get_in_it,
                    "collect_records",
                    return_value=[
                        {
                            "id": 1,
                            "title": "Python Developer",
                            "url": "/jobsuche/p1",
                            "homeOffice": True,
                            "locations": [{"name": "Remote"}],
                            "company": {"title": "Example GmbH"},
                        }
                    ],
                ),
                patch.object(get_in_it, "fetch_job", return_value=refreshed_job) as fetch_job,
            ):
                jobs = get_in_it.fetch_jobs(cache_path=cache_path, now=now)
                enriched = get_in_it.enrich_candidate_jobs(jobs, {jobs[0].id}, cache_path=cache_path, now=now)

        self.assertEqual(jobs, [refreshed_job])
        self.assertEqual(enriched, 1)
        fetch_job.assert_called_once_with("https://www.get-in-it.de/jobsuche/p1")

    def test_failed_refresh_falls_back_to_stale_detail(self):
        now = datetime(2026, 7, 17, 12, tzinfo=UTC)
        url = "https://example.test/arbeitsagentur/1"
        cached_job = self.make_job(arbeitsagentur.SOURCE_NAME, url, now - timedelta(days=8))

        with tempfile.TemporaryDirectory() as directory:
            cache_path = Path(directory) / "details.json"
            save_detail_cache(cache_path, {url: cached_job})
            with (
                patch.object(arbeitsagentur, "collect_links", return_value=[url]),
                patch.object(arbeitsagentur, "fetch_job", side_effect=RuntimeError("nicht erreichbar")),
            ):
                jobs = arbeitsagentur.fetch_jobs(cache_path=cache_path, now=now)

        self.assertEqual([job.id for job in jobs], [cached_job.id])
        self.assertTrue(jobs[0].cache_stale)

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
