"""Small local web interface for reviewing prefiltered jobs."""

import argparse
import errno
import json
import mimetypes
import os
import re
import socket
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlsplit

import psycopg

from job_finder.matching.config import LOCAL_SEARCH_LOCATION, LOCAL_SEARCH_POSTAL_CODE
from job_finder.models import WorkflowStatus
from job_finder.paths import (
    APPLICATION_DOCUMENTS_DIR,
    JOBS_FILE,
    MANUAL_CACHE_FILE,
    MEMORY_FILE,
    RECOMMENDATIONS_JSON,
)
from job_finder.persistence import document_store
from job_finder.persistence.application_documents import find_document, resolve_document_key
from job_finder.workflow.applications import load_application_overview
from job_finder.workflow.manual_import import import_manual_url
from job_finder.workflow.memory import load_memory
from job_finder.workflow.review_actions import (
    delete_workflow_history,
    start_application,
    undo_ignored_decision,
    update_application_salary,
    update_review_decision,
    update_workflow_history,
    update_workflow_status,
)
from job_finder.workflow.review_data import load_review_jobs

LANDING_PAGE = Path(__file__).with_name("landing.html")
REVIEW_PAGE = Path(__file__).with_name("review.html")
APPLICATIONS_PAGE = Path(__file__).with_name("applications.html")
APP_STYLES = Path(__file__).with_name("app.css")
APP_SCRIPT = Path(__file__).with_name("app.js")
LANDING_SCRIPT = Path(__file__).with_name("landing.js")
REVIEW_SCRIPT = Path(__file__).with_name("review.js")
APPLICATIONS_SCRIPT = Path(__file__).with_name("applications.js")
ROUTE_ORIGIN = f"{LOCAL_SEARCH_POSTAL_CODE} {LOCAL_SEARCH_LOCATION}".strip()
MAX_REQUEST_BYTES = 45 * 1024 * 1024
LOCAL_HOST_PATTERN = re.compile(r"^(?:127\.0\.0\.1|localhost)(?::\d{1,5})?$")
# Set when deployed behind a real hostname (e.g. Azure Container Apps); the
# Host/Origin checks below allow exactly this one hostname over HTTPS instead
# of only 127.0.0.1/localhost over HTTP.
DEPLOYED_HOST = os.environ.get("JOBFINDER_REVIEW_HOST", "").casefold()


class LocalReviewServer(HTTPServer):
    """Bind the local review port exclusively, especially on Windows."""

    allow_reuse_address = False

    def server_bind(self):
        """Bind the server with exclusive address use when the platform supports it."""
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


