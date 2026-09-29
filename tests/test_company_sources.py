"""Tests for direct company career-page source adapters."""

import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from job_finder.models import Job, JobSource, WorkMode
from job_finder.sources import company_careers, compose_it, edag, jumo
from job_finder.sources.common import canonical_detail_url, load_detail_cache, save_detail_cache
from job_finder.sources.company_careers import (
    BYTEWERK,
    CSS,
    NETHINKS,
    PROEMION,
    RHOENENERGIE,
    CareerPage,
    PaginatedCareerPage,
    fetch_company_jobs,
)

POSTING_HTML = """<script type="application/ld+json">
{"@type": "JobPosting", "title": "Junior Developer", "description": "<p>Python</p>",
 "jobLocation": {"address": {"addressLocality": "Fulda"}}}
</script>"""


class CareerPageFetchTests(unittest.TestCase):
    def fetch(self, page, pages):
        """Run fetch_jobs with fake pages; return the jobs and the fetched URLs in order."""
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.object(company_careers, "fetch_text", side_effect=pages.__getitem__) as fetch,
        ):
            jobs = page.fetch_jobs(cache_path=Path(directory) / "cache.json")
        return jobs, [call.args[0] for call in fetch.call_args_list]

    def test_career_page_imports_each_matching_detail_page_once(self):
        page = CareerPage(
            "example", "Example GmbH", "https://jobs.example.test/", r"example\.test/job/\d+$"
        )
        jobs, fetched = self.fetch(
            page,
            {
                "https://jobs.example.test/": '<a href="/job/1">A</a><a href="/job/1">A</a>'
                '<a href="/about">About</a>',
                "https://jobs.example.test/job/1": POSTING_HTML,
            },
        )

        self.assertEqual(
            [(job.id, job.title, job.company, job.locations) for job in jobs],
            [("example:1", "Junior Developer", "Example GmbH", ["Fulda"])],
        )
        self.assertEqual(fetched, ["https://jobs.example.test/", "https://jobs.example.test/job/1"])

    def test_paginated_career_page_reads_every_announced_page(self):
        page = PaginatedCareerPage(
            "example",
            "Example GmbH",
            "https://example.test/jobs/",
            r"example\.test/jobs/(?!page/)[^/]+/$",
        )
        jobs, fetched = self.fetch(
            page,
            {
                "https://example.test/jobs/": '<a href="/jobs/one/">1</a><a href="/jobs/page/2/">2</a>',
                "https://example.test/jobs/page/2/": '<a href="/jobs/two/">2</a><a href="/jobs/one/">1</a>',
                "https://example.test/jobs/one/": POSTING_HTML,
                "https://example.test/jobs/two/": POSTING_HTML,
            },
        )

        self.assertEqual([job.id for job in jobs], ["example:one", "example:two"])
        self.assertEqual(
            fetched,
            [
                "https://example.test/jobs/",
                "https://example.test/jobs/page/2/",
                "https://example.test/jobs/one/",
                "https://example.test/jobs/two/",
            ],
        )


class SourceNameTests(unittest.TestCase):
    def test_source_names_stay_stable(self):
        sources = (CSS, PROEMION, BYTEWERK, RHOENENERGIE, NETHINKS, edag, compose_it.COMPOSE_IT)
        self.assertEqual(
            [source.SOURCE_NAME for source in sources],
            ["css", "proemion", "bytewerk", "rhoenenergie", "nethinks", "edag", "compose_it"],
        )


class ComposeItSourceTests(unittest.TestCase):
    def test_collect_links_keeps_only_compose_job_pages(self):
        html = """
        <a href="https://compose-it.de/job/it-supporter/">Support</a>
        <a href="/job/it-systemadministrator/">Admin</a>
        <a href="https://compose-it.de/unternehmen/karriere/">Karriere</a>
        """

        with patch.object(company_careers, "fetch_text", return_value=html):
            links = compose_it.COMPOSE_IT.collect_links()

        self.assertEqual(
            links,
            [
                "https://compose-it.de/job/it-supporter/",
                "https://compose-it.de/job/it-systemadministrator/",
            ],
        )

    def test_job_from_html_extracts_visible_job_content(self):
        html = """
        <span class="elementor-icon-list-text">Festanstellung</span>
        <span class="elementor-icon-list-text">Fulda (Stadtmitte)</span>
        <h1>IT-Supporter (m/w/d)</h1>
        <div data-elementor-type="wp-post" data-elementor-id="3364">
          <h2>Was du mitbringen solltest</h2>
          <p>Abgeschlossene IT-Ausbildung. Hybrid (Büro / HomeOffice).</p>
        </div>
        <div id="bewerberform">Bewerbungsformular mit irrelevanten Feldern</div>
        """

        job = compose_it.job_from_html(
            "compose_it", "COMPOSE IT", "https://compose-it.de/job/it-supporter/", html
        )

        self.assertEqual(job.id, "compose_it:it-supporter")
        self.assertEqual(job.title, "IT-Supporter (m/w/d)")
        self.assertEqual(job.company, "COMPOSE IT")
        self.assertEqual(job.locations, ["Fulda (Stadtmitte)"])
        self.assertEqual(job.employment_type, "Festanstellung")
        self.assertEqual(job.work_mode, WorkMode.HYBRID)
        self.assertIn("Abgeschlossene IT-Ausbildung", job.description_clean)
        self.assertNotIn("irrelevanten Feldern", job.description_clean)


