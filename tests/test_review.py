"""Tests for the local recommendation review workflow."""

import base64
import http.client
import json
import socket
import tempfile
import threading
import unittest
from contextlib import contextmanager
from datetime import date
from http.server import HTTPServer
from pathlib import Path
from unittest import mock
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from job_finder.matching.config import LOCAL_SEARCH_LOCATION, LOCAL_SEARCH_POSTAL_CODE
from job_finder.review import (
    MAX_REQUEST_BYTES,
    PACKAGE,
    LocalReviewServer,
    ReviewRequestHandler,
    address_is_in_use,
    load_review_jobs,
    start_application,
    undo_ignored_decision,
    update_application_salary,
    update_review_decision,
    update_review_note,
    update_workflow_status,
)
from job_finder.workflow.memory import load_memory, save_memory
from job_finder.workflow.review_data import company_applications, same_company_applications


def json_request(url, payload):
    return Request(
        url, data=json.dumps(payload).encode("utf-8"), headers={"Content-Type": "application/json"}, method="POST"
    )


def post_json(url, payload):
    with urlopen(json_request(url, payload)) as response:
        return json.load(response)


def get_json(url):
    with urlopen(url) as response:
        return json.load(response)


def upload(kind, name, content):
    """One browser upload as the review sends it: raw bytes base64-encoded."""
    return {"kind": kind, "name": name, "content": base64.b64encode(content).decode("ascii")}


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
        server.socket = mock.Mock()

        with mock.patch.object(HTTPServer, "server_bind") as parent_bind:
            server.server_bind()

        server.socket.setsockopt.assert_called_once_with(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        parent_bind.assert_called_once_with()

    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary_directory.name)
        self.memory_path = self.directory / "state.sqlite3"
        self.recommendations_path = self.directory / "recommendations.json"
        save_memory(
            {"job:1": {"title": "Python Developer", "company": "Example GmbH", "workflow_status": "interesting"}},
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

    def edit_recommendation(self, **fields):
        document = json.loads(self.recommendations_path.read_text(encoding="utf-8"))
        document["recommendations"][0].update(fields)
        self.recommendations_path.write_text(json.dumps(document), encoding="utf-8")

    @contextmanager
    def server_context(self, **attributes):
        handler = type(
            "TemporaryReviewHandler",
            (ReviewRequestHandler,),
            {"recommendations_path": self.recommendations_path, "memory_path": self.memory_path, **attributes},
        )
        server = LocalReviewServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        try:
            yield f"http://127.0.0.1:{server.server_port}"
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_idle_connection_does_not_block_other_requests(self):
        with self.server_context() as base_url:
            idle = socket.create_connection(("127.0.0.1", int(base_url.rsplit(":", 1)[1])))
            try:
                with urlopen(base_url + "/", timeout=5) as response:
                    self.assertEqual(response.status, 200)
            finally:
                idle.close()

    def test_json_actions_reject_non_object_payloads_without_changing_memory(self):
        original = load_memory(self.memory_path)
        routes = (
            "/api/status",
            "/api/applications",
            "/api/application-salary",
            "/api/review-status",
            "/api/review-undo",
            "/api/history",
            "/api/history/delete",
            "/api/manual-import",
        )
        with self.server_context() as base_url:
            for route in routes:
                for payload in (None, [], "text", 1):
                    with self.subTest(route=route, payload=payload):
                        request = json_request(base_url + route, payload)
                        with self.assertRaises(HTTPError) as raised:
                            urlopen(request)
                        self.assertEqual(raised.exception.code, 400)
                        result = json.loads(raised.exception.read())
                        self.assertEqual(result["error"], "JSON-Objekt erforderlich")
        self.assertEqual(load_memory(self.memory_path), original)

    def test_review_routes_ignore_and_undo_the_same_job(self):
        with self.server_context() as base_url:
            for route, fields, expected in (
                ("/api/review-status", {"workflow_status": "ignored"}, "ignored"),
                ("/api/review-undo", {"expected_status": "ignored"}, "interesting"),
            ):
                result = post_json(base_url + route, {"job_id": "job:1", **fields})
                self.assertEqual(result["workflow_status"], expected)
                self.assertFalse(result["application_tracked"])
                self.assertEqual(load_memory(self.memory_path)["job:1"]["workflow_status"], expected)

    def test_review_jobs_include_persisted_workflow_status(self):
        jobs = load_review_jobs(self.recommendations_path, self.memory_path)

        self.assertEqual(jobs[0]["workflow_status"], "interesting")
        self.assertFalse(jobs[0]["application_tracked"])
        self.assertFalse(jobs[0]["international"])

    def test_reviewed_new_job_does_not_reappear_after_reload(self):
        self.edit_recommendation(is_new=True)

        jobs = load_review_jobs(self.recommendations_path, self.memory_path)

        self.assertEqual(jobs[0]["workflow_status"], "interesting")
        self.assertFalse(jobs[0]["is_new"])

    def test_unreviewed_new_job_remains_visible_after_reload(self):
        memory = load_memory(self.memory_path)
        memory["job:1"]["workflow_status"] = "new"
        save_memory(memory, self.memory_path)
        self.edit_recommendation(is_new=True)

        jobs = load_review_jobs(self.recommendations_path, self.memory_path)

        self.assertTrue(jobs[0]["is_new"])

    def test_active_interesting_job_survives_one_missed_source_run(self):
        self.recommendations_path.write_text(json.dumps({"recommendations": []}), encoding="utf-8")
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
        self.assertEqual(jobs[0]["source_links"], [{"source": "studysmarter", "url": "https://example.test/job"}])

    def test_inactive_interesting_job_remains_with_availability_warning(self):
        self.recommendations_path.write_text(json.dumps({"recommendations": []}), encoding="utf-8")
        memory = load_memory(self.memory_path)
        memory["job:1"]["active"] = False
        save_memory(memory, self.memory_path)

        jobs = load_review_jobs(self.recommendations_path, self.memory_path)

        self.assertEqual(len(jobs), 1)
        self.assertIn("mehreren vollständigen Läufen", jobs[0]["prefilter_warning"])

    def test_merged_recommendation_does_not_restore_a_source_alias(self):
        self.edit_recommendation(
            source_links=[
                {"source": "first", "url": "https://example.test/first"},
                {"source": "second", "url": "https://example.test/second"},
            ]
        )
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

    def test_recommendations_of_one_remembered_job_show_as_one_card(self):
        # As before both runs have published a job again that memory now knows as one.
        document = json.loads(self.recommendations_path.read_text(encoding="utf-8"))
        azure = document["recommendations"][0]
        azure.update(locations=["Würzburg"], source_links=[{"source": "arbeitnow", "url": "https://arbeitnow.test/1"}])
        local = {
            **azure,
            "id": "remotely:1",
            "match_percent": 70,
            "locations": ["Remote"],
            "international": True,
            "source_links": [{"source": "remotely", "url": "https://remotely.test/1"}],
        }
        document["recommendations"].append(local)
        self.recommendations_path.write_text(json.dumps(document), encoding="utf-8")
        memory = load_memory(self.memory_path)
        memory["job:1"]["source_urls"] = ["https://arbeitnow.test/1", "https://remotely.test/1"]
        save_memory(memory, self.memory_path)

        jobs = load_review_jobs(self.recommendations_path, self.memory_path)

        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["recommendation_id"], "job:1")
        self.assertEqual(
            [link["url"] for link in jobs[0]["source_links"]], ["https://arbeitnow.test/1", "https://remotely.test/1"]
        )
        self.assertEqual(jobs[0]["locations"], ["Würzburg", "Remote"])
        self.assertFalse(jobs[0]["international"])

    def test_review_jobs_classify_legacy_international_recommendation(self):
        self.edit_recommendation(locations=["weltweit"])

        jobs = load_review_jobs(self.recommendations_path, self.memory_path)

        self.assertTrue(jobs[0]["international"])

    def test_review_jobs_reclassify_stale_multicountry_recommendation(self):
        self.edit_recommendation(
            locations=["Canada", "Germany", "United States"],
            source_links=[{"source": "himalayas", "url": "https://example.test/job"}],
            international=False,
        )

        jobs = load_review_jobs(self.recommendations_path, self.memory_path)

        self.assertTrue(jobs[0]["international"])

    def test_review_jobs_preserve_stored_international_language_classification(self):
        self.edit_recommendation(
            locations=["Germany"],
            source_links=[{"source": "jobicy", "url": "https://example.test/job"}],
            international=True,
        )

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
        self.assertEqual(jobs[0]["source_links"], [{"source": "stepstone", "url": "https://portal.test/job"}])

    def test_application_wins_over_exact_review_entry_with_same_url(self):
        memory = load_memory(self.memory_path)
        memory["job:1"].update({"workflow_status": "interesting", "source_urls": ["https://portal.test/job"]})
        memory["portal:applied"] = {
            "workflow_status": "applied",
            "workflow_history": [{"status": "applied", "occurred_on": "2026-08-12"}],
            "source_urls": ["https://portal.test/job"],
            "source_names": ["arbeitsagentur", "test"],
        }
        save_memory(memory, self.memory_path)
        self.edit_recommendation(
            url="https://portal.test/job", source_links=[{"source": "test", "url": "https://portal.test/job"}]
        )

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
        applied_events = [event for event in entry["workflow_history"] if event["status"] == "applied"]
        self.assertEqual(result["workflow_status"], "applied")
        self.assertTrue(result["application_tracked"])
        self.assertEqual(repeated_result, result)
        self.assertEqual(applied_events, [{"status": "applied", "occurred_on": date.today().isoformat()}])

    def test_start_application_archives_supplied_documents(self):
        documents_directory = self.directory / "application_documents"

        start_application(
            "job:1",
            self.memory_path,
            [upload("cover_letter", "Anschreiben.pdf", b"%PDF application")],
            documents_directory,
        )

        document = load_memory(self.memory_path)["job:1"]["application_documents"][0]
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

        result = update_review_decision("job:1", "ignored", self.memory_path)

        entry = load_memory(self.memory_path)["job:1"]
        self.assertEqual(result["workflow_status"], "interview")
        self.assertTrue(result["application_tracked"])
        self.assertEqual(entry["workflow_status"], "interview")
        self.assertEqual(len(entry["workflow_history"]), 2)

    def test_start_application_rejects_unknown_job(self):
        with self.assertRaisesRegex(KeyError, "Unbekannte Job-ID"):
            start_application("job:unknown", self.memory_path)

    def test_inquiry_and_waiting_are_persisted_as_review_decisions(self):
        for status in ("inquiry", "waiting"):
            with self.subTest(status=status):
                result = update_review_decision("job:1", status, self.memory_path)

                self.assertEqual(result["workflow_status"], status)
                self.assertEqual(load_memory(self.memory_path)["job:1"]["workflow_status"], status)

    def test_review_note_is_saved_trimmed_shown_and_removable(self):
        result = update_review_note("job:1", "  Java-Pflicht, will ich nicht  ", self.memory_path)

        self.assertEqual(result, {"review_note": "Java-Pflicht, will ich nicht"})
        self.assertEqual(load_memory(self.memory_path)["job:1"]["workflow_status"], "interesting")
        jobs = load_review_jobs(self.recommendations_path, self.memory_path)
        self.assertEqual(jobs[0]["review_note"], "Java-Pflicht, will ich nicht")

        update_review_note("job:1", "   ", self.memory_path)

        self.assertNotIn("review_note", load_memory(self.memory_path)["job:1"])

    def test_invalid_review_note_changes_nothing(self):
        for note in (None, 42, "x" * 2001):
            label = f"{len(note)} Zeichen" if isinstance(note, str) else note
            with self.subTest(note=label), self.assertRaises(ValueError):
                update_review_note("job:1", note, self.memory_path)
        self.assertNotIn("review_note", load_memory(self.memory_path)["job:1"])

    def test_latest_ignored_decision_can_be_undone(self):
        update_review_decision("job:1", "ignored", self.memory_path)

        result = undo_ignored_decision("job:1", "ignored", self.memory_path)

        entry = load_memory(self.memory_path)["job:1"]
        self.assertEqual(result["workflow_status"], "interesting")
        self.assertEqual(entry["workflow_status"], "interesting")
        self.assertEqual(entry["workflow_history"], [{"status": "interesting", "occurred_on": None}])

    def test_ignored_undo_rejects_a_changed_decision(self):
        update_review_decision("job:1", "ignored", self.memory_path)
        update_review_decision("job:1", "inquiry", self.memory_path)

        with self.assertRaisesRegex(ValueError, "zwischenzeitlich"):
            undo_ignored_decision("job:1", "ignored", self.memory_path)

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
                    self.assertIn('href="/app.css?v=6"', page)
                    self.assertIn('src="/app.js"', page)
                    self.assertNotIn("<style", page)
                    self.assertNotIn("style=", page)
                    self.assertNotIn("<script>", page)
                    self.assertEqual(response.headers["Cache-Control"], "no-store")
                    policy = response.headers["Content-Security-Policy"]
                    self.assertIn("frame-ancestors 'none'", policy)
                    self.assertIn("script-src 'self';", policy)
                    self.assertNotIn("unsafe-inline", policy)
            with urlopen(base_url + "/review?job=job%3A1") as response:
                targeted = response.read()
            self.assertEqual(targeted, (PACKAGE / "review.html").read_bytes())

    def test_shared_assets_are_served_with_correct_types(self):
        with self.server_context() as base_url:
            for route, content_type, path in [
                ("/app.css", "text/css", "app.css"),
                ("/app.js", "text/javascript", "app.js"),
                ("/landing.js", "text/javascript", "landing.js"),
                ("/review.js", "text/javascript", "review.js"),
                ("/applications.js", "text/javascript", "applications.js"),
            ]:
                with self.subTest(route=route), urlopen(base_url + route) as response:
                    self.assertIn(content_type, response.headers["Content-Type"])
                    self.assertEqual(response.read(), (PACKAGE / path).read_bytes())

    def test_manual_import_api_forwards_paths_and_url(self):
        calls = []

        def importer(url, **paths):
            calls.append((url, paths))
            return {"job_id": "manual:python", "analyzed": 1}

        with self.server_context(
            jobs_path=self.directory / "jobs.json",
            manual_cache_path=self.directory / "manual.json",
            manual_importer=staticmethod(importer),
        ) as base_url:
            result = post_json(f"{base_url}/api/manual-import", {"url": "https://example.com/jobs/python"})

        self.assertEqual(result["job_id"], "manual:python")
        self.assertEqual(calls[0][0], "https://example.com/jobs/python")
        self.assertEqual(calls[0][1]["memory_path"], self.memory_path)

    def test_application_start_api_adds_job_to_overview(self):
        with self.server_context() as base_url:
            result = post_json(f"{base_url}/api/applications", {"job_id": "job:1", "salary_expectation_eur": 58000})
            overview = get_json(f"{base_url}/api/applications")

        self.assertEqual(result["workflow_status"], "applied")
        self.assertTrue(result["application_tracked"])
        self.assertEqual(overview["statistics"]["total"], 1)
        self.assertEqual(overview["applications"][0]["id"], "job:1")
        self.assertEqual(overview["applications"][0]["salary_expectation_eur"], 58_000)

    def test_application_document_can_be_downloaded_from_overview_link(self):
        documents_directory = self.directory / "application_documents"
        start_application(
            "job:1", self.memory_path, [upload("resume", "Lebenslauf.pdf", b"%PDF resume")], documents_directory
        )
        document = load_memory(self.memory_path)["job:1"]["application_documents"][0]
        query = urlencode({"job_id": "job:1", "document_id": document["id"]})
        with (
            self.server_context(application_documents_dir=documents_directory) as base_url,
            urlopen(f"{base_url}/api/application-document?{query}") as response,
        ):
            content = response.read()
            disposition = response.headers["Content-Disposition"]

        self.assertEqual(content, b"%PDF resume")
        self.assertIn("Lebenslauf.pdf", disposition)

    def test_a_document_is_served_only_for_its_own_job(self):
        documents_directory = self.directory / "application_documents"
        start_application(
            "job:1", self.memory_path, [upload("resume", "Lebenslauf.pdf", b"%PDF resume")], documents_directory
        )
        memory = load_memory(self.memory_path)
        memory["job:2"] = {"title": "Cloud Engineer", "company": "Andere GmbH", "workflow_status": "applied"}
        save_memory(memory, self.memory_path)
        document_id = memory["job:1"]["application_documents"][0]["id"]

        with self.server_context(application_documents_dir=documents_directory) as base_url:
            for job_id, wanted in (("job:2", document_id), ("job:1", "unbekannt"), ("job:9", document_id)):
                query = urlencode({"job_id": job_id, "document_id": wanted})
                with self.subTest(job_id=job_id), self.assertRaises(HTTPError) as caught:
                    urlopen(f"{base_url}/api/application-document?{query}")
                self.assertEqual(caught.exception.code, 404)
                caught.exception.close()

    def test_an_oversized_request_is_refused_before_its_body_is_read(self):
        before = load_memory(self.memory_path)
        with self.server_context() as base_url:
            connection = http.client.HTTPConnection("127.0.0.1", int(base_url.rsplit(":", 1)[1]), timeout=5)
            # Only the announced size counts: the server must answer without waiting for 46 MB.
            connection.putrequest("POST", "/api/review-status")
            connection.putheader("Content-Type", "application/json")
            connection.putheader("Content-Length", str(MAX_REQUEST_BYTES + 1))
            connection.endheaders()
            response = connection.getresponse()
            body = json.loads(response.read())
            connection.close()

        self.assertEqual(response.status, 400)
        self.assertIn("zu groß", body["error"])
        self.assertEqual(load_memory(self.memory_path), before)

    def test_monthly_salary_and_edits_preserve_application_history(self):
        start_application("job:1", self.memory_path, salary_expectation_eur=4500, salary_period="month")
        original = load_memory(self.memory_path)["job:1"]
        self.assertEqual(original["salary_expectation_eur"], 54000)
        with self.server_context() as base_url:
            result = post_json(
                base_url + "/api/application-salary",
                {"job_id": "job:1", "salary_expectation_eur": 5000, "salary_period": "month"},
            )
        self.assertEqual(result["salary_expectation_eur"], 60000)
        updated = load_memory(self.memory_path)["job:1"]
        self.assertEqual(updated["workflow_history"], original["workflow_history"])
        for value, period in [(900000, "month"), (0, "year"), (True, "month"), (4500, "week")]:
            with self.subTest(value=value, period=period), self.assertRaises(ValueError):
                update_application_salary("job:1", value, period, self.memory_path)
            self.assertEqual(load_memory(self.memory_path)["job:1"], updated)
        update_application_salary("job:1", None, memory_path=self.memory_path)
        self.assertNotIn("salary_expectation_eur", load_memory(self.memory_path)["job:1"])

    def test_application_can_store_optional_salary_expectation(self):
        result = start_application("job:1", self.memory_path, salary_expectation_eur=58_000)

        entry = load_memory(self.memory_path)["job:1"]
        self.assertTrue(result["application_tracked"])
        self.assertEqual(entry["salary_expectation_eur"], 58_000)
        self.assertNotIn("salary_expectation", entry)

    def test_salary_expectation_rejects_non_numeric_input(self):
        with self.assertRaisesRegex(ValueError, "ganze Zahl"):
            start_application("job:1", self.memory_path, salary_expectation_eur="58.000 Euro")

        self.assertNotIn("salary_expectation_eur", load_memory(self.memory_path)["job:1"])

    def test_salary_expectation_rejects_non_positive_input(self):
        with self.assertRaisesRegex(ValueError, "gültigen Bereich"):
            start_application("job:1", self.memory_path, salary_expectation_eur=0)

    def test_local_api_loads_jobs_and_persists_status(self):
        with self.server_context() as base_url:
            document = get_json(f"{base_url}/api/recommendations")
            result = post_json(f"{base_url}/api/review-status", {"job_id": "job:1", "workflow_status": "ignored"})

        self.assertEqual(document["recommendations"][0]["workflow_status"], "interesting")
        self.assertEqual(document["route_origin"], f"{LOCAL_SEARCH_POSTAL_CODE} {LOCAL_SEARCH_LOCATION}")
        self.assertEqual(result["workflow_status"], "ignored")
        self.assertNotIn("personal_ratings", document)
        jobs = load_review_jobs(self.recommendations_path, self.memory_path)
        self.assertEqual(jobs[0]["workflow_status"], "ignored")

    def test_local_api_saves_a_review_note(self):
        with self.server_context() as base_url:
            result = post_json(f"{base_url}/api/review-note", {"job_id": "job:1", "review_note": "Gute Firma"})
            document = get_json(f"{base_url}/api/recommendations")

        self.assertEqual(result, {"review_note": "Gute Firma"})
        self.assertEqual(document["recommendations"][0]["review_note"], "Gute Firma")

    def test_local_api_rejects_dns_rebinding_host(self):
        with self.server_context() as base_url:
            request = Request(f"{base_url}/api/recommendations", headers={"Host": "attacker.example"})
            with self.assertRaises(HTTPError) as caught:
                urlopen(request)

        self.assertEqual(caught.exception.code, 403)
        caught.exception.close()

    def test_application_page_records_dated_event_and_returns_statistics(self):
        event_on = date.today().isoformat()
        with self.server_context() as base_url:
            with urlopen(f"{base_url}/applications") as response:
                page = response.read().decode("utf-8")
            request = json_request(
                f"{base_url}/api/status", {"job_id": "job:1", "workflow_status": "applied", "occurred_on": event_on}
            )
            with urlopen(request):
                pass
            no_response_request = json_request(
                f"{base_url}/api/status", {"job_id": "job:1", "workflow_status": "no_response", "occurred_on": event_on}
            )
            with urlopen(no_response_request):
                pass
            overview = get_json(f"{base_url}/api/applications")
            no_response_event = next(
                event
                for event in overview["completed_applications"][0]["workflow_history"]
                if event["status"] == "no_response"
            )
            edit_result = post_json(
                f"{base_url}/api/history",
                {
                    "job_id": "job:1",
                    "event_index": no_response_event["event_index"],
                    "previous_status": "no_response",
                    "previous_occurred_on": event_on,
                    "workflow_status": "response",
                    "occurred_on": event_on,
                },
            )
            delete_result = post_json(
                f"{base_url}/api/history/delete",
                {
                    "job_id": "job:1",
                    "event_index": no_response_event["event_index"],
                    "previous_status": "response",
                    "previous_occurred_on": event_on,
                },
            )
            final_overview = get_json(f"{base_url}/api/applications")
            interview_request = json_request(
                f"{base_url}/api/status",
                {
                    "job_id": "job:1",
                    "workflow_status": "interview",
                    "occurred_on": event_on,
                    "scheduled_for": "2099-08-25T10:30",
                },
            )
            with urlopen(interview_request):
                pass
            interview_overview = get_json(f"{base_url}/api/applications")

        self.assertIn("Bewerbungsübersicht", page)
        self.assertIn("Abgeschlossene Bewerbungen bearbeiten", page)
        script = (PACKAGE / "applications.js").read_text(encoding="utf-8")
        self.assertIn('input.type = "datetime-local"', script)
        self.assertIn("Nächstes Gespräch", script)
        self.assertEqual(overview["statistics"]["total"], 1)
        self.assertEqual(overview["applications"], [])
        self.assertEqual(overview["completed_applications"][0]["applied_on"], event_on)
        self.assertEqual(edit_result["workflow_status"], "response")
        self.assertEqual(delete_result["workflow_status"], "applied")
        self.assertEqual(interview_overview["applications"][0]["next_interview_at"], "2099-08-25T10:30")
        self.assertEqual(final_overview["statistics"]["open"], 1)

    def test_cross_origin_and_non_json_mutations_are_rejected(self):
        before = load_memory(self.memory_path)
        with self.server_context() as base_url:
            for headers, code in [
                ({"Content-Type": "application/json", "Origin": "https://attacker.example"}, 403),
                ({"Content-Type": "text/plain"}, 415),
            ]:
                request = Request(
                    base_url + "/api/review-status",
                    method="POST",
                    data=b'{"job_id":"job:1","workflow_status":"ignored"}',
                    headers=headers,
                )
                with self.subTest(headers=headers), self.assertRaises(HTTPError) as caught:
                    urlopen(request)
                self.assertEqual(caught.exception.code, code)
                caught.exception.close()
        self.assertEqual(load_memory(self.memory_path), before)

    def test_database_failure_removes_new_application_documents(self):
        before = load_memory(self.memory_path)
        root = self.directory / "documents"
        with (
            mock.patch("job_finder.workflow.memory.write_memory", side_effect=OSError("commit failed")),
            self.assertRaises(OSError),
        ):
            start_application("job:1", self.memory_path, [upload("resume", "CV.pdf", b"test document")], root)
        self.assertEqual(load_memory(self.memory_path), before)
        self.assertEqual([p for p in root.rglob("*") if p.is_file()], [])


