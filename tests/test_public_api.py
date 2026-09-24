"""Compatibility checks for established Python entry points."""

import unittest

from job_finder.matching.scoring import analyze_experience, passes_hard_filters
from job_finder.review import memory_entry_for_job
from job_finder.sources.manual import main_fragment


class PublicApiTests(unittest.TestCase):
    def test_experience_accepts_original_two_argument_call(self):
        self.assertEqual(
            analyze_experience("developer", "2 jahre erfahrung"),
            {"rank": 3, "points": 8, "label": "2 Jahr(e) gefordert"},
        )

    def test_filter_returns_original_tuple_and_first_reason(self):
        self.assertEqual(
            passes_hard_filters("senior developer", "", "USA", "0%", "", None, []),
            (False, "Titel enthaelt Ausschlusswort: senior"),
        )

    def test_main_fragment_retains_original_html(self):
        self.assertEqual(main_fragment("<main><p>A &amp; B</p></main>"), "<p>A &amp; B</p>")
        with self.assertRaisesRegex(ValueError, "Kein Hauptinhalt"):
            main_fragment("<p>Outside</p>")

    def test_memory_lookup_retains_id_and_returns_original_entry(self):
        entry = {"source_urls": ["https://example.test/job"], "workflow_status": "interesting"}
        self.assertEqual(memory_entry_for_job({"id": "missing"}, {}), ("missing", {}))
        job_id, result = memory_entry_for_job(
            {"id": "old", "url": "https://example.test/job"}, {"saved": entry}
        )
        self.assertEqual(job_id, "saved")
        self.assertIs(result, entry)
