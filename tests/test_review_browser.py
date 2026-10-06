"""The review in a real browser on the demo data: fact sheet, decisions with a note, waiting list, undo, application.

Runs only with JOBFINDER_BROWSER_TESTS=1, as the CI job "Browser tests" sets it, because it needs
Chromium (uv run playwright install chromium) and the local PostgreSQL of the quickstart. Every test
gets a freshly seeded demo database and its own review server, so the tests do not depend on each other.
"""

import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
ENABLED = os.environ.get("JOBFINDER_BROWSER_TESTS") == "1"
# A demo job with a fact sheet, and the file the application test uploads.
WITH_FACT_SHEET = "demo:lokal-andere-richtung"
RESUME = {
    "name": "Lebenslauf.pdf",
    "mimeType": "application/pdf",
    "buffer": b"%PDF-1.4 Lebenslauf aus dem Browser-Test",
}


def free_port():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


@unittest.skipUnless(ENABLED, "Browser-Tests laufen nur mit JOBFINDER_BROWSER_TESTS=1 (CI-Job Browser tests)")
class ReviewBrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from playwright.sync_api import sync_playwright

        cls.playwright = sync_playwright().start()
        cls.browser = cls.playwright.chromium.launch()

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()

    def setUp(self):
        self.base_url = f"http://127.0.0.1:{free_port()}"
        log = self.enterContext(tempfile.TemporaryFile())
        server = subprocess.Popen(
            [
                sys.executable,
                "scripts/demo_data.py",
                "--serve",
                "--no-browser",
                "--port",
                self.base_url.rsplit(":", 1)[1],
            ],
            cwd=ROOT,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        self.addCleanup(server.wait, 10)
        self.addCleanup(server.terminate)
        self.wait_for(server, log)
        context = self.browser.new_context(base_url=self.base_url, locale="de-DE")
        self.addCleanup(context.close)
        self.page = context.new_page()

    def wait_for(self, server, log):
        """Wait until the demo is seeded and the review answers; fail with the script's output otherwise."""
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline and server.poll() is None:
            try:
                with urlopen(self.base_url + "/", timeout=2):
                    return
            except (URLError, OSError):
                time.sleep(0.3)
        log.seek(0)
        self.fail("Demo-Review startet nicht:\n" + log.read().decode("utf-8", "replace"))

    def job(self, title):
        """The review's view of one job, read through the same API the page uses."""
        jobs = self.page.request.get("/api/recommendations").json()["recommendations"]
        return next(job for job in jobs if job["title"] == title)

    def current_title(self):
        """The title of the card on screen, once the page has loaded and drawn it."""
        from playwright.sync_api import expect

        expect(self.page.locator("#card")).to_be_visible()
        expect(self.page.locator("#title")).not_to_be_empty()
        return self.page.locator("#title").inner_text()

    def decide(self, button):
        """Click a decision on the current card; return the card's title once the server answered."""
        title = self.current_title()
        with self.page.expect_response(lambda response: "/api/review-" in response.url) as answer:
            self.page.click(button)
        self.assertTrue(answer.value.ok)
        return title

    def test_a_card_shows_the_agents_fact_sheet(self):
        from playwright.sync_api import expect

        self.page.goto(f"/review?job={WITH_FACT_SHEET}")

        expect(self.page.locator("#fact-sheet")).to_be_visible()
        expect(self.page.locator("#fact-sheet-verdict")).to_contain_text("Fazit")
        self.assertGreaterEqual(self.page.locator("#fact-sheet-lines li").count(), 7)
        expect(self.page.locator("#fact-sheet-meta")).to_contain_text("gpt-5-mini")

    def test_a_decision_saves_the_note_and_moves_on(self):
        from playwright.sync_api import expect

        self.page.goto("/review")
        self.current_title()
        expect(self.page.locator("#counter")).to_contain_text("1 von")
        before = self.page.locator("#counter").inner_text()
        self.page.fill("#review-note", "Browser-Test: Team klingt gut")

        title = self.decide("#mark-interesting")

        expect(self.page.locator("#counter")).not_to_have_text(before)
        self.assertNotEqual(self.page.locator("#title").inner_text(), title)
        job = self.job(title)
        self.assertEqual(job["workflow_status"], "interesting")
        self.assertEqual(job["review_note"], "Browser-Test: Team klingt gut")

    def test_a_waiting_job_appears_under_its_filter(self):
        from playwright.sync_api import expect

        self.page.goto("/review")
        title = self.decide("#mark-waiting")
        self.page.select_option("#status-filter", "waiting")

        expect(self.page.locator("#title")).to_have_text(title)
        self.assertEqual(self.job(title)["workflow_status"], "waiting")

    def test_offline_listings_load_only_with_their_filter(self):
        from playwright.sync_api import expect

        offline = {
            "id": "demo:offline",
            "title": "Abgelaufene Stelle",
            "company": "Beispiel GmbH",
            "workflow_status": "ignored",
            "source_links": [],
            "current_snapshot_missing": True,
            "prefilter_warning": "Anzeige nicht mehr verfügbar; automatisch auf Nicht interessant gesetzt.",
        }
        requests = []

        def archive(route):
            requests.append(route.request.url)
            route.fulfill(json={"recommendations": [offline]})

        self.page.route("**/api/recommendations?archived=1", archive)
        self.page.goto("/review")
        self.current_title()
        self.assertEqual(requests, [])

        self.page.select_option("#status-filter", "ignored")
        self.page.fill("#search-filter", "Abgelaufene")

        expect(self.page.locator("#title")).to_have_text("Abgelaufene Stelle")
        self.page.select_option("#status-filter", "")
        self.assertEqual(len(requests), 1)

    def test_not_interested_can_be_undone(self):
        from playwright.sync_api import expect

        self.page.goto("/review")
        title = self.decide("#mark-ignored")
        self.assertEqual(self.job(title)["workflow_status"], "ignored")

        expect(self.page.locator("#undo-ignored")).to_be_visible()
        with self.page.expect_response(lambda response: "/api/review-undo" in response.url):
            self.page.click("#undo-ignored")

        expect(self.page.locator("#title")).to_have_text(title)
        self.assertEqual(self.job(title)["workflow_status"], "new")

    def test_an_application_keeps_its_document(self):
        from playwright.sync_api import expect

        self.page.goto("/review")
        title = self.current_title()
        self.page.click("#mark-applied")
        expect(self.page.locator("#application-dialog")).to_be_visible()
        self.page.set_input_files("#resume-file", files=[RESUME])
        self.page.fill("#salary-expectation", "52000")
        with self.page.expect_response(
            lambda response: response.url.endswith("/api/applications") and response.request.method == "POST"
        ) as answer:
            self.page.click("#application-save")
        self.assertTrue(answer.value.ok)

        self.page.goto("/applications")
        application = self.page.locator("#applications").get_by_text(title).first
        expect(application).to_be_visible()
        link = self.page.locator('#applications a[href*="/api/application-document"]').first
        document = self.page.request.get(link.get_attribute("href"))
        self.assertEqual(document.body(), RESUME["buffer"])

    def test_a_listing_can_be_attached_to_an_existing_application(self):
        from playwright.sync_api import expect

        before = self.page.request.get("/api/applications").json()
        target = before["applications"][0]
        self.page.goto("/review")
        title = self.current_title()
        self.page.fill("#review-note", "Notiz zur zusätzlichen Anzeige")
        self.page.click("#link-application")
        expect(self.page.locator("#link-application-dialog")).to_be_visible()
        self.page.select_option("#link-application-select", target["id"])
        with self.page.expect_response(lambda response: response.url.endswith("/api/application-listing")) as answer:
            self.page.click("#link-application-save")
        self.assertTrue(answer.value.ok)
        self.page.wait_for_url("**/applications?job=*")
        expect(self.page.locator(".selected-application h2")).to_have_text(target["title"])
        after = self.page.request.get("/api/applications").json()
        self.assertEqual(after["statistics"]["total"], before["statistics"]["total"])
        linked = next(application for application in after["applications"] if application["id"] == target["id"])
        self.assertEqual(linked["documents"], target["documents"])
        self.assertEqual(linked["workflow_history"], target["workflow_history"])
        self.assertEqual(linked["linked_listings"][0]["title"], title)
        self.assertEqual(linked["linked_listings"][0]["review_note"], "Notiz zur zusätzlichen Anzeige")


if __name__ == "__main__":
    unittest.main()
