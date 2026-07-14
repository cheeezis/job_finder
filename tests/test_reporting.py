"""Tests for preserving manual Markdown review feedback."""

import unittest

from job_agent.reporting import format_feedback_line, parse_review_feedback


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


if __name__ == "__main__":
    unittest.main()
