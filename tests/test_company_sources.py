"""Tests for direct company career-page source adapters."""

import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qsl

from job_finder.models import Job, JobSource, WorkMode
from job_finder.sources import (
    bytewerk,
    compose_it,
    css,
    edag,
    jumo,
    nethinks,
    proemion,
    rhoenenergie,
)
from job_finder.sources.common import canonical_detail_url, load_detail_cache, save_detail_cache
from job_finder.sources.company_careers import fetch_company_jobs


class FakeJumoSession:
    """Answer JUMO's session requests in order and record what was sent."""

    def __init__(self, answers):
        self.answers = list(answers)
        self.requests = []

    def open(self, request, timeout):
        form = dict(parse_qsl(request.data.decode())) if request.data else None
        self.requests.append((request.full_url, form, dict(request.header_items()), timeout))
        return FakeResponse(self.answers.pop(0))


class FakeResponse:
    def __init__(self, text):
        self.text = text

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return self.text.encode()


class JumoSessionTests(unittest.TestCase):
    def test_collect_links_follows_csrf_session_batches_until_no_more_offers(self):
        session = FakeJumoSession(
            [
                '<input name="_csrf" type="hidden" value="a&amp;b">',
                "search started",
                '<a href="x?jobOfferId=AA11">1</a><a href="x?jobOfferId=bb22">2</a>',
                "true",
                '<a href="x?jobOfferId=bb22">2</a><a href="x?jobOfferId=cc33">3</a>',
                "False",
            ]
        )
        with patch.object(jumo, "build_opener", return_value=session):
            links = jumo.collect_links()

        self.assertEqual(
            [link.split("jobOfferId=")[1].split("&")[0] for link in links], ["AA11", "bb22", "cc33"]
        )
        self.assertTrue(
            links[0].startswith(f"{jumo.BASE_URL}showJobOfferDetail.do?jobOfferId=AA11")
        )
        urls = [url for url, _form, _headers, _timeout in session.requests]
        forms = [form for _url, form, _headers, _timeout in session.requests]
        self.assertEqual(
            urls, [jumo.SEARCH_URL, f"{jumo.LIST_URL}?search=true", *[jumo.LIST_URL] * 4]
        )
        self.assertIsNone(forms[0])
        self.assertEqual(forms[1], {"j": "jobexchange", "_csrf": "a&b"})
        self.assertEqual(
            forms[2], {"showNextJobOffers": "true", "j": "jobexchange", "_csrf": "a&b"}
        )
        self.assertEqual(forms[3], {"hasNextJobOffers": "true", "_csrf": "a&b"})
        for _url, form, headers, timeout in session.requests:
            self.assertEqual(headers["User-agent"], "job-finder/0.1")
            self.assertEqual(timeout, 20)
            if form is not None:
                self.assertEqual(headers["Content-type"], "application/x-www-form-urlencoded")

    def test_collect_links_needs_the_csrf_token(self):
        with (
            patch.object(jumo, "build_opener", return_value=FakeJumoSession(["<form></form>"])),
            self.assertRaisesRegex(ValueError, "CSRF"),
        ):
            jumo.collect_links()


EDAG_LIST = "https://www.edag.com/de/karriere/stellenanzeigen"
EDAG_DETAIL = "https://www.edag.com/de/karriere/stellenanzeigen/detail"
COMPANY_SOURCES = [
    (
        css,
        "CSS AG",
        {
            "https://jobs.css.de/public/jobs/?standort=1": '<a href="https://jobs.css.de/job-dev-1.html">'
            '<a href="/job-admin-9.html"><a href="https://jobs.css.de/public/jobs/">'
            '<a href="https://jobs.css.de/job-dev-1.html#top">'
        },
        ["https://jobs.css.de/job-dev-1.html", "https://jobs.css.de/job-admin-9.html"],
    ),
    (
        proemion,
        "Proemion GmbH",
        {
            # A query that survives canonicalisation breaks the anchored pattern.
            "https://proemion.jobs.personio.de/?language=de": '<a href="/job/77?language=de">'
            '<a href="/job/88?language=de&amp;display=de"><a href="/?language=de">'
        },
        ["https://proemion.jobs.personio.de/job/77"],
    ),
    (
        bytewerk,
        "bytewerk GmbH",
        {"https://bytewerk-gmbh.jobs.personio.de/?language=de": '<a href="/job/55"><a href="/">'},
        ["https://bytewerk-gmbh.jobs.personio.de/job/55"],
    ),
    (
        rhoenenergie,
        "RhönEnergie Fulda GmbH",
        {
            "https://re-gruppe.de/karriere/": '<a href="/karriere/it-admin-de-j123.html">'
            '<a href="https://re-gruppe.de/karriere/">'
        },
        ["https://re-gruppe.de/karriere/it-admin-de-j123.html"],
    ),
    (
        nethinks,
        "NETHINKS GmbH",
        {
            "https://nethinks.com/nethinks_jobs/": '<a href="/nethinks_jobs/page/2/">'
            '<a href="/nethinks_jobs/page/3/"><a href="/nethinks_jobs/dev/">'
            '<a href="/nethinks_jobs/feed/">',
            "https://nethinks.com/nethinks_jobs/page/2/": '<a href="/nethinks_jobs/dev/">'
            '<a href="/nethinks_jobs/ops/">',
            "https://nethinks.com/nethinks_jobs/page/3/": '<a href="/nethinks_jobs/qa/">',
        },
        [
            "https://nethinks.com/nethinks_jobs/dev/",
            "https://nethinks.com/nethinks_jobs/ops/",
            "https://nethinks.com/nethinks_jobs/qa/",
        ],
    ),
    (
        edag,
        "EDAG Engineering GmbH",
        {
            EDAG_LIST: '<a class="sfjob" href="/de/karriere/stellenanzeigen/detail/dev-fulda-11">'
            'Dev Fulda</a><a class="sfjob" href="/de/karriere/stellenanzeigen/detail/dev-muc-12">'
            'Dev München</a><a href="?tx_successfactors_view%5BcurrentPage%5D=2">2</a>',
            f"{EDAG_LIST}?tx_successfactors_view%5BcurrentPage%5D=2": '<a class="x sfjob" '
            f'href="{EDAG_DETAIL}/ops-13">Ops Mehrere Standorte</a><a class="sfjob" '
            'href="/de/karriere/stellenanzeigen/detail/dev-fulda-11">Dev Fulda</a>',
        },
        [f"{EDAG_DETAIL}/dev-fulda-11", f"{EDAG_DETAIL}/ops-13"],
    ),
    (
        compose_it,
        "COMPOSE IT",
        {"https://compose-it.de/unternehmen/karriere/": '<a href="/job/it-supporter/">'},
        ["https://compose-it.de/job/it-supporter/"],
    ),
]


