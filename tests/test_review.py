"""Tests for the local recommendation review workflow."""

import base64
import http.client
import io
import json
import socket
import tempfile
import threading
import unittest
from contextlib import contextmanager, redirect_stdout
from copy import deepcopy
from datetime import date
from html.parser import HTMLParser
from pathlib import Path
from unittest import mock
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import psycopg
import uvicorn
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from job_finder.matching.config import route_origin
from job_finder.review import address_is_in_use, bind_exclusively
from job_finder.review_app import MAX_JSON_BYTES, PACKAGE, ReviewPaths, create_app
from job_finder.sources.registry import SOURCE_LABELS
from job_finder.workflow.memory import load_memory, load_review_memory, save_memory
from job_finder.workflow.review_actions import (
    RERUN_FIELD,
    start_application,
    undo_ignored_decision,
    update_application_salary,
    update_review_decision,
    update_review_note,
    update_workflow_status,
)
from job_finder.workflow.review_data import company_applications, load_review_jobs, same_company_applications


def span_recorder():
    """Record the spans of this test process in memory, once for all tests."""
    global SPANS
    if SPANS is None:
        SPANS = InMemorySpanExporter()
        provider = trace.get_tracer_provider()
        if not isinstance(provider, TracerProvider):
            provider = TracerProvider()
            trace.set_tracer_provider(provider)
        provider.add_span_processor(SimpleSpanProcessor(SPANS))
    return SPANS


SPANS = None


class PageTags(HTMLParser):
    """Collect every linked file of a page and whether it has inline script, as the browser parses it."""

    def __init__(self):
        super().__init__()
        self.links = []
        self.inline_script = False
        self._in_script = False

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        self.links += [(attributes[key] or "", attributes) for key in ("src", "href") if key in attributes]
        self._in_script = tag == "script"

    def handle_endtag(self, tag):
        self._in_script = False

    def handle_data(self, data):
        self.inline_script = self.inline_script or (self._in_script and bool(data.strip()))


def json_request(url, payload):
    return Request(
        url, data=json.dumps(payload).encode("utf-8"), headers={"Content-Type": "application/json"}, method="POST"
    )


def post_json(url, payload):
    with urlopen(json_request(url, payload)) as response:
        return json.load(response)


def form_request(url, fields, files=(), headers=None):
    """A form upload as the review sends it: fields, files and the review's own header."""
    boundary = "jobfinder-test-boundary"
    parts = [
        f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode()
        for name, value in fields.items()
    ]
    for name, filename, content in files:
        head = f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'
        parts.append(head.encode() + b"Content-Type: application/octet-stream\r\n\r\n" + content + b"\r\n")
    body = b"".join(parts) + f"--{boundary}--\r\n".encode()
    own = {"Content-Type": f"multipart/form-data; boundary={boundary}", "X-Jobfinder-Upload": "1"}
    origin = url.split("/api/", 1)[0]
    return Request(url, data=body, headers={**own, "Origin": origin, **(headers or {})}, method="POST")


def post_form(url, fields, files=()):
    with urlopen(form_request(url, fields, files)) as response:
        return json.load(response)


