"""Closed listings must be proven, not inferred from missing search results."""

import tempfile
import unittest
from pathlib import Path
from urllib.error import HTTPError
from unittest.mock import patch

from job_finder.availability import ignore_closed_listings, listing_is_closed
from job_finder.memory import edit_memory, load_memory, save_memory
from job_finder.review import load_review_jobs, undo_ignored_decision


class AvailabilityTests(unittest.TestCase):
    def test_himalayas_removed_detail_redirect_but_not_login_is_closed(self):
        url = "https://himalayas.app/companies/example/jobs/developer"
        for final_url, expected in [
            ("https://himalayas.app/jobs", True),
            ("https://himalayas.app/jobs/?page=1", True),
            ("https://himalayas.app/login", False),
            ("https://other.test/jobs", False),
            (url, False),
        ]:
            with self.subTest(final_url=final_url), patch(
                "job_finder.availability.fetch_text_with_final_url",
                return_value=(final_url, "<main><h1>Remote jobs</h1></main>"),
            ):
                self.assertEqual(listing_is_closed(url), expected)

    def test_arbeitnow_missing_page_is_closed_even_with_http_200_and_related_jobs(self):
        url = "https://www.arbeitnow.com/jobs/companies/example/developer-123"
        html = '''<body><h1>Jobs in Germany</h1><h2>Page <span>not found</span></h2>
            <p>Click below to find more jobs.</p><h2>Other jobs you may like</h2>
            <script type="application/ld+json">
            {"@type":"JobPosting","title":"An unrelated suggested job"}
            </script></body>'''
        with patch("job_finder.availability.fetch_text_with_final_url", return_value=(url, html)):
            self.assertTrue(listing_is_closed(url))

    def test_arbeitnow_missing_detection_does_not_match_scripts_or_external_login(self):
        url = "https://www.arbeitnow.com/jobs/companies/example/developer-123"
        for final_url, html, expected in [
            (url, '<script>"<h2>Page not found</h2>"</script><h1>Developer</h1>', False),
            (url, '<h1>Developer</h1><p>Handle errors such as Page not found.</p>', False),
            ("https://login.example.test/", "<h2>Page not found</h2>", False),
            ("https://www.arbeitnow.com/?not_found=1", "<h1>Jobs in Germany</h1>", True),
        ]:
            with self.subTest(final_url=final_url, html=html), patch(
                "job_finder.availability.fetch_text_with_final_url", return_value=(final_url, html)
            ):
                self.assertEqual(listing_is_closed(url), expected)

    def test_only_definitive_http_errors_mean_closed(self):
        url = "https://example.test/job/1"
        for code, host, expected in [
            (404, "example.test", True), (410, "example.test", True),
            (403, "example.test", False), (429, "example.test", False),
            (500, "example.test", False), (404, "login.test", False),
        ]:
            error = HTTPError(f"https://{host}/job/1", code, "error", {}, None)
            with self.subTest(code=code, host=host), patch(
                "job_finder.availability.fetch_text_with_final_url", side_effect=error
            ):
                self.assertEqual(listing_is_closed(url), expected)
        with patch("job_finder.availability.fetch_text_with_final_url",
                   side_effect=TimeoutError):
            self.assertFalse(listing_is_closed(url))

    def test_explicit_visible_closure_but_not_login_or_unrelated_text(self):
        url = "https://example.test/job/1"
        for html, expected in [
            ("<main><h1>Diese Stelle ist nicht mehr verfügbar.</h1></main>", True),
            ("<main><p>No longer accepting applications</p></main>", True),
            ("<main><h1>Bitte anmelden</h1></main>", False),
            ("<main><h1>Developer</h1><p>Eine andere Stelle ist bereits vergeben.</p></main>", False),
            ("<main><h1>Developer</h1><script>Diese Stelle ist nicht mehr verfügbar.</script></main>", False),
        ]:
            with self.subTest(html=html), patch(
                "job_finder.availability.fetch_text_with_final_url", return_value=(url, html)
            ):
                self.assertEqual(listing_is_closed(url), expected)

    def test_closed_shortlist_moves_to_ignored_with_history_and_can_be_undone(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.sqlite3"
            save_memory({"job:1": {
                "title": "Developer", "workflow_status": "interesting",
                "source_urls": ["https://example.test/a", "https://example.test/b"],
            }}, path)
            with patch("job_finder.availability.listing_is_closed", return_value=True):
                self.assertEqual(ignore_closed_listings([], path), {"job:1"})
            entry = load_memory(path)["job:1"]
            self.assertEqual(entry["workflow_status"], "ignored")
            self.assertFalse(entry["active"])
            self.assertEqual(entry["workflow_history"][-1]["reason"], "listing_unavailable")
            self.assertTrue(entry["workflow_history"][-1]["occurred_on"])
            rows = load_review_jobs(Path(directory) / "missing.json", path)
            self.assertEqual(rows[0]["workflow_status"], "ignored")
            undo_ignored_decision("job:1", "ignored", path)
            self.assertEqual(load_memory(path)["job:1"]["workflow_status"], "interesting")

    def test_one_unconfirmed_source_preserves_the_shortlist(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.sqlite3"
            original = {"job:1": {"workflow_status": "new", "source_urls": [
                "https://example.test/a", "https://example.test/b"]}}
            save_memory(original, path)
            with patch("job_finder.availability.listing_is_closed", side_effect=[True, False]):
                self.assertEqual(ignore_closed_listings([], path), set())
            self.assertEqual(load_memory(path), original)

    def test_application_history_and_concurrent_decisions_are_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.sqlite3"
            save_memory({
                "job:1": {"workflow_status": "interesting", "source_urls": ["https://example.test/a"]},
                "job:2": {"workflow_status": "interesting", "source_urls": ["https://example.test/b"],
                          "workflow_history": [{"status": "applied", "occurred_on": "2026-09-01"}]},
            }, path)
            def change_during_request(url):
                with edit_memory(path) as state:
                    state["job:1"]["workflow_status"] = "inquiry"
                return True
            with patch("job_finder.availability.listing_is_closed", side_effect=change_during_request) as check:
                self.assertEqual(ignore_closed_listings([], path), set())
                check.assert_called_once_with("https://example.test/a")
            self.assertEqual(load_memory(path)["job:1"]["workflow_status"], "inquiry")
            self.assertEqual(load_memory(path)["job:2"]["workflow_status"], "interesting")