class CompanyListingTests(unittest.TestCase):
    def test_each_company_source_hands_its_links_to_the_shared_cache(self):
        for module, company, pages, links in COMPANY_SOURCES:
            with self.subTest(module.SOURCE_NAME):
                with (
                    patch.object(module, "fetch_text", side_effect=pages.get) as fetched,
                    patch.object(module, "fetch_company_jobs", return_value=["job"]) as cache,
                ):
                    self.assertEqual(module.fetch_jobs("cache.json", now="now"), ["job"])

                self.assertEqual([call.args[0] for call in fetched.call_args_list], list(pages))
                args, kwargs = cache.call_args
                self.assertEqual(args, (module.SOURCE_NAME, company, links, "cache.json"))
                self.assertEqual(kwargs.pop("now"), "now")
                self.assertEqual(
                    kwargs, {"parser": module.job_from_html} if module in (edag, compose_it) else {}
                )

    def test_jumo_hands_its_session_links_to_the_shared_cache(self):
        with (
            patch.object(jumo, "collect_links", return_value=["https://jobs.jumo.de/a"]),
            patch.object(jumo, "fetch_company_jobs", return_value=["job"]) as cache,
        ):
            self.assertEqual(jumo.fetch_jobs("cache.json", now="now"), ["job"])
        cache.assert_called_once_with(
            "jumo", "JUMO GmbH & Co. KG", ["https://jobs.jumo.de/a"], "cache.json", now="now"
        )

    def test_source_names_stay_stable(self):
        self.assertEqual(
            [module.SOURCE_NAME for module, *_rest in COMPANY_SOURCES],
            ["css", "proemion", "bytewerk", "rhoenenergie", "nethinks", "edag", "compose_it"],
        )


class ComposeItSourceTests(unittest.TestCase):
    def test_collect_links_keeps_only_compose_job_pages(self):
        html = """
        <a href="https://compose-it.de/job/it-supporter/">Support</a>
        <a href="/job/it-systemadministrator/">Admin</a>
        <a href="https://compose-it.de/unternehmen/karriere/">Karriere</a>
        """

        with patch.object(compose_it, "fetch_text", return_value=html):
            links = compose_it.collect_links()

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
            compose_it.SOURCE_NAME,
            compose_it.COMPANY,
            "https://compose-it.de/job/it-supporter/",
            html,
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

        with patch.object(bytewerk, "fetch_text", return_value=html):
            links = bytewerk.collect_links()

        self.assertEqual(links, ["https://bytewerk-gmbh.jobs.personio.de/job/1249333"])


class RhoenenergieSourceTests(unittest.TestCase):
    def test_collect_links_keeps_only_current_job_details(self):
        html = """
        <a href="/karriere/IT-Fachadministrator-mwd-de-j1077.html">IT</a>
        <a href="https://re-gruppe.de/karriere/Busfahrer-mwd-de-j121.html">Bus</a>
        <a href="/karriere/">Karriere</a>
        <a href="https://other.test/karriere/Developer-de-j999.html">Andere</a>
        """

        with patch.object(rhoenenergie, "fetch_text", return_value=html):
            links = rhoenenergie.collect_links()

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