class BytewerkSourceTests(unittest.TestCase):
    def test_collect_links_keeps_only_bytewerk_job_pages(self):
        html = """
        <a href="/job/1249333?language=de">IT Consultant</a>
        <a href="/">Startseite</a>
        <a href="https://other.jobs.personio.de/job/123">Andere Firma</a>
        """

        with patch.object(company_careers, "fetch_text", return_value=html):
            links = BYTEWERK.collect_links()

        self.assertEqual(links, ["https://bytewerk-gmbh.jobs.personio.de/job/1249333"])


class RhoenenergieSourceTests(unittest.TestCase):
    def test_collect_links_keeps_only_current_job_details(self):
        html = """
        <a href="/karriere/IT-Fachadministrator-mwd-de-j1077.html">IT</a>
        <a href="https://re-gruppe.de/karriere/Busfahrer-mwd-de-j121.html">Bus</a>
        <a href="/karriere/">Karriere</a>
        <a href="https://other.test/karriere/Developer-de-j999.html">Andere</a>
        """

        with patch.object(company_careers, "fetch_text", return_value=html):
            links = RHOENENERGIE.collect_links()

        self.assertEqual(
            links,
            [
                "https://re-gruppe.de/karriere/IT-Fachadministrator-mwd-de-j1077.html",
                "https://re-gruppe.de/karriere/Busfahrer-mwd-de-j121.html",
            ],
        )


class CompanyCareerTests(unittest.TestCase):
    def test_cached_company_jobs_keep_unique_url_identity_and_age_limits(self):
        now = datetime(2026, 9, 9, tzinfo=UTC)
        url = "https://example.test/job/developer-12345"
        for age, expected_count in [(1, 1), (8, 1), (15, 0)]:
            with self.subTest(age=age), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "cache.json"
                job = Job(
                    id="example:generic",
                    title="Developer",
                    company="Example",
                    locations=["Fulda"],
                    description_raw="IT",
                    description_clean="IT",
                    sources=[JobSource(source="example", source_id="generic", url=url)],
                    fetched_at=now - timedelta(days=age),
                )
                save_detail_cache(path, {url: job})
                with patch(
                    "job_finder.sources.company_careers.fetch_text", side_effect=OSError("offline")
                ) as fetch:
                    jobs = fetch_company_jobs("example", "Example", [url], path, now=now)
                self.assertEqual(len(jobs), expected_count)
                self.assertEqual(fetch.called, age >= 7)
                if jobs:
                    self.assertEqual(jobs[0].id, "example:12345")
                    self.assertEqual(jobs[0].sources[0].source_id, "12345")
                    self.assertEqual(jobs[0].cache_stale, age >= 7)
                    self.assertEqual(load_detail_cache(path)[url].id, "example:12345")

    def test_jumo_job_ids_are_unique(self):
        html = """
            onclick="showJobOfferDetail.do?jobOfferId=abc12345&amp;j=jobexchange"
            onclick="showJobOfferDetail.do?jobOfferId=def67890&amp;j=jobexchange"
            onclick="showJobOfferDetail.do?jobOfferId=abc12345&amp;j=jobexchange"
        """

        self.assertEqual(jumo.extract_job_ids(html), ["abc12345", "def67890"])

    def test_edag_list_keeps_fulda_and_multiple_locations(self):
        html = """
            <a class="sfjob" href="/de/karriere/stellenanzeigen/detail/local-12345">
              <div class="sfjob-location">Fulda</div>
            </a>
            <a class="sfjob" href="/de/karriere/stellenanzeigen/detail/multi-23456">
              <div class="sfjob-location">Mehrere Standorte verfügbar</div>
            </a>
            <a class="sfjob" href="/de/karriere/stellenanzeigen/detail/other-34567">
              <div class="sfjob-location">München</div>
            </a>
        """

        links = edag.extract_local_links(html)

        self.assertEqual(len(links), 2)
        self.assertTrue(links[0].endswith("local-12345"))
        self.assertTrue(links[1].endswith("multi-23456"))

    def test_jumo_cache_key_keeps_job_offer_id(self):
        first = canonical_detail_url(
            "https://jobs.jumo.de/engage/jobexchange/showJobOfferDetail.do?"
            "jobOfferId=first&j=jobexchange"
        )
        second = canonical_detail_url(
            "https://jobs.jumo.de/engage/jobexchange/showJobOfferDetail.do?"
            "jobOfferId=second&j=jobexchange"
        )

        self.assertNotEqual(first, second)
        self.assertEqual(first, first.split("&")[0])
