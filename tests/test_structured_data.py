"""Tests for shared schema.org JobPosting extraction."""

import unittest

from job_finder.structured_data import extract_json_ld_job_posting


class StructuredDataTests(unittest.TestCase):
    def test_entities_inside_json_strings_do_not_break_json(self):
        html = """<script type="application/ld+json">{
            "@type": "JobPosting",
            "description": "&lt;h2 id=&quot;aufgaben&quot;&gt;Aufgaben&lt;/h2&gt;"
        }</script>"""

        posting = extract_json_ld_job_posting(html)

        self.assertIn("&quot;aufgaben&quot;", posting["description"])

    def test_accepts_entirely_html_escaped_json(self):
        html = """<script type="application/ld+json">{
            &quot;@type&quot;: &quot;JobPosting&quot;,
            &quot;title&quot;: &quot;Developer&quot;
        }</script>"""

        self.assertEqual(extract_json_ld_job_posting(html)["title"], "Developer")

    def test_finds_nested_job_posting(self):
        html = """
            <script type="application/ld+json">
                {"@graph": [{"@type": "JobPosting", "title": "Developer"}]}
            </script>
        """

        posting = extract_json_ld_job_posting(html)

        self.assertEqual(posting["title"], "Developer")

    def test_skips_malformed_json_ld(self):
        html = """
            <script type="application/ld+json">{invalid}</script>
            <script type="application/ld+json">
                {"@type": "JobPosting", "title": "Data Engineer"}
            </script>
        """

        posting = extract_json_ld_job_posting(html)

        self.assertEqual(posting["title"], "Data Engineer")

    def test_returns_none_without_job_posting(self):
        html = '<script type="application/ld+json">{"@type": "WebPage"}</script>'

        self.assertIsNone(extract_json_ld_job_posting(html))
