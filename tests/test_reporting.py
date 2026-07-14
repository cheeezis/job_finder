"""Tests for preserving manual Markdown review feedback."""

import unittest

from job_agent.reporting import (
    format_feedback_line,
    parse_review_feedback,
    render_review_markdown,
)


class ReportingTests(unittest.TestCase):
    def test_parses_checked_feedback_by_url(self):
        markdown = """
### 80% | Example Job

- Bewertung: [ ] passt  [ ] vielleicht  [x] passt nicht
- URL: https://example.test/job
"""

        self.assertEqual(
            parse_review_feedback(markdown),
            {"https://example.test/job": "passt nicht"},
        )

    def test_formats_persisted_feedback(self):
        self.assertEqual(
            format_feedback_line("vielleicht"),
            "- Bewertung: [ ] passt  [x] vielleicht  [ ] passt nicht",
        )

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

        markdown = render_review_markdown(
            {"included": [job], "excluded": []},
        )

        self.assertIn("- Ort: Fulda, Frankfurt", markdown)
        self.assertIn("- Remote: 100%", markdown)
        self.assertIn("- URL: https://example.test/job", markdown)


if __name__ == "__main__":
    unittest.main()
