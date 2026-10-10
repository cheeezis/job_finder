"""Visible job text stays separate from scripts, styles and structured data."""

import unittest

from job_finder.structured_data import extract_json_ld_job_posting
from job_finder.text import html_to_text


class HtmlTextTests(unittest.TestCase):
    def test_scripts_and_styles_are_not_visible_job_text(self):
        html = '<p>Python &amp; Azure</p><SCRIPT>const seniority = "30 years";</SCRIPT><style>.remote { display: none; }</style><p>Junior</p>'
        self.assertEqual(html_to_text(html), "Python & Azure Junior")

    def test_unclosed_script_and_style_do_not_leak_their_content(self):
        for tag in ("script", "style"):
            with self.subTest(tag=tag):
                self.assertEqual(html_to_text(f"<p>Python</p><{tag}>hidden <b>content"), "Python")

    def test_self_closing_hidden_tag_does_not_hide_following_text(self):
        self.assertEqual(html_to_text("<script/><style/><p>Junior</p>"), "Junior")

    def test_json_ld_remains_available_to_the_structured_data_parser(self):
        html = '<script type="application/ld+json">{"@type":"JobPosting","title":"Junior Python Developer"}</script><p>Python</p>'
        self.assertEqual(html_to_text(html), "Python")
        self.assertEqual(extract_json_ld_job_posting(html)["title"], "Junior Python Developer")
