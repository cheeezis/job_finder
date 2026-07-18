"""Tests that every source creates the shared Job model."""

import json
import unittest
from datetime import date
from unittest.mock import Mock, patch

from job_agent.models import Job, WorkMode
from job_agent.sources import arbeitnow, arbeitsagentur, edag, get_in_it, stepstone
from job_agent.sources.company_careers import job_from_json_ld


def script_html(script_id, values, script_type="application/json"):
    data = json.dumps(values)
    return f'<script id="{script_id}" type="{script_type}">{data}</script>'


def json_ld_html(posting):
    data = json.dumps(posting)
    return f'<script type="application/ld+json">{data}</script>'


class SourceJobModelTests(unittest.TestCase):
    def test_arbeitsagentur_creates_job_model(self):
        url = "https://www.arbeitsagentur.de/jobsuche/jobdetail/123-S"
        html = script_html(
            "ng-state",
            {
                "jobdetail": {
                    "stellenangebotsTitel": "Junior Python Developer",
                    "firma": "Example GmbH",
                    "stellenlokationen": [{"adresse": {"ort": "Fulda"}}],
                    "stellenangebotsBeschreibung": "<p>Python und APIs</p>",
                    "homeofficemoeglich": True,
                    "homeofficetyp": "AUSSCHLIESSLICH",
                    "externeURL": "https://example.test/apply",
                    "arbeitszeitVollzeit": True,
                    "datumErsteVeroeffentlichung": "2026-07-10",
                }
            },
        )

        with patch.object(arbeitsagentur, "fetch_text", return_value=html):
            job = arbeitsagentur.fetch_job(url)

        self.assert_common_job(job, "arbeitsagentur:123-S")
        self.assertEqual(job.work_mode, WorkMode.REMOTE)
        self.assertEqual(job.remote_percentage, 100)
        self.assertEqual(job.employment_type, "FULL_TIME")

    def test_stepstone_creates_job_model(self):
        url = (
            "https://www.stepstone.de/"
            "stellenangebote--Junior-Python-Developer--14291000-inline.html"
        )
        client = Mock()
        client.get.return_value = json_ld_html(self.posting())

        job = stepstone.fetch_job(url, client)

        self.assert_common_job(job, "stepstone:14291000")
        self.assertEqual(job.work_mode, WorkMode.HYBRID)
        self.assertIsNone(job.remote_percentage)

    def test_stepstone_reads_explicit_career_level_metadata(self):
        url = "https://www.stepstone.de/stellenangebote--Junior-Python--14250001-inline.html"
        client = Mock()
        client.get.return_value = (
            json_ld_html(self.posting())
            + '<script>{"metaData":{"contractType":"Berufseinstieg/Trainee, Feste Anstellung"}}</script>'
        )

        job = stepstone.fetch_job(url, client)

        self.assertEqual(job.career_levels, ["Berufseinstieg/Trainee"])

    def test_get_in_it_creates_job_model(self):
        url = "https://www.get-in-it.de/jobsuche/p309919"
        html = json_ld_html(self.posting())

        with patch.object(get_in_it, "fetch_text", return_value=html):
            job = get_in_it.fetch_job(url)

        self.assert_common_job(job, "get_in_it:309919")
        self.assertEqual(job.work_mode, WorkMode.HYBRID)

    def test_get_in_it_reads_explicit_career_level_from_job_facts(self):
        url = "https://www.get-in-it.de/jobsuche/p309921"
        posting = self.posting()
        posting["description"] = (
            "<p>Karrierestufe: Absolventinnen &amp; Absolventen; "
            "Berufserfahrene Beschäftigungsgrad: Vollzeit</p>"
        )

        with patch.object(get_in_it, "fetch_text", return_value=json_ld_html(posting)):
            job = get_in_it.fetch_job(url)

        self.assertEqual(
            job.career_levels,
            ["Absolventinnen & Absolventen", "Berufserfahrene"],
        )

    def test_get_in_it_ignores_portal_remote_boilerplate(self):
        url = "https://www.get-in-it.de/jobsuche/p309920"
        posting = self.posting()
        posting["description"] = "<p>Python und APIs</p>"
        html = "<div>Portalfilter: Homeoffice</div>" + json_ld_html(posting)

        with patch.object(get_in_it, "fetch_text", return_value=html):
            job = get_in_it.fetch_job(url)

        self.assertEqual(job.work_mode, WorkMode.ONSITE)
        self.assertEqual(job.remote_percentage, 0)

    def test_arbeitnow_creates_job_model(self):
        job = arbeitnow.job_from_record(
            {
                "slug": "junior-python-developer-123",
                "company_name": "Example GmbH",
                "title": "Junior Python Developer",
                "description": "<p>Python und APIs</p>",
                "remote": True,
                "url": "https://www.arbeitnow.com/jobs/example/123",
                "job_types": ["full_time"],
                "location": "Fulda",
                "created_at": 1783641600,
            }
        )

        self.assert_common_job(job, "arbeitnow:junior-python-developer-123")
        self.assertEqual(job.work_mode, WorkMode.REMOTE)
        self.assertEqual(job.remote_percentage, 100)
        self.assertEqual(job.employment_type, "full_time")

    def test_company_json_ld_creates_job_model(self):
        url = "https://careers.example.test/job/123"
        job = job_from_json_ld(
            "example_company",
            "Fallback GmbH",
            url,
            json_ld_html(self.posting()),
        )

        self.assert_common_job(job, "example_company:123")
        self.assertEqual(job.company, "Example GmbH")
        self.assertEqual(job.work_mode, WorkMode.HYBRID)

    def test_company_json_ld_uses_url_when_schema_identifier_is_generic(self):
        url = "https://careers.example.test/job/unique-role-456"
        posting = self.posting()
        posting["identifier"] = {"value": "123456SM"}

        job = job_from_json_ld(
            "example_company",
            "Fallback GmbH",
            url,
            json_ld_html(posting),
        )

        self.assertEqual(job.id, "example_company:456")

    def test_edag_visible_detail_creates_job_model(self):
        url = (
            "https://www.edag.com/de/karriere/stellenanzeigen/detail/"
            "junior-python-developer-58815"
        )
        html = """
            <div class="short-facts">
              <span>EDAG Engineering GmbH</span>
              <span>Hybrides Arbeiten möglich</span>
              <span>Fulda</span>
              <span>Absolventen</span>
              <span>Vollzeit</span>
            </div>
            <div class="breadcrumb"></div>
            <h2 class="title h2">Junior Python Developer (m/w/d)</h2>
            <div class="teaser">Python und APIs</div>
            <div class="description"><div>Einsteiger willkommen</div></div>
            </div></div><div class="actions">
        """

        job = edag.job_from_html(edag.SOURCE_NAME, edag.COMPANY, url, html)

        self.assertEqual(job.id, "edag:58815")
        self.assertEqual(job.title, "Junior Python Developer (m/w/d)")
        self.assertEqual(job.locations, ["Fulda"])
        self.assertEqual(job.work_mode, WorkMode.HYBRID)
        self.assertEqual(job.employment_type, "Vollzeit")
        self.assertIn("Einsteiger willkommen", job.description_clean)

    def assert_common_job(self, job, expected_id):
        self.assertIsInstance(job, Job)
        self.assertEqual(job.id, expected_id)
        self.assertEqual(job.locations, ["Fulda"])
        self.assertIn("<p>", job.description_raw)
        self.assertIn("Python und APIs", job.description_clean)
        self.assertEqual(job.published_at, date(2026, 7, 10))
        self.assertIsNotNone(job.fetched_at)
        if job.sources[0].source in {"stepstone", "get_in_it"}:
            self.assertEqual(job.salary_min_eur, 73_000)
            self.assertEqual(job.salary_max_eur, 91_000)

    @staticmethod
    def posting():
        return {
            "@type": "JobPosting",
            "title": "Junior Python Developer",
            "hiringOrganization": {"name": "Example GmbH"},
            "jobLocation": {
                "address": {"addressLocality": "Fulda"},
            },
            "description": "<p>Python und APIs, Homeoffice</p>",
            "employmentType": "FULL_TIME",
            "datePosted": "2026-07-10",
            "baseSalary": {
                "currency": "EUR",
                "value": {
                    "minValue": 73_000,
                    "maxValue": 91_000,
                    "unitText": "YEAR",
                },
            },
        }


if __name__ == "__main__":
    unittest.main()
