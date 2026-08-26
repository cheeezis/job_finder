"""Tests for compact rule-based review output."""

import json
import tempfile
import unittest
from pathlib import Path

from job_finder.reporting import is_international_listing, write_recommendations


def included_job(job_id="test:1"):
    return {
        "id": job_id,
        "title": "Junior Python Developer",
        "company": "Example GmbH",
        "locations": ["Fulda"],
        "sources": [{"source": "listing", "url": f"https://example.test/{job_id}"}],
        "work_mode": "remote",
        "remote_percentage": 100,
        "published_at": "2026-08-20",
        "first_seen_at": "2026-08-21T10:00:00+00:00",
        "match_percent": 80,
        "role_group": "software_development",
        "experience_level": "klare Einstiegsstelle",
        "location_precheck": "100% remote Deutschland",
        "description_clean": "Nicht für die Review-Ausgabe bestimmt",
        "reasons": ["interne Bewertungsdetails"],
    }


class ReportingTests(unittest.TestCase):
    def test_output_contains_every_included_job_and_no_description(self):
        first = included_job()
        first["sources"] = [
            {"source": "listing", "url": "https://portal.test/job"},
            {
                "source": "arbeitnow",
                "url": "https://arbeitnow.test/job",
                "application_url": "https://company.test/job",
            },
        ]
        second = included_job("test:2")

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "recommendations.json"
            write_recommendations(
                {"included": [first, second], "excluded": [included_job("excluded")]},
                path,
            )
            stored = json.loads(path.read_text(encoding="utf-8"))["recommendations"]

        self.assertEqual(len(stored), 2)
        recommendation = stored[0]
        self.assertEqual(recommendation["match_percent"], 80)
        self.assertEqual(recommendation["role_group"], "software_development")
        self.assertEqual(recommendation["url"], "https://company.test/job")
        self.assertEqual(len(recommendation["source_links"]), 3)
        self.assertNotIn("description_clean", recommendation)
        self.assertNotIn("reasons", recommendation)
        self.assertNotIn("llm_score", recommendation)

    def test_international_listing_uses_scope_language_and_source(self):
        for locations in (["weltweit"], ["Europe"]):
            with self.subTest(locations=locations):
                self.assertTrue(is_international_listing({"locations": locations}))
        for locations in (["Germany"], ["Remote, Germany"], ["Fulda"]):
            with self.subTest(locations=locations):
                self.assertFalse(is_international_listing({"locations": locations}))
        self.assertTrue(
            is_international_listing(
                {
                    "title": "Software Engineer",
                    "description_clean": (
                        "We build products and we need your experience. "
                        "You own your work and communicate with customers."
                    ),
                    "locations": ["Griesheim", "Büttelborn", "Germany"],
                    "sources": [{"source": "jobicy"}],
                }
            )
        )
        self.assertFalse(
            is_international_listing(
                {
                    "title": "Softwareentwickler",
                    "description_clean": (
                        "Wir entwickeln Software und suchen dich. Deine Aufgaben "
                        "und Kenntnisse besprechen wir gemeinsam."
                    ),
                    "locations": ["Griesheim", "Büttelborn", "Germany"],
                    "sources": [{"source": "himalayas"}],
                }
            )
        )
        self.assertFalse(
            is_international_listing(
                {
                    "title": "Software Engineer",
                    "description_clean": (
                        "We build products and we need your experience. "
                        "You own your work and communicate with customers."
                    ),
                    "locations": ["Germany"],
                    "sources": [
                        {"source": "jobicy"},
                        {"source": "stepstone"},
                    ],
                }
            )
        )
        self.assertTrue(
            is_international_listing(
                {
                    "locations": ["Canada", "Germany", "India"],
                    "sources": [{"source": "himalayas"}],
                }
            )
        )


if __name__ == "__main__":
    unittest.main()