def rejection_status(request):
    """Return the status the review rejects a request with, or "reset".

    The review may answer an oversized body before reading it and close the
    connection; the client can then see the reset before the answer.
    """
    try:
        urlopen(request).close()
    except HTTPError as error:
        error.close()
        return error.code
    except URLError as error:
        if isinstance(error.reason, ConnectionResetError):
            return "reset"
        raise
    raise AssertionError("request was accepted")


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

    def test_review_port_is_bound_exclusively_on_windows(self):
        if not hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.skipTest("SO_EXCLUSIVEADDRUSE is Windows-specific")
        sock = bind_exclusively("127.0.0.1", 0)
        try:
            self.assertEqual(sock.getsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE), 1)
            with self.assertRaises(OSError) as second:
                bind_exclusively("127.0.0.1", sock.getsockname()[1])
            self.assertTrue(address_is_in_use(second.exception))
        finally:
            sock.close()

    def test_the_database_sign_in_is_prepared_at_start_and_a_failure_only_logs(self):
        from job_finder import review

        output = io.StringIO()
        with redirect_stdout(output):
            review.warm_up_database()
            with mock.patch.object(review, "transaction", side_effect=psycopg.OperationalError("secret dsn")):
                review.warm_up_database()

        self.assertIn("Datenbankanmeldung vorbereitet", output.getvalue())
        self.assertIn("Datenbank-Vorbereitung fehlgeschlagen: OperationalError", output.getvalue())
        self.assertNotIn("secret", output.getvalue())

    def test_source_names_come_from_the_registry(self):
        with self.server_context() as base_url:
            labels = get_json(base_url + "/api/sources")["labels"]
        self.assertEqual(labels, SOURCE_LABELS)

    def test_read_endpoints_publish_their_response_shapes(self):
        with self.server_context() as base_url:
            schema = get_json(base_url + "/openapi.json")
            card = get_json(f"{base_url}/api/recommendations")["recommendations"][0]

        shapes = {
            "/api/recommendations": "RecommendationsResponse",
            "/api/applications": "ApplicationsResponse",
            "/api/runs": "RunsResponse",
            "/api/sources": "SourcesResponse",
        }
        for path, name in shapes.items():
            with self.subTest(path):
                content = schema["paths"][path]["get"]["responses"]["200"]["content"]["application/json"]
                self.assertEqual(content["schema"]["$ref"], f"#/components/schemas/{name}")
        card_fields = schema["components"]["schemas"]["ReviewCard"]["properties"]
        self.assertIn("fact_sheet", card_fields)
        # A card without a fact sheet lacks the field instead of carrying null.
        self.assertNotIn("fact_sheet", card)

    def test_the_api_page_loads_only_two_pinned_swagger_files(self):
        with self.server_context() as base_url:
            with urlopen(base_url + "/docs") as response:
                page = response.read().decode("utf-8")
                policy = response.headers["Content-Security-Policy"]
            with urlopen(base_url + "/review") as response:
                strict = response.headers["Content-Security-Policy"]
            schema = get_json(base_url + "/openapi.json")

        tags = PageTags()
        tags.feed(page)
        external = [(url, attributes) for url, attributes in tags.links if not url.startswith("/")]
        self.assertEqual(len(external), 2)
        for url, attributes in external:
            self.assertRegex(attributes.get("integrity") or "", r"^sha384-[A-Za-z0-9+/]{64}$")
            self.assertEqual(attributes.get("crossorigin"), "anonymous")
            self.assertIn(url, policy)
        self.assertFalse(tags.inline_script)
        self.assertNotIn("unsafe-inline", policy)
        self.assertNotIn("cdn.jsdelivr.net", strict)
        self.assertIn("/api/review-note", schema["paths"])

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
        paths = ReviewPaths(
            recommendations=attributes.get("recommendations_path", self.recommendations_path),
            memory=attributes.get("memory_path", self.memory_path),
            jobs=attributes.get("jobs_path", ReviewPaths.jobs),
            manual_cache=attributes.get("manual_cache_path", ReviewPaths.manual_cache),
            documents=attributes.get("application_documents_dir", ReviewPaths.documents),
        )
        options = {"manual_importer": attributes["manual_importer"]} if "manual_importer" in attributes else {}
        server = uvicorn.Server(
            uvicorn.Config(
                create_app(paths, **options), host="127.0.0.1", port=0, log_level="warning", access_log=False
            )
        )
        thread = threading.Thread(target=server.run)
        thread.start()
        try:
            while not server.started:
                thread.join(0.01)
            port = server.servers[0].sockets[0].getsockname()[1]
            yield f"http://127.0.0.1:{port}"
        finally:
            server.should_exit = True
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

    def test_cached_board_names_do_not_create_a_false_same_company_hint(self):
        for company in ("Arbeitsagentur", "JOIN", "Remotely"):
            with self.subTest(company=company):
                memory = {
                    "studysmarter:cached": {
                        "title": "Junior Developer",
                        "company": company,
                        "workflow_status": "new",
                        "source_names": ["studysmarter"],
                    },
                    "studysmarter:applied": {
                        "title": "Junior Administrator",
                        "company": company,
                        "workflow_status": "interview",
                        "source_names": ["studysmarter"],
                        "workflow_history": [{"status": "interview", "occurred_on": "2026-10-01"}],
                    },
                }
                save_memory(memory, self.memory_path)
                self.edit_recommendation(id="studysmarter:cached", company=company)
                before = deepcopy(load_memory(self.memory_path))

                jobs = load_review_jobs(self.recommendations_path, self.memory_path)

                self.assertEqual(jobs[0]["company"], "")
                self.assertEqual(jobs[0]["company_applications"], [])
                self.assertEqual(jobs[0]["workflow_status"], "new")
                self.assertEqual(load_memory(self.memory_path), before)

    def test_cached_board_name_is_replaced_by_a_known_employer(self):
        memory = load_memory(self.memory_path)
        memory["job:1"].update(source_names=["studysmarter"])
        save_memory(memory, self.memory_path)
        self.edit_recommendation(company="JOIN")

        jobs = load_review_jobs(self.recommendations_path, self.memory_path)

        self.assertEqual(jobs[0]["company"], "Example GmbH")

    def test_the_review_keeps_same_named_employers_from_other_sources(self):
        memory = load_memory(self.memory_path)
        memory["job:1"].update(company="JOIN", source_names=["arbeitnow"])
        save_memory(memory, self.memory_path)
        self.edit_recommendation(company="JOIN")

        jobs = load_review_jobs(self.recommendations_path, self.memory_path)

        self.assertEqual(jobs[0]["company"], "JOIN")

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
                    self.assertIn('href="/app.css?v=10"', page)
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
            manual_importer=importer,
        ) as base_url:
            result = post_json(f"{base_url}/api/manual-import", {"url": "https://example.com/jobs/python"})

        self.assertEqual(result["job_id"], "manual:python")
        self.assertEqual(calls[0][0], "https://example.com/jobs/python")
        self.assertEqual(calls[0][1]["memory_path"], self.memory_path)

    def test_application_start_api_adds_job_to_overview(self):
        with self.server_context() as base_url:
            result = post_form(f"{base_url}/api/applications", {"job_id": "job:1", "salary_expectation_eur": "58000"})
            overview = get_json(f"{base_url}/api/applications")

        self.assertEqual(result["workflow_status"], "applied")
        self.assertTrue(result["application_tracked"])
        self.assertEqual(overview["statistics"]["total"], 1)
        self.assertEqual(overview["applications"][0]["id"], "job:1")
        self.assertEqual(overview["applications"][0]["salary_expectation_eur"], 58_000)

    def test_application_details_api_corrects_title_and_company(self):
        with self.server_context() as base_url:
            post_form(f"{base_url}/api/applications", {"job_id": "job:1"})
            result = post_json(
                f"{base_url}/api/application-details", {"job_id": "job:1", "title": "Admin", "company": "Example GmbH"}
            )
            overview = get_json(f"{base_url}/api/applications")

        self.assertEqual(result, {"title": "Admin", "company": "Example GmbH"})
        application = overview["applications"][0]
        self.assertEqual((application["title"], application["company"]), ("Admin", "Example GmbH"))
        self.assertEqual(application["agent_sources"], [])

    def test_documents_arrive_as_a_form_upload_and_can_be_downloaded(self):
        documents = self.directory / "application_documents"
        with self.server_context(application_documents_dir=documents) as base_url:
            result = post_form(
                f"{base_url}/api/applications",
                {"job_id": "job:1", "salary_period": "year"},
                [("resume", "Lebenslauf.pdf", b"%PDF resume")],
            )
            document = load_memory(self.memory_path)["job:1"]["application_documents"][0]
            query = urlencode({"job_id": "job:1", "document_id": document["id"]})
            with urlopen(f"{base_url}/api/application-document?{query}") as response:
                content = response.read()

        self.assertEqual(result["workflow_status"], "applied")
        self.assertEqual((document["kind"], document["name"]), ("resume", "Lebenslauf.pdf"))
        self.assertEqual(content, b"%PDF resume")

    def test_uploads_need_the_reviews_header_and_origin_and_json_stays_small(self):
        before = load_memory(self.memory_path)
        with self.server_context() as base_url:
            url = f"{base_url}/api/applications"
            cases = [
                (form_request(url, {"job_id": "job:1"}, headers={"X-Jobfinder-Upload": "0"}), 403),
                (form_request(url, {"job_id": "job:1"}, headers={"Origin": "https://attacker.example"}), 403),
                (json_request(url, {"job_id": "job:1"}), 415),
            ]
            for request, code in cases:
                with self.subTest(code=code), self.assertRaises(HTTPError) as caught:
                    urlopen(request)
                self.assertEqual(caught.exception.code, code)
                caught.exception.close()
            oversized = json_request(
                f"{base_url}/api/review-note", {"job_id": "job:1", "review_note": "x" * MAX_JSON_BYTES}
            )
            self.assertIn(rejection_status(oversized), {400, "reset"})
            too_big = form_request(url, {"job_id": "job:1"}, [("resume", "cv.pdf", b"x" * (15 * 1024 * 1024 + 1))])
            with self.assertRaises(HTTPError) as caught:
                urlopen(too_big)
            self.assertEqual(caught.exception.code, 400)
            self.assertIn("15 MB", json.loads(caught.exception.read())["error"])
        self.assertEqual(load_memory(self.memory_path), before)

    def test_documents_of_a_job_applied_to_meanwhile_are_removed_again(self):
        documents = self.directory / "documents"
        undecided = load_memory(self.memory_path)["job:1"]
        start_application("job:1", self.memory_path)
        # As if another tab had applied between reading the job and saving the documents.
        with mock.patch("job_finder.workflow.review_actions.load_job", return_value=undecided):
            result = start_application("job:1", self.memory_path, [upload("resume", "cv.pdf", b"%PDF")], documents)

        self.assertEqual(result["workflow_status"], "applied")
        self.assertNotIn("application_documents", load_memory(self.memory_path)["job:1"])
        self.assertFalse([path for path in documents.rglob("*") if path.is_file()])

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

    def test_document_download_refuses_bytes_changed_since_the_application(self):
        documents_directory = self.directory / "application_documents"
        start_application(
            "job:1", self.memory_path, [upload("resume", "CV.pdf", b"original resume")], documents_directory
        )
        document = load_memory(self.memory_path)["job:1"]["application_documents"][0]
        next(documents_directory.rglob("*.pdf")).write_bytes(b"changed after application")
        query = urlencode({"job_id": "job:1", "document_id": document["id"]})
        with self.server_context(application_documents_dir=documents_directory) as base_url:
            with self.assertRaises(HTTPError) as caught:
                urlopen(f"{base_url}/api/application-document?{query}")
            self.assertEqual(caught.exception.code, 404)
            caught.exception.close()

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
            connection.putheader("Content-Length", str(MAX_JSON_BYTES + 1))
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
        self.assertEqual(document["route_origin"], route_origin())
        self.assertEqual(result["workflow_status"], "ignored")
        self.assertNotIn("personal_ratings", document)
        jobs = load_review_jobs(self.recommendations_path, self.memory_path)
        self.assertEqual(jobs[0]["workflow_status"], "ignored")

    def test_offline_listings_load_only_on_request(self):
        memory = load_memory(self.memory_path)
        memory["job:closed"] = {
            "title": "Closed Developer",
            "company": "Example GmbH",
            "workflow_status": "ignored",
            "availability_checked_at": "2026-10-01T08:00:00",
        }
        memory["job:waiting"] = {"title": "Waiting Developer", "workflow_status": "waiting"}
        save_memory(memory, self.memory_path)

        with self.server_context() as base_url:
            current = get_json(f"{base_url}/api/recommendations")["recommendations"]
            archived = get_json(f"{base_url}/api/recommendations?archived=1")["recommendations"]

        self.assertEqual([job["id"] for job in current], ["job:1", "job:waiting"])
        self.assertEqual([job["id"] for job in archived], ["job:closed"])
        self.assertTrue(archived[0]["prefilter_warning"].startswith("Anzeige nicht mehr verfügbar"))

    def test_the_review_reads_only_entries_it_can_show_or_match(self):
        memory = load_memory(self.memory_path)
        memory.update(
            {
                "other:url": {"title": "Same listing", "source_urls": ["https://example.test/listing"]},
                "other:alias": {"title": "Linked listing", "linked_job_ids": ["job:x"]},
                "other:applied": {
                    "title": "Old application",
                    "workflow_status": "ignored",
                    "workflow_history": [{"status": "applied", "occurred_on": "2026-09-01"}],
                },
                "other:waiting": {"title": "Waiting", "workflow_status": "waiting"},
                "other:closed": {
                    "title": "Closed",
                    "workflow_status": "ignored",
                    "availability_checked_at": "2026-10-01T08:00:00",
                },
                "other:unrelated": {"title": "Unrelated", "workflow_status": "new"},
            }
        )
        save_memory(memory, self.memory_path)

        current = load_review_memory({"job:x"}, {"https://example.test/listing"}, {"waiting"}, self.memory_path)
        archived = load_review_memory(set(), set(), set(), self.memory_path, archived=True)

        self.assertLessEqual({"other:url", "other:alias", "other:applied", "other:waiting"}, current.keys())
        self.assertFalse({"other:closed", "other:unrelated"} & current.keys())
        self.assertEqual(current["other:applied"], memory["other:applied"])
        self.assertIn("other:closed", archived)
        self.assertNotIn("other:unrelated", archived)

    def test_local_api_asks_the_next_agent_run_for_a_new_fact_sheet(self):
        with self.server_context() as base_url:
            result = post_json(f"{base_url}/api/fact-sheet-rerun", {"job_id": "job:1"})
            requested_at = load_memory(self.memory_path)["job:1"][RERUN_FIELD]
            post_json(f"{base_url}/api/fact-sheet-rerun", {"job_id": "job:1"})
            document = get_json(f"{base_url}/api/recommendations")
            with self.assertRaises(HTTPError) as unknown:
                urlopen(json_request(f"{base_url}/api/fact-sheet-rerun", {"job_id": "job:unknown"}))

        self.assertEqual(result, {"fact_sheet_rerun": True})
        self.assertTrue(document["recommendations"][0]["fact_sheet_rerun"])
        # Asking twice keeps the first request; the decision itself stays untouched.
        self.assertEqual(load_memory(self.memory_path)["job:1"][RERUN_FIELD], requested_at)
        self.assertEqual(load_memory(self.memory_path)["job:1"]["workflow_status"], "interesting")
        self.assertEqual(unknown.exception.code, 400)

    def test_api_requests_leave_timings_but_no_ids_or_text(self):
        spans = span_recorder()
        spans.clear()
        with self.server_context() as base_url:
            get_json(f"{base_url}/api/recommendations")
            get_json(f"{base_url}/api/recommendations")
            with urlopen(f"{base_url}/review"):
                pass

        finished = spans.get_finished_spans()
        requests = [item for item in finished if item.name == "review_request"]
        self.assertEqual([item.attributes["jobfinder.route"] for item in requests], ["/api/recommendations"] * 2)
        self.assertEqual([item.attributes["jobfinder.first_request"] for item in requests], [True, False])
        self.assertEqual(requests[0].attributes["jobfinder.status_code"], 200)
        steps = {item.name for item in finished if item.context.trace_id == requests[0].context.trace_id}
        self.assertLessEqual(
            {"read_recommendations", "read_memory", "build_cards", "read_fact_sheets", "db_connect"}, steps
        )
        values = [str(value) for item in finished for value in item.attributes.values()]
        self.assertFalse([value for value in values if "job:1" in value or "Python" in value])

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
        self.assertEqual(interview_overview["applications"][0]["next_appointment_at"], "2099-08-25T10:30")
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