class CompanyApplicationTests(unittest.TestCase):
    """The review's hint on applications at the same company, without a database."""

    def test_the_review_names_applications_at_the_same_company(self):
        memory = {
            "job:applied": {
                "title": "Cloud Engineer",
                "company": "Nordlicht Systems GmbH",
                "workflow_status": "applied",
                "workflow_history": [{"status": "applied", "occurred_on": "2026-09-28"}],
            },
            "job:silent": {
                "title": "DevOps Engineer",
                "company": "Nordlicht Systems",
                "workflow_status": "applied",
                "workflow_history": [{"status": "applied", "occurred_on": "2026-09-01"}],
            },
            "job:other": {"title": "Data Engineer", "company": "Datenweber GmbH", "workflow_status": "interview"},
            "job:unnamed": {"title": "Admin", "workflow_status": "applied"},
        }
        applications = company_applications(memory, as_of=date(2026, 10, 1))

        found = same_company_applications({"id": "job:new", "company": "Nordlicht Systems AG"}, applications)

        # Open first; a quiet application counts as no response after 14 days, as on the applications page.
        self.assertEqual(
            found,
            [
                {"title": "Cloud Engineer", "workflow_status": "applied", "open": True},
                {"title": "DevOps Engineer", "workflow_status": "no_response", "open": False},
            ],
        )
        self.assertEqual(
            same_company_applications({"id": "job:applied", "company": "Datenweber"}, applications)[0]["open"], True
        )
        self.assertEqual(same_company_applications({"id": "job:new", "company": ""}, applications), [])
