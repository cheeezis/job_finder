"""API error boundaries with synthetic failures and local document storage only."""

import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import psycopg
from azure.core.exceptions import ServiceRequestError
from fastapi.testclient import TestClient
from test_review import span_recorder

from job_finder import review_app
from job_finder.http import HttpResponseError, HttpStatusError
from job_finder.persistence import database_auth, document_store
from job_finder.review_app import ReviewPaths, create_app
from job_finder.workflow.memory import load_memory, save_memory
from job_finder.workflow.review_actions import start_application


class ReviewErrorTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        self.paths = ReviewPaths(
            memory=root / "memory",
            recommendations=root / "recommendations.json",
            jobs=root / "jobs.json",
            manual_cache=root / "manual.json",
            documents=root / "documents",
        )
        environment = patch.dict(os.environ, JOBFINDER_DOCUMENTS_BACKEND="local", JOBFINDER_DATABASE_AUTH="password")
        environment.start()
        self.addCleanup(environment.stop)
        self.paths.recommendations.write_text('{"recommendations": []}', encoding="utf-8")
        save_memory(
            {"synthetic:job": {"title": "Developer", "company": "Example", "workflow_status": "new"}}, self.paths.memory
        )
        self.spans = span_recorder()
        self.spans.clear()

    def client(self, **options):
        client = TestClient(create_app(self.paths, **options), base_url="http://localhost")
        self.addCleanup(client.close)
        return client

    def assert_safe_failure(self, response, status, *, error_type=None):
        self.assertEqual(response.status_code, status)
        self.assertEqual(set(response.json()), {"error"})
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertNotIn("private", response.text)
        spans = self.spans.get_finished_spans()
        requests = [item for item in spans if item.name == "review_request"]
        self.assertEqual(requests[-1].attributes["jobfinder.status_code"], status)
        if status >= 500:
            self.assertEqual(requests[-1].status.status_code.name, "ERROR")
        if error_type:
            self.assertEqual(requests[-1].status.description, error_type)
        for item in spans:
            self.assertNotIn("private", str(item.attributes))
            self.assertNotIn("private", str(item.events))
            self.assertNotIn("private", str(item.status.description))
            self.assertEqual(item.events, ())

    def test_password_and_entra_connection_failures_are_sanitized_503(self):
        cases = (
            {"JOBFINDER_DATABASE_AUTH": "password"},
            {"JOBFINDER_DATABASE_AUTH": "managed_identity", "JOBFINDER_MANAGED_IDENTITY_CLIENT_ID": "synthetic-id"},
        )
        for environment in cases:
            with (
                self.subTest(mode=environment["JOBFINDER_DATABASE_AUTH"]),
                patch.dict(os.environ, environment),
                patch.object(database_auth, "entra_parameters", return_value={"host": "offline.invalid"}),
                patch.object(
                    database_auth,
                    "_credential",
                    return_value=Mock(get_token=Mock(return_value=SimpleNamespace(token="private-token"))),
                ),
                patch.object(
                    database_auth.psycopg, "connect", side_effect=psycopg.OperationalError("private-dsn private-token")
                ),
            ):
                response = self.client().get("/api/recommendations")
                self.assert_safe_failure(response, 503)

    def test_entra_token_failure_is_503_without_password_fallback(self):
        with (
            patch.dict(
                os.environ,
                JOBFINDER_DATABASE_AUTH="managed_identity",
                JOBFINDER_MANAGED_IDENTITY_CLIENT_ID="synthetic-id",
            ),
            patch.object(database_auth, "entra_parameters", return_value={"host": "offline.invalid"}),
            patch.object(database_auth, "_credential", side_effect=RuntimeError("private-token")),
            patch.object(database_auth.psycopg, "connect") as connect,
        ):
            self.assert_safe_failure(self.client().get("/api/recommendations"), 503)
        connect.assert_not_called()

    def test_unexpected_errors_are_500_instead_of_client_or_database_errors(self):
        errors = (
            RuntimeError,
            TypeError,
            KeyError,
            ValueError,
            OSError,
            psycopg.ProgrammingError,
            psycopg.IntegrityError,
            psycopg.OperationalError,
        )
        for kind in errors:
            with (
                self.subTest(kind=kind.__name__),
                patch.object(review_app, "load_review_jobs", side_effect=kind("private-diagnostic")),
            ):
                self.assert_safe_failure(self.client().get("/api/recommendations"), 500, error_type=kind.__name__)

    def test_input_errors_stay_400_and_unknown_jobs_are_404(self):
        client = self.client()
        cases = (
            ("/api/review-status", {"job_id": "synthetic:job", "workflow_status": "invalid"}, 400),
            ("/api/review-note", {"job_id": "synthetic:job", "review_note": []}, 400),
            ("/api/status", {"job_id": "synthetic:job", "workflow_status": "applied", "occurred_on": "invalid"}, 400),
            ("/api/review-note", {"job_id": "synthetic:missing", "review_note": "note"}, 404),
        )
        for path, payload, status in cases:
            with self.subTest(path=path, status=status):
                self.assert_safe_failure(client.post(path, json=payload), status)
        self.assertEqual(load_memory(self.paths.memory)["synthetic:job"]["workflow_status"], "new")

    def test_manual_network_errors_are_502_and_invariants_stay_500(self):
        for failure in (
            HttpStatusError(403, "https://offline.invalid/private"),
            HttpResponseError("private-oversized-response"),
            TimeoutError("private-diagnostic"),
            ConnectionError("private-diagnostic"),
        ):
            with (
                self.subTest(kind=type(failure).__name__),
                patch("job_finder.sources.manual.validate_public_url", return_value="https://offline.invalid/job"),
                patch("job_finder.sources.manual.fetch_text_with_final_url", side_effect=failure),
            ):
                self.assert_safe_failure(
                    self.client().post("/api/manual-import", json={"url": "https://offline.invalid/job"}), 502
                )
        importer = Mock(
            side_effect=RuntimeError("Die manuell importierte Stelle konnte nicht zugeordnet werden; private")
        )
        response = self.client(manual_importer=importer).post(
            "/api/manual-import", json={"url": "https://offline.invalid/job"}
        )
        self.assert_safe_failure(response, 500, error_type="RuntimeError")

    def test_manual_validation_is_400_and_local_io_failure_is_500(self):
        client = self.client()
        for value in (
            None,
            "not a URL",
            "https://localhost/job",
            "https://example.test:private/job",
            "https://[invalid/job",
        ):
            with self.subTest(value=value):
                self.assert_safe_failure(client.post("/api/manual-import", json={"url": value}), 400)
        with patch("job_finder.workflow.manual_import.manual.add_url", side_effect=PermissionError("private-path")):
            response = client.post("/api/manual-import", json={"url": "https://offline.invalid/job"})
        self.assert_safe_failure(response, 500, error_type="PermissionError")

    def stored_document(self):
        start_application(
            "synthetic:job",
            self.paths.memory,
            [{"kind": "resume", "name": "private-resume.pdf", "content": b"private-original-bytes"}],
            self.paths.documents,
        )
        return load_memory(self.paths.memory)["synthetic:job"]["application_documents"][0]

    def download(self, client, document_id, *, job_id="synthetic:job"):
        return client.get("/api/application-document", params={"job_id": job_id, "document_id": document_id})

    def test_checksum_failure_is_sanitized_500_with_integrity_telemetry(self):
        document = self.stored_document()
        next(self.paths.documents.rglob("*.pdf")).write_bytes(b"private-changed-bytes")
        self.assert_safe_failure(self.download(self.client(), document["id"]), 500, error_type="DocumentIntegrityError")

    def test_missing_job_metadata_and_document_version_stay_404(self):
        document = self.stored_document()
        client = self.client()
        self.assert_safe_failure(self.download(client, document["id"], job_id="missing"), 404)
        self.assert_safe_failure(self.download(client, "missing"), 404)
        memory = load_memory(self.paths.memory)
        memory["synthetic:job"]["application_documents"][0]["blob_version_id"] = "synthetic-version"
        save_memory(memory, self.paths.memory)
        with patch.object(document_store, "read", side_effect=FileNotFoundError("private-version")) as read:
            self.assert_safe_failure(self.download(client, document["id"]), 404)
        self.assertEqual(read.call_args.kwargs["version_id"], "synthetic-version")

    def test_storage_outages_are_500_and_unsafe_metadata_never_reads_bytes(self):
        document = self.stored_document()
        client = self.client()
        for failure in (PermissionError("private-path"), ServiceRequestError("private-storage")):
            with self.subTest(kind=type(failure).__name__), patch.object(document_store, "read", side_effect=failure):
                self.assert_safe_failure(self.download(client, document["id"]), 500, error_type=type(failure).__name__)
        memory = load_memory(self.paths.memory)
        memory["synthetic:job"]["application_documents"][0]["folder_name"] = "../private"
        save_memory(memory, self.paths.memory)
        with patch.object(document_store, "read") as read:
            self.assert_safe_failure(self.download(client, document["id"]), 404)
        read.assert_not_called()
