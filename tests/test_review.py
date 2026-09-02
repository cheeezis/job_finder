"""Tests for the local recommendation review workflow."""

import base64
from datetime import date
import json
import tempfile
import threading
import unittest
from http.server import HTTPServer
from pathlib import Path
from urllib.parse import urlencode
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from job_finder.memory import load_memory, save_memory
from job_finder.review import (
    APP_STYLES,
    APPLICATIONS_PAGE,
    LANDING_PAGE,
    REVIEW_PAGE,
    ReviewRequestHandler,
    load_review_jobs,
    start_application,
    update_review_decision,
    undo_ignored_decision,
    update_workflow_status,
)
from job_finder.config import LOCAL_SEARCH_LOCATION, LOCAL_SEARCH_POSTAL_CODE


class ReviewTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary_directory.name)
        self.memory_path = self.directory / "seen_jobs.json"
        self.recommendations_path = self.directory / "recommendations.json"
        save_memory(
            {
                "job:1": {
                    "title": "Python Developer",
                    "company": "Example GmbH",
                    "workflow_status": "interesting",
                }
            },
            self.memory_path,
        )
        self.recommendations_path.write_text(
            json.dumps(
                {
                    "recommendations": [
                        {
                            "id": "job:1",
                            "title": "Python Developer",
                            "company": "Example GmbH",
                            "match_percent": 80,
                            "role_group": "software_development",
                            "experience_level": "klare Einstiegsstelle",
                            "location_precheck": "100% remote Deutschland",
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )

    def tearDown(self):
        self.temporary_directory.cleanup()

    def test_review_jobs_include_persisted_workflow_status(self):
        jobs = load_review_jobs(self.recommendations_path, self.memory_path)

        self.assertEqual(jobs[0]["workflow_status"], "interesting")
        self.assertFalse(jobs[0]["application_tracked"])
        self.assertFalse(jobs[0]["international"])

    def test_reviewed_new_job_does_not_reappear_after_reload(self):
        document = json.loads(self.recommendations_path.read_text(encoding="utf-8"))
        document["recommendations"][0]["is_new"] = True
        self.recommendations_path.write_text(json.dumps(document), encoding="utf-8")

        jobs = load_review_jobs(self.recommendations_path, self.memory_path)

        self.assertEqual(jobs[0]["workflow_status"], "interesting")
        self.assertFalse(jobs[0]["is_new"])

    def test_unreviewed_new_job_remains_visible_after_reload(self):
        memory = load_memory(self.memory_path)
        memory["job:1"]["workflow_status"] = "new"
        save_memory(memory, self.memory_path)
        document = json.loads(self.recommendations_path.read_text(encoding="utf-8"))
        document["recommendations"][0]["is_new"] = True
        self.recommendations_path.write_text(json.dumps(document), encoding="utf-8")

        jobs = load_review_jobs(self.recommendations_path, self.memory_path)

        self.assertTrue(jobs[0]["is_new"])

    def test_review_jobs_classify_legacy_international_recommendation(self):
        document = json.loads(self.recommendations_path.read_text(encoding="utf-8"))
        document["recommendations"][0]["locations"] = ["weltweit"]
        self.recommendations_path.write_text(json.dumps(document), encoding="utf-8")

        jobs = load_review_jobs(self.recommendations_path, self.memory_path)

        self.assertTrue(jobs[0]["international"])

    def test_review_jobs_reclassify_stale_multicountry_recommendation(self):
        document = json.loads(self.recommendations_path.read_text(encoding="utf-8"))
        document["recommendations"][0].update(
            {
                "locations": ["Canada", "Germany", "United States"],
                "source_links": [
                    {"source": "himalayas", "url": "https://example.test/job"}
                ],
                "international": False,
            }
        )
        self.recommendations_path.write_text(json.dumps(document), encoding="utf-8")

        jobs = load_review_jobs(self.recommendations_path, self.memory_path)

        self.assertTrue(jobs[0]["international"])

    def test_review_jobs_preserve_stored_international_language_classification(self):
        document = json.loads(self.recommendations_path.read_text(encoding="utf-8"))
        document["recommendations"][0].update(
            {
                "locations": ["Germany"],
                "source_links": [
                    {"source": "jobicy", "url": "https://example.test/job"}
                ],
                "international": True,
            }
        )
        self.recommendations_path.write_text(json.dumps(document), encoding="utf-8")

        jobs = load_review_jobs(self.recommendations_path, self.memory_path)

        self.assertTrue(jobs[0]["international"])

    def test_stale_recommendation_id_resolves_through_known_url(self):
        memory = load_memory(self.memory_path)
        memory["job:1"]["workflow_status"] = "applied"
        memory["job:1"]["source_urls"] = ["https://portal.test/job"]
        memory["job:1"]["source_names"] = ["stepstone"]
        save_memory(memory, self.memory_path)
        self.recommendations_path.write_text(
            json.dumps(
                {
                    "recommendations": [
                        {
                            "id": "portal:99",
                            "url": "https://portal.test/job",
                            "title": "Python Developer",
                            "company": "Example GmbH",
                            "match_percent": 80,
                            "role_group": "software_development",
                            "experience_level": "klare Einstiegsstelle",
                            "location_precheck": "100% remote Deutschland",
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )

        jobs = load_review_jobs(self.recommendations_path, self.memory_path)

        self.assertEqual(jobs[0]["id"], "job:1")
        self.assertEqual(jobs[0]["workflow_status"], "applied")
        self.assertTrue(jobs[0]["application_tracked"])
        self.assertEqual(
            jobs[0]["source_links"],
            [{"source": "stepstone", "url": "https://portal.test/job"}],
        )

    def test_application_wins_over_exact_review_entry_with_same_url(self):
        memory = load_memory(self.memory_path)
        memory["job:1"].update(
            {
                "workflow_status": "interesting",
                "source_urls": ["https://portal.test/job"],
            }
        )
        memory["portal:applied"] = {
            "workflow_status": "applied",
            "workflow_history": [
                {"status": "applied", "occurred_on": "2026-08-12"}
            ],
            "source_urls": ["https://portal.test/job"],
            "source_names": ["arbeitsagentur", "test"],
        }
        save_memory(memory, self.memory_path)
        document = json.loads(self.recommendations_path.read_text(encoding="utf-8"))
        document["recommendations"][0].update(
            {
                "url": "https://portal.test/job",
                "source_links": [
                    {"source": "test", "url": "https://portal.test/job"}
                ],
            }
        )
        self.recommendations_path.write_text(json.dumps(document), encoding="utf-8")

        jobs = load_review_jobs(self.recommendations_path, self.memory_path)

        self.assertEqual(jobs[0]["id"], "portal:applied")
        self.assertEqual(jobs[0]["workflow_status"], "applied")
        self.assertTrue(jobs[0]["application_tracked"])

    def test_review_jobs_recognize_historical_application(self):
        memory = load_memory(self.memory_path)
        memory["job:1"].update(
            {
                "workflow_status": "ignored",
                "workflow_history": [
                    {"status": "applied", "occurred_on": "2026-08-01"},
                    {"status": "ignored", "occurred_on": "2026-08-02"},
                ],
            }
        )
        save_memory(memory, self.memory_path)

        jobs = load_review_jobs(self.recommendations_path, self.memory_path)

        self.assertTrue(jobs[0]["application_tracked"])

    def test_status_change_is_persisted(self):
        update_workflow_status("job:1", "applied", self.memory_path)

        jobs = load_review_jobs(self.recommendations_path, self.memory_path)
        self.assertEqual(jobs[0]["workflow_status"], "applied")

    def test_start_application_records_one_dated_event(self):
        result = start_application("job:1", self.memory_path)
        repeated_result = start_application("job:1", self.memory_path)

        entry = load_memory(self.memory_path)["job:1"]
        applied_events = [
            event
            for event in entry["workflow_history"]
            if event["status"] == "applied"
        ]
        self.assertEqual(result["workflow_status"], "applied")
        self.assertTrue(result["application_tracked"])
        self.assertEqual(repeated_result, result)
        self.assertEqual(
            applied_events,
            [{"status": "applied", "occurred_on": date.today().isoformat()}],
        )

    def test_start_application_archives_supplied_documents(self):
        documents_directory = self.directory / "application_documents"

        start_application(
            "job:1",
            self.memory_path,
            [
                {
                    "kind": "cover_letter",
                    "name": "Anschreiben.pdf",
                    "content": base64.b64encode(b"%PDF application").decode(
                        "ascii"
                    ),
                }
            ],
            documents_directory,
        )

        document = load_memory(self.memory_path)["job:1"][
            "application_documents"
        ][0]
        stored_files = list(documents_directory.rglob("*.pdf"))
        self.assertEqual(document["name"], "Anschreiben.pdf")
        self.assertEqual(len(stored_files), 1)
        self.assertEqual(stored_files[0].read_bytes(), b"%PDF application")

    def test_start_application_does_not_overwrite_later_progress(self):
        memory = load_memory(self.memory_path)
        memory["job:1"].update(
            {
                "workflow_status": "interview",
                "workflow_history": [
                    {"status": "applied", "occurred_on": "2026-08-01"},
                    {"status": "interview", "occurred_on": "2026-08-10"},
                ],
            }
        )
        save_memory(memory, self.memory_path)

        result = start_application("job:1", self.memory_path)

        entry = load_memory(self.memory_path)["job:1"]
        self.assertEqual(result["workflow_status"], "interview")
        self.assertEqual(entry["workflow_status"], "interview")
        self.assertEqual(len(entry["workflow_history"]), 2)

    def test_stale_review_decision_does_not_overwrite_application(self):
        memory = load_memory(self.memory_path)
        memory["job:1"].update(
            {
                "workflow_status": "interview",
                "workflow_history": [
                    {"status": "applied", "occurred_on": "2026-08-01"},
                    {"status": "interview", "occurred_on": "2026-08-10"},
                ],
            }
        )
        save_memory(memory, self.memory_path)

        result = update_review_decision(
            "job:1",
            "ignored",
            self.memory_path,
        )

        entry = load_memory(self.memory_path)["job:1"]
        self.assertEqual(result["workflow_status"], "interview")
        self.assertTrue(result["application_tracked"])
        self.assertEqual(entry["workflow_status"], "interview")
        self.assertEqual(len(entry["workflow_history"]), 2)

    def test_start_application_rejects_unknown_job(self):
        with self.assertRaisesRegex(KeyError, "Unbekannte Job-ID"):
            start_application("job:unknown", self.memory_path)

    def test_inquiry_is_persisted_as_review_decision(self):
        memory = load_memory(self.memory_path)
        memory["job:1"]["review_update_pending"] = True
        save_memory(memory, self.memory_path)

        result = update_review_decision(
            "job:1",
            "inquiry",
            self.memory_path,
        )

        self.assertEqual(result["workflow_status"], "inquiry")
        self.assertEqual(
            load_memory(self.memory_path)["job:1"]["workflow_status"],
            "inquiry",
        )
        self.assertFalse(
            load_memory(self.memory_path)["job:1"]["review_update_pending"]
        )

    def test_latest_ignored_decision_can_be_undone(self):
        update_review_decision("job:1", "ignored", self.memory_path)

        result = undo_ignored_decision(
            "job:1",
            "ignored",
            self.memory_path,
        )

        entry = load_memory(self.memory_path)["job:1"]
        self.assertEqual(result["workflow_status"], "interesting")
        self.assertEqual(entry["workflow_status"], "interesting")
        self.assertEqual(
            entry["workflow_history"],
            [{"status": "interesting", "occurred_on": None}],
        )

    def test_ignored_undo_rejects_a_changed_decision(self):
        update_review_decision("job:1", "ignored", self.memory_path)
        update_review_decision("job:1", "inquiry", self.memory_path)

        with self.assertRaisesRegex(ValueError, "zwischenzeitlich"):
            undo_ignored_decision(
                "job:1",
                "ignored",
                self.memory_path,
            )

    def test_invalid_status_is_rejected_without_changing_memory(self):
        with self.assertRaises(ValueError):
            update_workflow_status("job:1", "maybe", self.memory_path)

        jobs = load_review_jobs(self.recommendations_path, self.memory_path)
        self.assertEqual(jobs[0]["workflow_status"], "interesting")

    def test_unknown_job_is_rejected(self):
        with self.assertRaisesRegex(KeyError, "Unbekannte Job-ID"):
            update_workflow_status("job:unknown", "ignored", self.memory_path)

    def test_pages_have_distinct_routes(self):
        handler = type(
            "TemporaryPageHandler",
            (ReviewRequestHandler,),
            {
                "recommendations_path": self.recommendations_path,
                "memory_path": self.memory_path,
                "landing_page_path": LANDING_PAGE,
                "page_path": REVIEW_PAGE,
                "applications_page_path": APPLICATIONS_PAGE,
            },
        )
        server = HTTPServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        base_url = f"http://127.0.0.1:{server.server_port}"
        try:
            with urlopen(f"{base_url}/") as response:
                landing_page = response.read().decode("utf-8")
            with urlopen(f"{base_url}/review") as response:
                review_page = response.read().decode("utf-8")
            with urlopen(f"{base_url}/review?job=job%3A1") as response:
                targeted_review_page = response.read().decode("utf-8")
            with urlopen(f"{base_url}/applications") as response:
                applications_page = response.read().decode("utf-8")
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

        self.assertIn('href="/review"', landing_page)
        self.assertEqual(targeted_review_page, review_page)
        self.assertIn('href="/applications"', landing_page)
        self.assertIn("Stellen prüfen", landing_page)
        self.assertIn("Bewerbungen verwalten", landing_page)
        self.assertIn('id="manual-import-form"', landing_page)
        self.assertIn('fetch("/api/manual-import"', landing_page)
        for page in (landing_page, review_page, applications_page):
            self.assertIn('href="/app.css?v=2"', page)
            self.assertNotIn("<style", page)
            self.assertNotIn("style=", page)
        self.assertIn("Als beworben markieren", review_page)
        self.assertIn('id="application-dialog"', review_page)
        self.assertIn('id="cover-letter-file"', review_page)
        self.assertIn('id="resume-file"', review_page)
        self.assertIn("selectedDocuments()", review_page)
        self.assertIn("Rückfrage nötig", review_page)
        self.assertIn('changeStatus("inquiry")', review_page)
        self.assertIn('id="undo-ignored"', review_page)
        self.assertIn('fetch("/api/review-undo"', review_page)
        self.assertLess(
            review_page.index("const score ="),
            review_page.index("const published ="),
        )
        self.assertNotIn("Meine Notiz", review_page)
        self.assertNotIn('/api/note', review_page)
        self.assertIn("Ergebnis des Vorfilters", review_page)
        self.assertIn('id="role-filter"', review_page)
        self.assertIn('<option value="attention">Neu oder aktualisiert</option>', review_page)
        self.assertIn('job.is_new || job.review_update_pending', review_page)
        self.assertNotIn('<option value="fresh">Nur neu</option>', review_page)
        self.assertNotIn('<option value="updated">Nur aktualisiert</option>', review_page)
        self.assertIn('id="change-badge"', review_page)
        self.assertIn('id="experience-level"', review_page)
        self.assertIn('id="location-precheck"', review_page)
        self.assertNotIn("renderDecisionHints", review_page)
        self.assertNotIn("llm_score", review_page)
        self.assertIn("Bewerbung verwalten", review_page)
        self.assertIn("function safeUrl(value)", review_page)
        self.assertIn("renderSourceLinks(job);", review_page)
        self.assertIn("renderRouteLink(job);", review_page)
        self.assertIn("Entfernung &amp; Fahrtzeit", review_page)
        self.assertIn("https://www.google.com/maps/dir/", review_page)
        self.assertIn("Anzeigen öffnen (${links.length})", review_page)
        self.assertIn('id="international-filter" type="checkbox"', review_page)
        self.assertIn("(showInternational || !job.international)", review_page)
        self.assertIn('id="junior-hybrid-filter" type="checkbox"', review_page)
        self.assertIn("Junior-Sonderfälle anzeigen", review_page)
        self.assertIn("showJuniorHybrid || !String(job.location_precheck", review_page)
        self.assertIn("function applyFilters(resetPosition = true)", review_page)
        self.assertIn("applyFilters(false);", review_page)
        self.assertIn("prefilter-warning", review_page)
        self.assertIn("new URLSearchParams(window.location.search)", review_page)
        self.assertNotIn("progress-select", review_page)
        self.assertIn('href="/">← Zur Startseite</a>', review_page)
        self.assertIn("Bewerbungsübersicht", applications_page)
        self.assertIn("appendDocuments(card, application);", applications_page)
        self.assertIn("/api/application-document?${query}", applications_page)
        self.assertIn('inquiry: "Rückfrage offen"', applications_page)
        self.assertIn('href="/">← Zur Startseite</a>', applications_page)
        self.assertIn("application.automatic_no_response", applications_page)
        self.assertIn(
            'new Set(["rejected", "no_response", "offer"])',
            applications_page,
        )
        self.assertLess(
            applications_page.index('["offers",'),
            applications_page.index('["rejections",'),
        )
        self.assertLess(
            applications_page.index('["rejections",'),
            applications_page.index('["no_responses",'),
        )

    def test_shared_stylesheet_is_served(self):
        handler = type(
            "TemporaryStyleHandler",
            (ReviewRequestHandler,),
            {"styles_path": APP_STYLES},
        )
        server = HTTPServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        try:
            with urlopen(
                f"http://127.0.0.1:{server.server_port}/app.css"
            ) as response:
                content_type = response.headers["Content-Type"]
                stylesheet = response.read().decode("utf-8")
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

        self.assertIn("text/css", content_type)
        self.assertIn("--accent", stylesheet)

    def test_manual_import_api_forwards_paths_and_url(self):
        calls = []

        def importer(url, **paths):
            calls.append((url, paths))
            return {"job_id": "manual:python", "analyzed": 1}

        handler = type(
            "TemporaryManualImportHandler",
            (ReviewRequestHandler,),
            {
                "recommendations_path": self.recommendations_path,
                "memory_path": self.memory_path,
                "jobs_path": self.directory / "jobs.json",
                "manual_cache_path": self.directory / "manual.json",
                "manual_importer": staticmethod(importer),
            },
        )
        server = HTTPServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        try:
            request = Request(
                f"http://127.0.0.1:{server.server_port}/api/manual-import",
                data=json.dumps(
                    {"url": "https://example.com/jobs/python"}
                ).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urlopen(request) as response:
                result = json.load(response)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

        self.assertEqual(result["job_id"], "manual:python")
        self.assertEqual(calls[0][0], "https://example.com/jobs/python")
        self.assertEqual(calls[0][1]["memory_path"], self.memory_path)

    def test_application_start_api_adds_job_to_overview(self):
        handler = type(
            "TemporaryApplicationStartHandler",
            (ReviewRequestHandler,),
            {
                "recommendations_path": self.recommendations_path,
                "memory_path": self.memory_path,
            },
        )
        server = HTTPServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        base_url = f"http://127.0.0.1:{server.server_port}"
        try:
            request = Request(
                f"{base_url}/api/applications",
                data=json.dumps({"job_id": "job:1"}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urlopen(request) as response:
                result = json.load(response)
            with urlopen(f"{base_url}/api/applications") as response:
                overview = json.load(response)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

        self.assertEqual(result["workflow_status"], "applied")
        self.assertTrue(result["application_tracked"])
        self.assertEqual(overview["statistics"]["total"], 1)
        self.assertEqual(overview["applications"][0]["id"], "job:1")

    def test_application_document_can_be_downloaded_from_overview_link(self):
        documents_directory = self.directory / "application_documents"
        start_application(
            "job:1",
            self.memory_path,
            [
                {
                    "kind": "resume",
                    "name": "Lebenslauf.pdf",
                    "content": base64.b64encode(b"%PDF resume").decode("ascii"),
                }
            ],
            documents_directory,
        )
        document = load_memory(self.memory_path)["job:1"][
            "application_documents"
        ][0]
        handler = type(
            "TemporaryDocumentHandler",
            (ReviewRequestHandler,),
            {
                "memory_path": self.memory_path,
                "application_documents_dir": documents_directory,
            },
        )
        server = HTTPServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        query = urlencode({"job_id": "job:1", "document_id": document["id"]})
        try:
            with urlopen(
                f"http://127.0.0.1:{server.server_port}"
                f"/api/application-document?{query}"
            ) as response:
                content = response.read()
                disposition = response.headers["Content-Disposition"]
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

        self.assertEqual(content, b"%PDF resume")
        self.assertIn("Lebenslauf.pdf", disposition)

    def test_application_can_store_optional_salary_expectation(self):
        result = start_application(
            "job:1",
            self.memory_path,
            salary_expectation="  58.000 € brutto / Jahr  ",
        )

        entry = load_memory(self.memory_path)["job:1"]
        self.assertTrue(result["application_tracked"])
        self.assertEqual(entry["salary_expectation"], "58.000 € brutto / Jahr")

    def test_salary_expectation_is_bounded(self):
        with self.assertRaisesRegex(ValueError, "höchstens 500"):
            start_application(
                "job:1",
                self.memory_path,
                salary_expectation="x" * 501,
            )

        self.assertNotIn(
            "salary_expectation",
            load_memory(self.memory_path)["job:1"],
        )

    def test_local_api_loads_jobs_and_persists_status(self):
        handler = type(
            "TemporaryReviewHandler",
            (ReviewRequestHandler,),
            {
                "recommendations_path": self.recommendations_path,
                "memory_path": self.memory_path,
                "page_path": REVIEW_PAGE,
            },
        )
        server = HTTPServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        base_url = f"http://127.0.0.1:{server.server_port}"
        try:
            with urlopen(f"{base_url}/api/recommendations") as response:
                document = json.load(response)
            request = Request(
                f"{base_url}/api/review-status",
                data=json.dumps(
                    {"job_id": "job:1", "workflow_status": "ignored"}
                ).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urlopen(request) as response:
                result = json.load(response)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

        self.assertEqual(
            document["recommendations"][0]["workflow_status"],
            "interesting",
        )
        self.assertEqual(
            document["route_origin"],
            f"{LOCAL_SEARCH_POSTAL_CODE} {LOCAL_SEARCH_LOCATION}",
        )
        self.assertEqual(result["workflow_status"], "ignored")
        self.assertNotIn("personal_ratings", document)
        jobs = load_review_jobs(self.recommendations_path, self.memory_path)
        self.assertEqual(jobs[0]["workflow_status"], "ignored")

    def test_local_api_rejects_dns_rebinding_host(self):
        handler = type(
            "SecureReviewHandler",
            (ReviewRequestHandler,),
            {"recommendations_path": self.recommendations_path,
             "memory_path": self.memory_path},
        )
        server = HTTPServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        try:
            request = Request(
                f"http://127.0.0.1:{server.server_port}/api/recommendations",
                headers={"Host": "attacker.example"},
            )
            with self.assertRaises(HTTPError) as caught:
                urlopen(request)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

        self.assertEqual(caught.exception.code, 403)
        caught.exception.close()

    def test_application_page_records_dated_event_and_returns_statistics(self):
        event_on = date.today().isoformat()
        handler = type(
            "TemporaryApplicationHandler",
            (ReviewRequestHandler,),
            {
                "recommendations_path": self.recommendations_path,
                "memory_path": self.memory_path,
                "page_path": REVIEW_PAGE,
                "applications_page_path": APPLICATIONS_PAGE,
            },
        )
        server = HTTPServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        base_url = f"http://127.0.0.1:{server.server_port}"
        try:
            with urlopen(f"{base_url}/applications") as response:
                page = response.read().decode("utf-8")
            request = Request(
                f"{base_url}/api/status",
                data=json.dumps(
                    {
                        "job_id": "job:1",
                        "workflow_status": "applied",
                        "occurred_on": event_on,
                    }
                ).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urlopen(request):
                pass
            no_response_request = Request(
                f"{base_url}/api/status",
                data=json.dumps(
                    {
                        "job_id": "job:1",
                        "workflow_status": "no_response",
                        "occurred_on": event_on,
                    }
                ).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urlopen(no_response_request):
                pass
            with urlopen(f"{base_url}/api/applications") as response:
                overview = json.load(response)
            no_response_event = next(
                event
                for event in overview["completed_applications"][0][
                    "workflow_history"
                ]
                if event["status"] == "no_response"
            )
            edit_request = Request(
                f"{base_url}/api/history",
                data=json.dumps(
                    {
                        "job_id": "job:1",
                        "event_index": no_response_event["event_index"],
                        "previous_status": "no_response",
                        "previous_occurred_on": event_on,
                        "workflow_status": "response",
                        "occurred_on": event_on,
                    }
                ).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urlopen(edit_request) as response:
                edit_result = json.load(response)
            delete_request = Request(
                f"{base_url}/api/history/delete",
                data=json.dumps(
                    {
                        "job_id": "job:1",
                        "event_index": no_response_event["event_index"],
                        "previous_status": "response",
                        "previous_occurred_on": event_on,
                    }
                ).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urlopen(delete_request) as response:
                delete_result = json.load(response)
            with urlopen(f"{base_url}/api/applications") as response:
                final_overview = json.load(response)
            interview_request = Request(
                f"{base_url}/api/status",
                data=json.dumps(
                    {
                        "job_id": "job:1",
                        "workflow_status": "interview",
                        "occurred_on": event_on,
                        "scheduled_for": "2099-08-25T10:30",
                    }
                ).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urlopen(interview_request):
                pass
            with urlopen(f"{base_url}/api/applications") as response:
                interview_overview = json.load(response)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

        self.assertIn("Bewerbungsübersicht", page)
        self.assertIn("Abgeschlossene Bewerbungen bearbeiten", page)
        self.assertIn('input.type = "datetime-local"', page)
        self.assertIn("Nächstes Gespräch", page)
        self.assertEqual(overview["statistics"]["total"], 1)
        self.assertEqual(overview["applications"], [])
        self.assertEqual(
            overview["completed_applications"][0]["applied_on"],
            event_on,
        )
        self.assertEqual(edit_result["workflow_status"], "response")
        self.assertEqual(delete_result["workflow_status"], "applied")
        self.assertEqual(
            interview_overview["applications"][0]["next_interview_at"],
            "2099-08-25T10:30",
        )
        self.assertEqual(final_overview["statistics"]["open"], 1)


if __name__ == "__main__":
    unittest.main()
