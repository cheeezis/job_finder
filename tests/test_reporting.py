"""Tests for rendering model-based job reviews."""

import unittest

from job_agent.reporting import render_review_markdown


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


if __name__ == "__main__":
    unittest.main()
