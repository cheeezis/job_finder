"""Tests for rendering model-based job reviews."""

import json
import tempfile
import unittest
from pathlib import Path

from job_agent.reporting import render_review_markdown, write_review_files


class ReportingTests(unittest.TestCase):
    def test_renders_new_model_fields(self):
        job = {
            "title": "Junior Python Developer",
            "company": "Example GmbH",
            "locations": ["Fulda", "Frankfurt"],
            "sources": [
                {
                    "source": "stepstone",
                    "url": "https://example.test/job",
                }
            ],
            "description_clean": "Python und APIs",
            "work_mode": "remote",
            "remote_percentage": 100,
            "match_percent": 82,
            "experience_level": "klare Einstiegsstelle",
            "reasons": ["Python gefunden"],
            "filter_status": "included",
        }

        markdown = render_review_markdown({"included": [job], "excluded": []})

        self.assertIn("- Ort: Fulda, Frankfurt", markdown)
        self.assertIn("- Remote: 100%", markdown)
        self.assertIn("- URL: https://example.test/job", markdown)

    def test_renders_llm_score_summary_tasks_and_gaps(self):
        job = {
            "title": "Junior Python Developer",
            "company": "Example GmbH",
            "locations": ["Fulda"],
            "sources": [],
            "work_mode": "hybrid",
            "match_percent": 80,
            "llm_score": 90,
            "llm_result": {
                "recommendation": "strong_match",
                "summary": "Sehr passende Einstiegsstelle.",
                "tasks": ["Python-APIs entwickeln"],
                "gaps": ["Docker ist nicht belegt."],
            },
            "reasons": [],
        }

        markdown = render_review_markdown({"included": [job], "excluded": []})

        self.assertIn("### 90% | Junior Python Developer", markdown)
        self.assertIn("- Regel-Score: 80%", markdown)
        self.assertIn("- KI-Score: 90%", markdown)
        self.assertIn("  - Python-APIs entwickeln", markdown)
        self.assertIn("  - Docker ist nicht belegt.", markdown)

    def test_scored_json_omits_descriptions_already_stored_in_import(self):
        job = {
            "id": "test:1",
            "description_raw": "<p>Volltext</p>",
            "description_clean": "Volltext",
            "score_reasons": ["Doppelte Gruende"],
            "reasons": ["Python gefunden"],
        }
        results = {"included": [job], "excluded": []}

        with tempfile.TemporaryDirectory() as directory:
            scored_path = Path(directory) / "scored.json"
            write_review_files(
                results,
                scored_path=scored_path,
                review_path=Path(directory) / "review.md",
            )
            stored = json.loads(scored_path.read_text(encoding="utf-8"))

        stored_job = stored["included"][0]
        self.assertNotIn("description_raw", stored_job)
        self.assertNotIn("description_clean", stored_job)
        self.assertNotIn("score_reasons", stored_job)


if __name__ == "__main__":
    unittest.main()
