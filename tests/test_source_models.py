"""Tests that every source creates the shared Job model."""

import json
import unittest
from datetime import date
from unittest.mock import Mock, patch

from job_agent.models import Job, WorkMode
from job_agent.sources import arbeitsagentur, get_in_it, stepstone


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
                    "arbeitszeitmodelle": ["VOLLZEIT"],
                    "aktuelleVeroeffentlichungsdatum": "2026-07-10",
                }
            },
        )

        with patch.object(arbeitsagentur, "fetch_text", return_value=html):
            job = arbeitsagentur.fetch_job(url)

        self.assert_common_job(job, "arbeitsagentur:123-S")
        self.assertEqual(job.work_mode, WorkMode.REMOTE)
        self.assertEqual(job.remote_percentage, 100)

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

    def test_get_in_it_creates_job_model(self):
        url = "https://www.get-in-it.de/jobsuche/p309919"
        html = json_ld_html(self.posting())

        with patch.object(get_in_it, "fetch_text", return_value=html):
            job = get_in_it.fetch_job(url)

        self.assert_common_job(job, "get_in_it:309919")
        self.assertEqual(job.work_mode, WorkMode.HYBRID)

    def assert_common_job(self, job, expected_id):
        self.assertIsInstance(job, Job)
        self.assertEqual(job.id, expected_id)
        self.assertEqual(job.locations, ["Fulda"])
        self.assertIn("<p>", job.description_raw)
        self.assertIn("Python und APIs", job.description_clean)
        self.assertEqual(job.published_at, date(2026, 7, 10))
        self.assertIsNotNone(job.fetched_at)
        if job.sources[0].source != "arbeitsagentur":
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
