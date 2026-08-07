"""Tests for final recommendation output."""

import json
import tempfile
import unittest
from pathlib import Path

from job_agent.reporting import write_recommendations


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
            markdown_path = Path(directory) / "recommendations.md"
            write_recommendations(results, json_path, markdown_path)
            stored = json.loads(json_path.read_text(encoding="utf-8"))
            markdown = markdown_path.read_text(encoding="utf-8")

        self.assertEqual(len(stored["recommendations"]), 1)
        recommendation = stored["recommendations"][0]
        self.assertEqual(recommendation["llm_score"], 90)
        self.assertEqual(recommendation["url"], "https://company.test/job")
        self.assertNotIn("description_clean", recommendation)
        self.assertNotIn("reasons", recommendation)
        self.assertIn("90% | Junior Python Developer", markdown)
        self.assertIn("Python-APIs entwickeln", markdown)
        self.assertNotIn("rule-only", markdown)


if __name__ == "__main__":
    unittest.main()