class ReviewRequestHandler(BaseHTTPRequestHandler):
    """Serve the review page and its small JSON API."""

    recommendations_path = RECOMMENDATIONS_JSON
    memory_path = MEMORY_FILE
    jobs_path = JOBS_FILE
    manual_cache_path = MANUAL_CACHE_FILE
    application_documents_dir = APPLICATION_DOCUMENTS_DIR
    manual_importer = staticmethod(import_manual_url)
    landing_page_path = LANDING_PAGE
    page_path = REVIEW_PAGE
    applications_page_path = APPLICATIONS_PAGE
    styles_path = APP_STYLES
    script_path = APP_SCRIPT
    landing_script_path = LANDING_SCRIPT
    review_script_path = REVIEW_SCRIPT
    applications_script_path = APPLICATIONS_SCRIPT

    def do_GET(self):
        """Handle database outages without exposing connection details."""
        try:
            self._do_GET()
        except psycopg.Error:
            self.send_json({"error": "Datenbank vorübergehend nicht erreichbar."}, status=503)

    def _do_GET(self):
        """Return the page or the current joined recommendation data."""
        if not self.accept_local_request():
            return
        request_path = urlsplit(self.path).path
        if request_path in {"/", "/index.html"}:
            self.send_file(self.landing_page_path, "text/html; charset=utf-8")
            return
        if request_path in {"/review", "/review.html"}:
            self.send_file(self.page_path, "text/html; charset=utf-8")
            return
        if request_path in {"/applications", "/applications.html"}:
            self.send_file(self.applications_page_path, "text/html; charset=utf-8")
            return
        if request_path == "/app.css":
            self.send_file(self.styles_path, "text/css; charset=utf-8")
            return
        scripts = {
            "/app.js": self.script_path,
            "/landing.js": self.landing_script_path,
            "/review.js": self.review_script_path,
            "/applications.js": self.applications_script_path,
        }
        if request_path in scripts:
            self.send_file(scripts[request_path], "text/javascript; charset=utf-8")
            return
        if request_path == "/api/recommendations":
            self.send_json(
                {
                    "recommendations": load_review_jobs(
                        self.recommendations_path, self.memory_path
                    ),
                    "workflow_statuses": [status.value for status in WorkflowStatus],
                    "route_origin": ROUTE_ORIGIN,
                }
            )
            return
        if request_path == "/api/applications":
            self.send_json(load_application_overview(self.memory_path))
            return
        if request_path == "/api/application-document":
            self.send_application_document()
            return
        self.send_error(404)

    def do_POST(self):
        """Validate a local JSON request and dispatch its application action."""
        if not self.accept_local_request(require_json=True):
            return
        actions = {
            "/api/manual-import": self._import_manual,
            "/api/applications": self._start_application,
            "/api/application-salary": self._update_salary,
            "/api/review-status": self._review_status,
            "/api/review-undo": self._undo_review,
            "/api/status": self._update_status,
            "/api/history": self._update_history,
            "/api/history/delete": self._delete_history,
        }
        action = actions.get(urlsplit(self.path).path)
        if action is None:
            self.send_error(404)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > MAX_REQUEST_BYTES:
                raise ValueError("Anfrage ist leer oder zu groß")
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("JSON-Objekt erforderlich")
            result = action(payload)
        except psycopg.Error:
            self.send_json(
                {"error": "Datenbankänderung fehlgeschlagen; bitte erneut versuchen."}, status=503
            )
            return
        except (TypeError, ValueError, KeyError, OSError, RuntimeError) as error:
            self.send_json({"error": str(error)}, status=400)
            return
        self.send_json(result)

    def _import_manual(self, payload):
        return type(self).manual_importer(
            payload.get("url"),
            cache_path=self.manual_cache_path,
            jobs_path=self.jobs_path,
            memory_path=self.memory_path,
            recommendations_path=self.recommendations_path,
        )

    def _start_application(self, payload):
        return start_application(
            payload["job_id"],
            self.memory_path,
            payload.get("documents"),
            self.application_documents_dir,
            salary_expectation_eur=payload.get(
                "salary_expectation_eur", payload.get("salary_expectation")
            ),
            salary_period=payload.get("salary_period", "year"),
        )

    def _update_salary(self, payload):
        return update_application_salary(
            payload["job_id"],
            payload.get("salary_expectation_eur"),
            payload.get("salary_period", "year"),
            self.memory_path,
        )

    def _review_status(self, payload):
        return update_review_decision(
            payload["job_id"], payload["workflow_status"], self.memory_path
        )

    def _undo_review(self, payload):
        return undo_ignored_decision(
            payload["job_id"], payload["expected_status"], self.memory_path
        )

    def _update_status(self, payload):
        return {
            "workflow_status": update_workflow_status(
                payload["job_id"],
                payload["workflow_status"],
                self.memory_path,
                payload.get("occurred_on"),
                payload.get("scheduled_for"),
            )
        }

    def _update_history(self, payload):
        return update_workflow_history(
            payload["job_id"],
            payload["event_index"],
            payload["previous_status"],
            payload.get("previous_occurred_on"),
            payload["workflow_status"],
            payload.get("occurred_on"),
            self.memory_path,
            scheduled_for=payload.get("scheduled_for"),
            previous_scheduled_for=payload.get("previous_scheduled_for"),
        )

    def _delete_history(self, payload):
        return delete_workflow_history(
            payload["job_id"],
            payload["event_index"],
            payload["previous_status"],
            payload.get("previous_occurred_on"),
            self.memory_path,
            previous_scheduled_for=payload.get("previous_scheduled_for"),
        )

    def send_application_document(self):
        """Return one document referenced by the matching memory entry."""
        query = parse_qs(urlsplit(self.path).query)
        try:
            job_id = first_query_value(query, "job_id")
            document_id = first_query_value(query, "document_id")
            memory = load_memory(self.memory_path)
            entry = memory[job_id]
            metadata = find_document(entry, document_id)
            key = resolve_document_key(job_id, metadata)
            content = document_store.read(key, self.application_documents_dir)
        except (KeyError, ValueError, OSError):
            self.send_error(404)
            return
        content_type = mimetypes.guess_type(metadata["name"])[0]
        self.send_content(
            content,
            content_type or "application/octet-stream",
            disposition=f"attachment; filename*=UTF-8''{quote(metadata['name'])}",
        )

    def accept_local_request(self, *, require_json=False):
        """Reject DNS rebinding and cross-site mutation attempts.

        Browsers cannot send an ``application/json`` cross-origin POST without
        a preflight. This server deliberately emits no CORS permission.
        """
        host = self.headers.get("Host", "").casefold()
        if DEPLOYED_HOST:
            if host != DEPLOYED_HOST:
                self.send_error(403, "Ungültiger Host")
                return False
            scheme = "https"
        else:
            if not LOCAL_HOST_PATTERN.fullmatch(host):
                self.send_error(403, "Ungültiger lokaler Host")
                return False
            scheme = "http"
        origin = self.headers.get("Origin")
        if origin and origin.casefold() != f"{scheme}://{host}":
            self.send_error(403, "Ungültiger Ursprung")
            return False
        if require_json:
            content_type = self.headers.get("Content-Type", "")
            if content_type.partition(";")[0].strip().casefold() != "application/json":
                self.send_error(415, "JSON-Inhalt erforderlich")
                return False
        return True

    def end_headers(self):
        """Apply privacy and browser-hardening headers to every response."""
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "same-origin")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self' 'unsafe-inline'; "
            "style-src 'self'; img-src 'self' data:; "
            "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'",
        )
        super().end_headers()

    def send_file(self, path, content_type):
        """Return one UTF-8 page from disk."""
        try:
            content = Path(path).read_bytes()
        except FileNotFoundError:
            self.send_error(404)
            return
        self.send_content(content, content_type)

    def send_json(self, value, status=200):
        """Return one JSON response."""
        content = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_content(content, "application/json; charset=utf-8", status)

    def send_content(self, content, content_type, status=200, *, disposition=None):
        """Send bytes with shared response headers and an optional download name."""
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        if disposition is not None:
            self.send_header("Content-Disposition", disposition)
        self.end_headers()
        self.wfile.write(content)

    def log_message(self, format, *args):
        """Keep the launcher quiet during normal browser requests."""


def first_query_value(query, name):
    """Require one non-empty query parameter."""
    values = query.get(name, [])
    if len(values) != 1 or not values[0]:
        raise ValueError(f"Fehlender Parameter: {name}")
    return values[0]


def parse_args():
    """Parse local server options."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true")
    return parser.parse_args()


def address_is_in_use(error):
    """Recognize the cross-platform error for an already running server."""
    return error.errno == errno.EADDRINUSE or getattr(error, "winerror", None) == 10048


def main():
    """Start the review server on loopback using the configured port."""
    args = parse_args()
    address = (args.host, args.port)
    url = f"http://{address[0]}:{address[1]}"
    try:
        server = LocalReviewServer(address, ReviewRequestHandler)
    except OSError as error:
        if not address_is_in_use(error):
            raise
        print(f"Job Finder läuft bereits unter {url}")
        if not args.no_browser:
            webbrowser.open(url)
        return
    if not args.no_browser:
        threading.Timer(0.3, webbrowser.open, args=(url,)).start()
    print(f"Job Finder geoeffnet unter {url}")
    print("Dieses Fenster schliessen, um den Job Finder zu beenden.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
