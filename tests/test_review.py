"""Tests for the local recommendation review workflow."""

import base64
from contextlib import contextmanager
from datetime import date
import json
import socket
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
    APP_SCRIPT,
    LocalReviewServer,
    REVIEW_PAGE,
    ReviewRequestHandler,
    acknowledge_review_update,
    address_is_in_use,
    load_review_jobs,
    start_application,
    update_review_decision,
    undo_ignored_decision,
    update_workflow_status,
)
from job_finder.config import LOCAL_SEARCH_LOCATION, LOCAL_SEARCH_POSTAL_CODE


class ReviewTests(unittest.TestCase):
    def test_address_in_use_is_recognized_on_windows(self):
        error = OSError()
        error.winerror = 10048

        self.assertTrue(address_is_in_use(error))

    def test_review_server_requests_exclusive_port_binding_on_windows(self):
        self.assertFalse(LocalReviewServer.allow_reuse_address)
        if not hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.skipTest("SO_EXCLUSIVEADDRUSE is Windows-specific")
        server = object.__new__(LocalReviewServer)
        server.socket = unittest.mock.Mock()

        with unittest.mock.patch.object(HTTPServer, "server_bind") as parent_bind:
            server.server_bind()

        server.socket.setsockopt.assert_called_once_with(
            socket.SOL_SOCKET,
            socket.SO_EXCLUSIVEADDRUSE,
            1,
        )
        parent_bind.assert_called_once_with()

    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary_directory.name)
        self.memory_path = self.directory / "state.sqlite3"
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

    @contextmanager
    def server_context(self, **attributes):
        handler = type("TemporaryReviewHandler", (ReviewRequestHandler,), {
            "recommendations_path": self.recommendations_path,
            "memory_path": self.memory_path,
            **attributes,
        })
        server = HTTPServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        try:
            yield f"http://127.0.0.1:{server.server_port}"
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

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

    def test_active_interesting_job_survives_one_missed_source_run(self):
        self.recommendations_path.write_text(
            json.dumps({"recommendations": []}), encoding="utf-8"
        )
        memory = load_memory(self.memory_path)
        memory["job:1"].update(
            {
                "active": True,
                "missed_runs": 1,
                "locations": ["Hamburg"],
                "source_names": ["studysmarter"],
                "source_urls": ["https://example.test/job"],
            }
        )
        save_memory(memory, self.memory_path)

        jobs = load_review_jobs(self.recommendations_path, self.memory_path)

        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["id"], "job:1")
        self.assertEqual(jobs[0]["workflow_status"], "interesting")
        self.assertTrue(jobs[0]["current_snapshot_missing"])
        self.assertIn("aktuellen Lauf nicht gefunden", jobs[0]["prefilter_warning"])
        self.assertEqual(
            jobs[0]["source_links"],
            [{"source": "studysmarter", "url": "https://example.test/job"}],
        )

    def test_inactive_interesting_job_remains_with_availability_warning(self):
        self.recommendations_path.write_text(
            json.dumps({"recommendations": []}), encoding="utf-8"
        )
        memory = load_memory(self.memory_path)
        memory["job:1"]["active"] = False
        save_memory(memory, self.memory_path)

        jobs = load_review_jobs(self.recommendations_path, self.memory_path)

        self.assertEqual(len(jobs), 1)
        self.assertIn("mehreren vollständigen Läufen", jobs[0]["prefilter_warning"])

    def test_merged_recommendation_does_not_restore_a_source_alias(self):
        document = json.loads(self.recommendations_path.read_text(encoding="utf-8"))
        document["recommendations"][0]["source_links"] = [
            {"source": "first", "url": "https://example.test/first"},
            {"source": "second", "url": "https://example.test/second"},
        ]
        self.recommendations_path.write_text(json.dumps(document), encoding="utf-8")
        memory = load_memory(self.memory_path)
        memory["job:1"]["source_urls"] = ["https://example.test/first"]
        memory["job:alias"] = {
            "title": "Python Developer",
            "company": "Example GmbH",
            "workflow_status": "interesting",
            "active": True,
            "source_urls": ["https://example.test/second"],
        }
        save_memory(memory, self.memory_path)

        jobs = load_review_jobs(self.recommendations_path, self.memory_path)

        self.assertEqual(len(jobs), 1)

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

    def test_update_can_be_acknowledged_without_changing_workflow_status(self):
        memory = load_memory(self.memory_path)
        memory["job:1"]["review_update_pending"] = True
        save_memory(memory, self.memory_path)

        result = acknowledge_review_update("job:1", self.memory_path)
        entry = load_memory(self.memory_path)["job:1"]

        self.assertEqual(result["workflow_status"], "interesting")
        self.assertFalse(result["review_update_pending"])
        self.assertEqual(entry["workflow_status"], "interesting")
        self.assertFalse(entry["review_update_pending"])

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
        with self.server_context() as base_url:
            for route, marker in [
                ("/", 'id="manual-import-form"'),
                ("/review", 'id="application-dialog"'),
                ("/applications", 'id="completed-applications"'),
            ]:
                with self.subTest(route=route), urlopen(base_url + route) as response:
                    page = response.read().decode("utf-8")
                    self.assertIn(marker, page)
                    self.assertIn('href="/app.css?v=2"', page)
                    self.assertIn('src="/app.js"', page)
                    self.assertNotIn("<style", page)
                    self.assertNotIn("style=", page)
                    self.assertEqual(response.headers["Cache-Control"], "no-store")
                    self.assertIn("frame-ancestors 'none'", response.headers["Content-Security-Policy"])
            with urlopen(base_url + "/review?job=job%3A1") as response:
                targeted = response.read()
            self.assertEqual(targeted, REVIEW_PAGE.read_bytes())

    def test_shared_assets_are_served_with_correct_types(self):
        with self.server_context() as base_url:
            for route, content_type, path in [
                ("/app.css", "text/css", APP_STYLES),
                ("/app.js", "text/javascript", APP_SCRIPT),
            ]:
                with self.subTest(route=route), urlopen(base_url + route) as response:
                    self.assertIn(content_type, response.headers["Content-Type"])
                    self.assertEqual(response.read(), path.read_bytes())

    def test_manual_import_api_forwards_paths_and_url(self):
        calls = []

        def importer(url, **paths):
            calls.append((url, paths))
            return {"job_id": "manual:python", "analyzed": 1}

        with self.server_context(
            jobs_path=self.directory / 'jobs.json',
            manual_cache_path=self.directory / 'manual.json',
            manual_importer=staticmethod(importer),
        ) as base_url:
            request = Request(
                f"{base_url}/api/manual-import",
                data=json.dumps(
                    {"url": "https://example.com/jobs/python"}
                ).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urlopen(request) as response:
                result = json.load(response)

        self.assertEqual(result["job_id"], "manual:python")
        self.assertEqual(calls[0][0], "https://example.com/jobs/python")
        self.assertEqual(calls[0][1]["memory_path"], self.memory_path)

    def test_application_start_api_adds_job_to_overview(self):
        with self.server_context() as base_url:
            request = Request(
                f"{base_url}/api/applications",
                data=json.dumps(
                    {"job_id": "job:1", "salary_expectation_eur": 58_000}
                ).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urlopen(request) as response:
                result = json.load(response)
            with urlopen(f"{base_url}/api/applications") as response:
                overview = json.load(response)

        self.assertEqual(result["workflow_status"], "applied")
        self.assertTrue(result["application_tracked"])
        self.assertEqual(overview["statistics"]["total"], 1)
        self.assertEqual(overview["applications"][0]["id"], "job:1")
        self.assertEqual(
            overview["applications"][0]["salary_expectation_eur"],
            58_000,
        )

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
        query = urlencode({"job_id": "job:1", "document_id": document["id"]})
        with self.server_context(
            application_documents_dir=documents_directory,
        ) as base_url:
            with urlopen(
                f"{base_url}"
                f"/api/application-document?{query}"
            ) as response:
                content = response.read()
                disposition = response.headers["Content-Disposition"]

        self.assertEqual(content, b"%PDF resume")
        self.assertIn("Lebenslauf.pdf", disposition)


    def test_application_can_store_optional_salary_expectation(self):
        result = start_application(
            "job:1",
            self.memory_path,
            salary_expectation_eur=58_000,
        )

        entry = load_memory(self.memory_path)["job:1"]
        self.assertTrue(result["application_tracked"])
        self.assertEqual(entry["salary_expectation_eur"], 58_000)
        self.assertNotIn("salary_expectation", entry)

    def test_salary_expectation_rejects_non_numeric_input(self):
        with self.assertRaisesRegex(ValueError, "ganze Zahl"):
            start_application(
                "job:1",
                self.memory_path,
                salary_expectation_eur="58.000 Euro",
            )

        self.assertNotIn(
            "salary_expectation_eur",
            load_memory(self.memory_path)["job:1"],
        )

    def test_salary_expectation_rejects_non_positive_input(self):
        with self.assertRaisesRegex(ValueError, "gültigen Bereich"):
            start_application(
                "job:1",
                self.memory_path,
                salary_expectation_eur=0,
            )

    def test_local_api_loads_jobs_and_persists_status(self):
        with self.server_context() as base_url:
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
        with self.server_context() as base_url:
            request = Request(
                f"{base_url}/api/recommendations",
                headers={"Host": "attacker.example"},
            )
            with self.assertRaises(HTTPError) as caught:
                urlopen(request)

        self.assertEqual(caught.exception.code, 403)
        caught.exception.close()

    def test_application_page_records_dated_event_and_returns_statistics(self):
        event_on = date.today().isoformat()
        with self.server_context() as base_url:
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


    def test_cross_origin_and_non_json_mutations_are_rejected(self):
        before = load_memory(self.memory_path)
        with self.server_context() as base_url:
            for headers, code in [
                ({"Content-Type": "application/json", "Origin": "https://attacker.example"}, 403),
                ({"Content-Type": "text/plain"}, 415),
            ]:
                request = Request(base_url + "/api/review-status", method="POST",
                    data=b'{"job_id":"job:1","workflow_status":"ignored"}', headers=headers)
                with self.subTest(headers=headers), self.assertRaises(HTTPError) as caught:
                    urlopen(request)
                self.assertEqual(caught.exception.code, code)
                caught.exception.close()
        self.assertEqual(load_memory(self.memory_path), before)

    def test_database_failure_removes_new_application_documents(self):
        before = load_memory(self.memory_path)
        root = self.directory / "documents"
        with unittest.mock.patch("job_finder.memory.replace_sqlite_memory", side_effect=OSError("commit failed")):
            with self.assertRaises(OSError):
                start_application("job:1", self.memory_path, [{
                    "kind": "resume", "name": "CV.pdf",
                    "content": base64.b64encode(b"test document").decode("ascii"),
                }], root)
        self.assertEqual(load_memory(self.memory_path), before)
        self.assertEqual([p for p in root.rglob("*") if p.is_file()], [])

if __name__ == "__main__":
    unittest.main()
