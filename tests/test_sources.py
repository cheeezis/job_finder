"""Tests for source-specific search configuration and caching."""

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
from job_finder.models import Job, JobSource, WorkflowStatus, WorkMode
from job_finder.sources import (
    arbeitnow,
    arbeitsagentur,
    bytewerk,
    compose_it,
    edag,
    get_in_it,
    himalayas,
    jobicy,
    jumo,
    rhoenenergie,
    stepstone,
    startup_jobs,
    studysmarter,
)
from job_finder.sources.common import (
    canonical_detail_url,
    load_detail_cache,
    remote_region_allows_germany,
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
        self.assertTrue(all(item["location"] == "Beispielstadt" for item in searches))


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

        self.assertEqual(
            links,
            ["https://bytewerk-gmbh.jobs.personio.de/job/1249333"],
        )


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
        "value": {"minValue": 91000, "maxValue": 60000, "unitText": "YEAR"}
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


class HimalayasTests(unittest.TestCase):
    def test_build_search_url_limits_results_to_entry_level_remote_for_germany(self):
        url = himalayas.build_search_url("quality assurance", page=2)

        self.assertIn("q=quality+assurance", url)
        self.assertIn("country=DE", url)
        self.assertIn("seniority=Entry-level", url)
        self.assertIn("sort=recent", url)
        self.assertIn("page=2", url)

    def test_collect_records_paginates_and_removes_cross_query_duplicates(self):
        pages = [
            {
                "offset": 0,
                "limit": 2,
                "totalCount": 3,
                "jobs": [{"guid": "one"}, {"guid": "two"}],
            },
            {
                "offset": 2,
                "limit": 2,
                "totalCount": 3,
                "jobs": [{"guid": "three"}],
            },
            {
                "offset": 0,
                "limit": 2,
                "totalCount": 2,
                "jobs": [{"guid": "two"}, {"guid": "four"}],
            },
        ]

        with (
            patch.object(himalayas, "fetch_json", side_effect=pages) as fetch,
            patch.object(himalayas.time, "sleep") as sleep,
        ):
            records = himalayas.collect_records(["software", "data"])

        self.assertEqual(
            [record["guid"] for record in records],
            ["one", "two", "three", "four"],
        )
        self.assertEqual(fetch.call_count, 3)
        self.assertEqual(sleep.call_count, 2)

    def test_job_from_record_maps_remote_constraints_and_euro_salary(self):
        record = {
            "guid": "https://himalayas.app/companies/example/jobs/junior-developer",
            "applicationLink": (
                "https://himalayas.app/companies/example/jobs/junior-developer"
            ),
            "title": "Junior Developer",
            "companyName": "Example GmbH",
            "description": "<p>Python und APIs</p>",
            "employmentType": "Full Time",
            "seniority": ["Entry-level"],
            "locationRestrictions": [{"alpha2": "DE", "name": "Germany"}],
            "currency": "EUR",
            "salaryPeriod": "annual",
            "minSalary": 73000,
            "maxSalary": 101000,
            "pubDate": 1786838400000,
        }

        job = himalayas.job_from_record(record)

        self.assertTrue(job.id.startswith("himalayas:"))
        self.assertEqual(job.locations, ["Germany"])
        self.assertEqual(job.description_clean, "Python und APIs")
        self.assertIs(job.work_mode, WorkMode.REMOTE)
        self.assertEqual(job.remote_percentage, 100)
        self.assertEqual(job.career_levels, ["Entry-level"])
        self.assertEqual((job.salary_min_eur, job.salary_max_eur), (73000, 101000))
        self.assertEqual(job.published_at.isoformat(), "2026-08-16")

    def test_job_from_record_marks_unrestricted_non_euro_job_as_worldwide(self):
        job = himalayas.job_from_record(
            {
                "guid": "https://himalayas.app/companies/example/jobs/support",
                "title": "IT Support",
                "companyName": "Example",
                "description": "Support",
                "locationRestrictions": [],
                "currency": "USD",
                "minSalary": 91000,
                "maxSalary": 60000,
            }
        )

        self.assertEqual(job.locations, ["weltweit"])
        self.assertIsNone(job.salary_min_eur)
        self.assertIsNone(job.salary_max_eur)


class StartupJobsTests(unittest.TestCase):
    def test_source_is_only_configured_with_nonempty_api_key(self):
        self.assertFalse(startup_jobs.is_configured({}))
        self.assertFalse(
            startup_jobs.is_configured({startup_jobs.API_KEY_ENV: "  "})
        )
        self.assertTrue(
            startup_jobs.is_configured({startup_jobs.API_KEY_ENV: "sj_test"})
        )

    def test_collect_records_paginates_and_removes_scope_overlaps(self):
        pages = [
            {
                "data": [{"id": "one"}, {"id": "two"}],
                "has_more": True,
                "next_cursor": "cursor-2",
            },
            {
                "data": [{"id": "three"}],
                "has_more": False,
                "next_cursor": None,
            },
            {
                "data": [{"id": "two"}, {"id": "four"}],
                "has_more": False,
                "next_cursor": None,
            },
        ]
        scopes = (
            {"role": "engineering", "country": "DE"},
            {"role": "engineering", "workplace_type": "remote"},
        )

        with patch.object(startup_jobs, "fetch_json", side_effect=pages) as fetch:
            records = startup_jobs.collect_records(
                {"Authorization": "Bearer sj_test"}, scopes=scopes
            )

        self.assertEqual(
            [record["id"] for record in records],
            ["one", "two", "three", "four"],
        )
        self.assertEqual(fetch.call_count, 3)
        self.assertIn("country=DE", fetch.call_args_list[0].args[0])
        self.assertIn("starting_after=cursor-2", fetch.call_args_list[1].args[0])
        self.assertIn("workplace_type=remote", fetch.call_args_list[2].args[0])

    def test_fetch_jobs_uses_bearer_key(self):
        records = [
            {
                "id": "de",
                "title": "Junior Developer",
                "url": "https://startup.jobs/de",
                "company": {"name": "Example"},
                "location": {"country": "Germany", "country_code": "DE"},
            },
            {
                "id": "ca",
                "title": "Junior Developer",
                "url": "https://startup.jobs/ca",
                "company": {"name": "Example"},
                "location": {"country": "Canada", "country_code": "CA"},
            },
        ]
        with patch.object(
            startup_jobs, "collect_records", return_value=records
        ) as collect:
            jobs = startup_jobs.fetch_jobs(api_key="sj_test")

        self.assertEqual([job.id for job in jobs], ["startup_jobs:de"])
        self.assertEqual(
            collect.call_args.args[0]["Authorization"], "Bearer sj_test"
        )

    def test_job_from_record_maps_remote_location_and_annual_euro_salary(self):
        job = startup_jobs.job_from_record(
            {
                "id": "job-1",
                "title": "Junior Software Engineer",
                "url": "https://startup.jobs/example-job",
                "published_at": "2026-08-16T09:30:00Z",
                "employment_type": "full-time",
                "workplace_type": "remote",
                "location": {
                    "city": "Berlin",
                    "country": "Germany",
                    "country_code": "DE",
                },
                "salary_data": {
                    "min": 83000,
                    "max": 103000,
                    "currency": "EUR",
                    "interval": "year",
                },
                "company": {"name": "Example GmbH"},
                "description_html": "<p>Python und APIs</p>",
            }
        )

        self.assertTrue(job.id.startswith("startup_jobs:"))
        self.assertEqual(job.locations, ["Berlin, Germany"])
        self.assertEqual(job.description_clean, "Python und APIs")
        self.assertIs(job.work_mode, WorkMode.REMOTE)
        self.assertEqual(job.remote_percentage, 100)
        self.assertEqual((job.salary_min_eur, job.salary_max_eur), (83000, 103000))
        self.assertEqual(job.published_at.isoformat(), "2026-08-16")
        self.assertEqual(job.sources[0].url, "https://startup.jobs/example-job")


class JobicyTests(unittest.TestCase):
    def test_build_search_url_uses_bounded_official_filters(self):
        url = jobicy.build_search_url({"industry": "technical-support"})

        self.assertEqual(
            url,
            "https://jobicy.com/api/v2/remote-jobs"
            "?industry=technical-support&count=100",
        )

    def test_collect_records_merges_scopes_and_removes_duplicates(self):
        pages = [
            {"jobs": [{"id": "one"}, {"id": "two"}]},
            {"jobs": [{"id": "two"}, {"id": "three"}]},
        ]

        with (
            patch.object(jobicy, "fetch_json", side_effect=pages) as fetch,
            patch.object(jobicy.time, "sleep") as sleep,
        ):
            records = jobicy.collect_records(
                ({"geo": "germany"}, {"industry": "engineering"})
            )

        self.assertEqual(
            [record["id"] for record in records], ["one", "two", "three"]
        )
        self.assertEqual(fetch.call_count, 2)
        self.assertEqual(sleep.call_count, 1)

    def test_remote_regions_only_allow_germany_compatible_scopes(self):
        allowed = [
            "Germany",
            "Austria, Germany",
            "Europe",
            "Europe, UK",
            "Anywhere",
            "Worldwide",
            "",
        ]
        rejected = ["USA", "Canada", "India", "APAC", "EMEA", "LATAM"]

        for location in allowed:
            with self.subTest(location=location):
                self.assertTrue(remote_region_allows_germany(location))
        for location in rejected:
            with self.subTest(location=location):
                self.assertFalse(remote_region_allows_germany(location))

    def test_fetch_jobs_discards_explicit_foreign_only_regions(self):
        records = [
            {
                "id": "worldwide",
                "url": "https://jobicy.com/jobs/worldwide",
                "jobTitle": "Junior Developer",
                "companyName": "Example",
                "jobGeo": "Worldwide",
            },
            {
                "id": "usa",
                "url": "https://jobicy.com/jobs/usa",
                "jobTitle": "Junior Developer",
                "companyName": "Example",
                "jobGeo": "USA",
            },
        ]
        with patch.object(jobicy, "collect_records", return_value=records):
            jobs = jobicy.fetch_jobs()

        self.assertEqual([job.id for job in jobs], ["jobicy:worldwide"])

    def test_job_from_record_maps_remote_metadata_and_euro_salary(self):
        job = jobicy.job_from_record(
            {
                "id": 123,
                "url": "https://jobicy.com/jobs/junior-developer",
                "jobTitle": "Junior Developer",
                "companyName": "Example GmbH",
                "jobType": "Full Time",
                "jobGeo": "Germany",
                "jobLevel": "Entry-Level, Junior",
                "jobDescription": "<p>Python und APIs</p>",
                "pubDate": "2026-08-16 09:30:00",
                "salaryMin": "73000",
                "salaryMax": 101000,
                "salaryCurrency": "EUR",
                "salaryPeriod": "yearly",
            }
        )

        self.assertEqual(job.id, "jobicy:123")
        self.assertEqual(job.locations, ["Germany"])
        self.assertEqual(job.description_clean, "Python und APIs")
        self.assertIs(job.work_mode, WorkMode.REMOTE)
        self.assertEqual(job.remote_percentage, 100)
        self.assertEqual(job.career_levels, ["Entry-Level", "Junior"])
        self.assertEqual((job.salary_min_eur, job.salary_max_eur), (73000, 101000))
        self.assertEqual(job.published_at.isoformat(), "2026-08-16")

    def test_job_from_record_normalizes_anywhere_and_ignores_usd_salary(self):
        job = jobicy.job_from_record(
            {
                "id": "worldwide",
                "url": "https://jobicy.com/jobs/support",
                "jobTitle": "IT Support",
                "companyName": "Example",
                "jobGeo": "Anywhere",
                "jobDescription": "Support",
                "salaryMin": 91000,
                "salaryMax": 60000,
                "salaryCurrency": "USD",
                "salaryPeriod": "yearly",
            }
        )

        self.assertEqual(job.locations, ["weltweit"])
        self.assertIsNone(job.salary_min_eur)
        self.assertIsNone(job.salary_max_eur)


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
                "location": "Example City",
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
            "location": "Example City",
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


class CompanyCareerTests(unittest.TestCase):
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
