"""Coverage of API and paginated sources: a bad record or a failed search costs only itself."""

import io
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from job_finder.http import HttpStatusError
from job_finder.sources import arbeitsagentur, company_careers, edag, himalayas, jobicy, startup_jobs
from job_finder.sources.common import collecting_diagnostics
from job_finder.sources.company_careers import MAX_LIST_PAGES, PaginatedCareerPage


def himalayas_record(guid, **fields):
    return {
        "guid": f"https://himalayas.app/companies/example/jobs/{guid}",
        "title": "Junior Developer",
        "companyName": "Example GmbH",
        **fields,
    }


def run(function, *args, **kwargs):
    """Call a source function quietly and return its result with the coverage it recorded."""
    with collecting_diagnostics() as diagnostics, redirect_stdout(io.StringIO()):
        result = function(*args, **kwargs)
    return result, diagnostics


class RecordIsolationTests(unittest.TestCase):
    def test_a_malformed_record_is_skipped_and_makes_the_source_partial(self):
        records = [himalayas_record("one"), himalayas_record("broken", companyName=""), "not a record"]

        with patch.object(himalayas, "collect_records", return_value=records):
            jobs, diagnostics = run(himalayas.fetch_jobs)

        self.assertEqual(len(jobs), 1)
        self.assertGreaterEqual(diagnostics.failed_segments, 1)

    def test_a_source_without_any_valid_record_still_fails(self):
        with (
            patch.object(himalayas, "collect_records", return_value=[himalayas_record("broken", title="")]),
            self.assertRaises(ValueError),
        ):
            run(himalayas.fetch_jobs)

    def test_left_out_records_are_no_error(self):
        records = [{"id": 1, "jobGeo": "USA only"}]

        with patch.object(jobicy, "collect_records", return_value=records):
            jobs, diagnostics = run(jobicy.fetch_jobs)

        self.assertEqual((jobs, diagnostics.failed_segments), ([], 0))


class SegmentTests(unittest.TestCase):
    def test_a_failed_search_keeps_the_results_of_the_others(self):
        pages = [
            {"offset": 0, "limit": 1, "totalCount": 1, "jobs": [{"guid": "one"}]},
            HttpStatusError(500, himalayas.API_URL),
            {"offset": 0, "limit": 1, "totalCount": 1, "jobs": [{"guid": "three"}]},
        ]

        with patch.object(himalayas, "fetch_json", side_effect=pages), patch.object(himalayas.time, "sleep"):
            records, diagnostics = run(himalayas.collect_records, ["software", "data", "support"])

        self.assertEqual([record["guid"] for record in records], ["one", "three"])
        self.assertEqual((diagnostics.failed_segments, diagnostics.total_segments), (1, 3))

    def test_a_block_stops_the_remaining_searches(self):
        pages = [{"jobs": [{"id": 1}]}, HttpStatusError(429, jobicy.API_URL)]

        with patch.object(jobicy, "fetch_json", side_effect=pages) as fetch, patch.object(jobicy.time, "sleep"):
            records, diagnostics = run(
                jobicy.collect_records, [{"geo": "germany"}, {"industry": "a"}, {"industry": "b"}]
            )

        self.assertEqual(len(records), 1)
        self.assertEqual(fetch.call_count, 2)
        self.assertEqual((diagnostics.failed_segments, diagnostics.total_segments), (2, 3))

    def test_every_search_failing_fails_the_source(self):
        with (
            patch.object(startup_jobs, "fetch_json", side_effect=TimeoutError("timeout")),
            self.assertRaises(TimeoutError),
        ):
            run(startup_jobs.collect_records, {}, [{"role": "engineering"}, {"role": "data"}])

    def test_arbeitsagentur_keeps_the_links_of_searches_that_worked(self):
        def search(term, location=None, radius=None):
            if term == "broken":
                raise HttpStatusError(503, "https://example.test/search")
            return [{"referenznummer": f"{term}-1"}]

        with (
            patch.object(arbeitsagentur, "search_terms", return_value=["first", "broken"]),
            patch.object(arbeitsagentur, "commuter_search_locations", return_value=[]),
            patch.object(arbeitsagentur, "search", side_effect=search),
        ):
            links, diagnostics = run(arbeitsagentur.collect_links)

        self.assertEqual(links, [f"{arbeitsagentur.DETAIL_BASE_URL}/first-1"])
        self.assertEqual((diagnostics.failed_segments, diagnostics.total_segments), (1, 2))

    def test_an_endless_arbeitsagentur_search_stops_at_its_page_limit(self):
        pages = (
            f'<script id="ng-state" type="application/json">{{"suchergebnis": {{"maxErgebnisse": 99999, '
            f'"ergebnisliste": [{{"referenznummer": "ref-{page}"}}]}}}}</script>'
            for page in range(1, 1000)
        )

        with patch.object(arbeitsagentur, "fetch_text", side_effect=pages) as fetch:
            results, diagnostics = run(arbeitsagentur.search, "Junior IT")

        self.assertEqual(len(results), arbeitsagentur.MAX_SEARCH_PAGES)
        self.assertEqual(fetch.call_count, arbeitsagentur.MAX_SEARCH_PAGES)
        self.assertEqual(diagnostics.failed_segments, 1)


class PageLimitTests(unittest.TestCase):
    def test_a_career_page_announcing_too_many_pages_is_read_only_up_to_the_limit(self):
        page = PaginatedCareerPage(
            "example", "Example GmbH", "https://example.test/jobs/", r"example\.test/jobs/(?!page/)[^/]+/$"
        )
        first = '<a href="/jobs/one/">1</a><a href="/jobs/page/5000/">5000</a>'

        def fetch_text(url):
            return first if url == "https://example.test/jobs/" else ""

        with patch.object(company_careers, "fetch_text", side_effect=fetch_text) as fetch:
            links, diagnostics = run(page.collect_links)

        self.assertEqual(links, ["https://example.test/jobs/one/"])
        self.assertEqual(fetch.call_count, MAX_LIST_PAGES)
        self.assertEqual(diagnostics.failed_segments, 1)

    def test_a_failed_later_page_keeps_the_links_of_the_first(self):
        first = '<a href="https://www.edag.com/de/karriere/stellenanzeigen?currentPage]=2">2</a>'
        first += '<a class="sfjob" href="/de/karriere/stellenanzeigen/detail/junior-fulda-1">Junior Fulda</a>'

        with patch.object(edag, "fetch_text", side_effect=[first, HttpStatusError(500, edag.LIST_URL)]):
            links, diagnostics = run(edag.collect_links)

        self.assertEqual(links, ["https://www.edag.com/de/karriere/stellenanzeigen/detail/junior-fulda-1"])
        self.assertEqual((diagnostics.failed_segments, diagnostics.total_segments), (1, 2))
