"""Tests for final recommendation output."""

import json
import tempfile
import unittest
from pathlib import Path

from job_finder.reporting import is_international_listing, write_recommendations


def analyzed_job():
    """Return one compact final recommendation candidate."""
    return {
        "id": "test:1",
        "title": "Junior Python Developer",
        "company": "Example GmbH",
        "locations": ["Fulda"],
        "sources": [{"url": "https://example.test/job"}],
        "work_mode": "remote",
        "remote_percentage": 100,
        "match_percent": 80,
        "llm_score": 90,
        "llm_result": {
            "recommendation": "strong_match",
            "confidence": "high",
            "summary": "Sehr passende Einstiegsstelle.",
            "tasks": ["Python-APIs entwickeln"],
            "requirements": ["Python"],
            "matching_evidence": ["Python-Projekte sind belegt."],
            "gaps": ["Docker ist nicht belegt."],
            "risks": [],
        },
    }


class ReportingTests(unittest.TestCase):
    def test_output_contains_only_final_llm_recommendations(self):
        job = analyzed_job()
        job["sources"] = [
            {"url": "https://portal.test/job"},
            {
                "url": "https://arbeitnow.test/job",
                "application_url": "https://company.test/job",
            },
        ]
        results = {
            "included": [job, {"id": "rule-only"}],
            "excluded": [{"id": "excluded"}],
        }

        with tempfile.TemporaryDirectory() as directory:
            json_path = Path(directory) / "recommendations.json"
            write_recommendations(results, json_path)
            stored = json.loads(json_path.read_text(encoding="utf-8"))

        self.assertEqual(len(stored["recommendations"]), 1)
        recommendation = stored["recommendations"][0]
        self.assertEqual(recommendation["llm_score"], 90)
        self.assertEqual(recommendation["url"], "https://company.test/job")
        self.assertEqual(
            recommendation["source_links"],
            [
                {"source": "listing", "url": "https://portal.test/job"},
                {"source": "original", "url": "https://company.test/job"},
                {"source": "listing", "url": "https://arbeitnow.test/job"},
            ],
        )
        self.assertNotIn("description_clean", recommendation)
        self.assertNotIn("reasons", recommendation)
        self.assertEqual(recommendation["title"], "Junior Python Developer")
        self.assertEqual(recommendation["tasks"], ["Python-APIs entwickeln"])
        self.assertFalse(recommendation["international"])

    def test_failed_llm_job_is_kept_for_manual_review(self):
        failed = {
            "id": "test:failed",
            "title": "Junior Developer",
            "company": "Example GmbH",
            "locations": ["Europe"],
            "sources": [{"url": "https://example.test/failed"}],
            "llm_status": "failed",
        }

        with tempfile.TemporaryDirectory() as directory:
            json_path = Path(directory) / "recommendations.json"
            write_recommendations(
                {"included": [failed, {"id": "rule-only"}], "excluded": []},
                json_path,
            )
            stored = json.loads(json_path.read_text(encoding="utf-8"))

        self.assertEqual(len(stored["recommendations"]), 1)
        self.assertTrue(stored["recommendations"][0]["llm_unavailable"])
        self.assertTrue(stored["recommendations"][0]["international"])

    def test_international_listing_requires_broad_location_without_germany(self):
        international_locations = [["weltweit"], ["Europe"]]
        domestic_locations = [["Germany"], ["Remote, Germany"], ["Fulda"]]

        for locations in international_locations:
            with self.subTest(locations=locations):
                self.assertTrue(is_international_listing({"locations": locations}))
        for locations in domestic_locations:
            with self.subTest(locations=locations):
                self.assertFalse(is_international_listing({"locations": locations}))

        self.assertTrue(
            is_international_listing(
                {
                    "locations": ["Remote"],
                    "sources": [{"source": "startup_jobs"}],
                }
            )
        )
        self.assertFalse(
            is_international_listing(
                {
                    "locations": ["Remote"],
                    "sources": [{"source": "stepstone"}],
                }
            )
        )
        self.assertTrue(
            is_international_listing(
                {
                    "locations": [
                        "Canada",
                        "Germany",
                        "India",
                        "United Kingdom",
                        "United States",
                    ],
                    "sources": [{"source": "himalayas"}],
                }
            )
        )

    def test_current_interesting_job_survives_missing_llm_result(self):
        interesting = {
            "id": "test:interesting",
            "title": "AI Integration Engineer",
            "company": "Example GmbH",
            "locations": ["Remote"],
            "sources": [{"url": "https://example.test/interesting"}],
            "work_mode": "remote",
            "remote_percentage": 100,
            "workflow_status": "interesting",
            "llm_status": "failed",
        }
        results = {
            "included": [interesting, {"id": "rule-only"}],
            "excluded": [
                {
                    **interesting,
                    "id": "test:inactive",
                    "title": "Inactive Interesting Job",
                }
            ],
        }

        with tempfile.TemporaryDirectory() as directory:
            json_path = Path(directory) / "recommendations.json"
            write_recommendations(results, json_path)
            stored = json.loads(json_path.read_text(encoding="utf-8"))

        self.assertEqual(len(stored["recommendations"]), 1)
        recommendation = stored["recommendations"][0]
        self.assertTrue(recommendation["llm_unavailable"])
        self.assertIsNone(recommendation["llm_score"])
        self.assertEqual(recommendation["url"], "https://example.test/interesting")
        self.assertEqual(recommendation["title"], "AI Integration Engineer")


if __name__ == "__main__":
    unittest.main()
