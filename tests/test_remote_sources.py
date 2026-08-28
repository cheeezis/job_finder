"""Tests for remote-job API source adapters."""

import unittest
from unittest.mock import patch

from job_finder.models import WorkMode
from job_finder.sources import (
    himalayas,
    jobicy,
    startup_jobs,
)
from job_finder.sources.common import remote_region_allows_germany


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
            "maxSalary": 91000,
            "pubDate": 1786838400000,
        }

        job = himalayas.job_from_record(record)

        self.assertTrue(job.id.startswith("himalayas:"))
        self.assertEqual(job.locations, ["Germany"])
        self.assertEqual(job.description_clean, "Python und APIs")
        self.assertIs(job.work_mode, WorkMode.REMOTE)
        self.assertEqual(job.remote_percentage, 100)
        self.assertEqual(job.career_levels, ["Entry-level"])
        self.assertEqual((job.salary_min_eur, job.salary_max_eur), (73000, 91000))
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
                "minSalary": 50000,
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
                    "max": 101000,
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
        self.assertEqual((job.salary_min_eur, job.salary_max_eur), (83000, 101000))
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
                "salaryMax": 91000,
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
        self.assertEqual((job.salary_min_eur, job.salary_max_eur), (73000, 91000))
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
                "salaryMin": 50000,
                "salaryMax": 60000,
                "salaryCurrency": "USD",
                "salaryPeriod": "yearly",
            }
        )

        self.assertEqual(job.locations, ["weltweit"])
        self.assertIsNone(job.salary_min_eur)
        self.assertIsNone(job.salary_max_eur)

if __name__ == "__main__":
    unittest.main()
