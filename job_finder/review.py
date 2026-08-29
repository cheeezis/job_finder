"""Small local web interface for reviewing prefiltered jobs."""

import argparse
import json
import mimetypes
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlsplit

from job_finder.application_documents import (
    document_path,
    find_document,
    remove_documents,
    store_documents,
)

from job_finder.applications import (
    delete_history_event,
    is_application,
    load_application_overview,
    record_status_change,
    synchronize_current_status,
    update_history_event,
)
from job_finder.config import LOCAL_SEARCH_LOCATION, LOCAL_SEARCH_POSTAL_CODE
from job_finder.manual_import import import_manual_url
from job_finder.memory import load_memory, preferred_memory_id, save_memory
from job_finder.models import WorkflowStatus
from job_finder.paths import (
    APPLICATION_DOCUMENTS_DIR,
    JOBS_FILE,
    MANUAL_CACHE_FILE,
    MEMORY_FILE,
    RECOMMENDATIONS_JSON,
)
from job_finder.reporting import is_international_listing


LANDING_PAGE = Path(__file__).with_name("landing.html")
REVIEW_PAGE = Path(__file__).with_name("review.html")
APPLICATIONS_PAGE = Path(__file__).with_name("applications.html")
APP_STYLES = Path(__file__).with_name("app.css")
ROUTE_ORIGIN = f"{LOCAL_SEARCH_POSTAL_CODE} {LOCAL_SEARCH_LOCATION}".strip()
MAX_REQUEST_BYTES = 45 * 1024 * 1024


def load_review_jobs(
    recommendations_path=RECOMMENDATIONS_JSON,
    memory_path=MEMORY_FILE,
):
    """Combine compact review jobs with their persisted workflow status."""
    path = Path(recommendations_path)
    if not path.exists():
        return []

    document = json.loads(path.read_text(encoding="utf-8"))
    recommendations = document.get("recommendations", [])
    memory = load_memory(memory_path)
    review_jobs = []
    for recommendation in recommendations:
        job = dict(recommendation)
        job["international"] = bool(job.get("international")) or is_international_listing(job)
        memory_id, entry = memory_entry_for_job(job, memory)
        job["id"] = memory_id
        job["workflow_status"] = entry.get(
            "workflow_status",
            WorkflowStatus.NEW.value,
        )
        job["application_tracked"] = is_application(entry)
        if not job.get("source_links"):
            source_names = entry.get("source_names", [])
            if not isinstance(source_names, list):
                source_names = []
            job["source_links"] = [
                {
                    "source": (
                        source_names[index]
                        if index < len(source_names)
                        else "listing"
                    ),
                    "url": url,
                }
                for index, url in enumerate(entry.get("source_urls", []))
                if isinstance(url, str) and url
            ]
        review_jobs.append(job)
    return review_jobs


def memory_entry_for_job(job, memory):
    """Resolve stale recommendation IDs through an exact known source URL."""
    job_id = job["id"]
    urls = {
        link.get("url")
        for link in job.get("source_links", [])
        if isinstance(link, dict) and link.get("url")
    }
    if job.get("url"):
        urls.add(job["url"])
    candidates = [
        memory_id
        for memory_id, entry in memory.items()
        if memory_id == job_id
        or urls.intersection(entry.get("source_urls", []))
    ]
    if not candidates:
        return job_id, {}
    memory_id = preferred_memory_id(candidates, memory, job_id)
    return memory_id, memory[memory_id]


def update_workflow_status(
    job_id,
    workflow_status,
    memory_path=MEMORY_FILE,
    occurred_on=None,
    scheduled_for=None,
):
    """Validate and persist one manual workflow decision."""
    status = WorkflowStatus(workflow_status)
    memory = load_memory(memory_path)
    if job_id not in memory:
        raise KeyError(f"Unbekannte Job-ID: {job_id}")
    current_status = record_status_change(
        memory[job_id],
        status,
        occurred_on,
        scheduled_for,
    )
    save_memory(memory, memory_path)
    return current_status


def update_review_decision(
    job_id,
    workflow_status,
    memory_path=MEMORY_FILE,
):
    """Persist a review decision without changing an existing application."""
    status = WorkflowStatus(workflow_status)
    if status not in {
        WorkflowStatus.INTERESTING,
        WorkflowStatus.INQUIRY,
        WorkflowStatus.IGNORED,
    }:
        raise ValueError("Ungueltiger Review-Status")
    memory = load_memory(memory_path)
    if job_id not in memory:
        raise KeyError(f"Unbekannte Job-ID: {job_id}")
    entry = memory[job_id]
    if is_application(entry):
        return {
            "workflow_status": entry.get(
                "workflow_status",
                WorkflowStatus.APPLIED.value,
            ),
            "application_tracked": True,
        }
    current_status = record_status_change(entry, status)
    save_memory(memory, memory_path)
    return {
        "workflow_status": current_status,
        "application_tracked": False,
    }


def undo_ignored_decision(
    job_id,
    expected_status,
    memory_path=MEMORY_FILE,
):
    """Remove the latest ignored transition and restore its prior status."""
    memory = load_memory(memory_path)
    if job_id not in memory:
        raise KeyError(f"Unbekannte Job-ID: {job_id}")
    entry = memory[job_id]
    if is_application(entry):
        raise ValueError("Bewerbungsstatus kann hier nicht rückgängig gemacht werden")
    if entry.get("workflow_status") != WorkflowStatus(expected_status).value:
        raise ValueError("Die Stelle wurde zwischenzeitlich geändert")
    if expected_status != WorkflowStatus.IGNORED.value:
        raise ValueError("Nur die letzte Nicht-interessant-Entscheidung ist rückgängig")
    history = entry.get("workflow_history")
    if not isinstance(history, list) or not history:
        raise ValueError("Keine Entscheidung zum Rückgängigmachen gefunden")
    last_event = history[-1]
    if not isinstance(last_event, dict) or last_event.get("status") != expected_status:
        raise ValueError("Die letzte Entscheidung hat sich zwischenzeitlich geändert")
    history.pop()
    status = synchronize_current_status(entry)
    save_memory(memory, memory_path)
    return {
        "workflow_status": status,
        "application_tracked": False,
    }


def start_application(
    job_id,
    memory_path=MEMORY_FILE,
    documents=None,
    documents_dir=APPLICATION_DOCUMENTS_DIR,
    salary_expectation=None,
):
    """Record the first application without overwriting later progress."""
    memory = load_memory(memory_path)
    if job_id not in memory:
        raise KeyError(f"Unbekannte Job-ID: {job_id}")
    entry = memory[job_id]
    if is_application(entry):
        return {
            "workflow_status": entry.get(
                "workflow_status",
                WorkflowStatus.APPLIED.value,
            ),
            "application_tracked": True,
        }
    salary_note = validated_salary_expectation(salary_expectation)
    stored_documents = store_documents(
        job_id,
        documents,
        documents_dir,
        company=entry.get("company", ""),
        title=entry.get("title", ""),
    )
    if stored_documents:
        entry["application_documents"] = stored_documents
    if salary_note:
        entry["salary_expectation"] = salary_note
    status = record_status_change(entry, WorkflowStatus.APPLIED)
    try:
        save_memory(memory, memory_path)
    except OSError:
        remove_documents(job_id, stored_documents, documents_dir)
        raise
    return {
        "workflow_status": status,
        "application_tracked": True,
    }


def validated_salary_expectation(value):
    """Return one optional, compact salary note exactly for this application."""
    if value is None or value == "":
        return ""
    if not isinstance(value, str):
        raise ValueError("Gehaltsvorstellung muss Text sein")
    note = " ".join(value.split())
    if len(note) > 500:
        raise ValueError("Gehaltsvorstellung darf höchstens 500 Zeichen lang sein")
    return note


def update_workflow_history(
    job_id,
    event_index,
    previous_status,
    previous_occurred_on,
    workflow_status,
    occurred_on,
    memory_path=MEMORY_FILE,
    scheduled_for=None,
    previous_scheduled_for=None,
):
    """Edit one manual workflow event."""
    memory = load_memory(memory_path)
    if job_id not in memory:
        raise KeyError(f"Unbekannte Job-ID: {job_id}")
    result = update_history_event(
        memory[job_id],
        event_index,
        previous_status,
        previous_occurred_on,
        workflow_status,
        occurred_on,
        scheduled_for,
        previous_scheduled_for,
    )
    save_memory(memory, memory_path)
    return result


def delete_workflow_history(
    job_id,
    event_index,
    previous_status,
    previous_occurred_on,
    memory_path=MEMORY_FILE,
    previous_scheduled_for=None,
):
    """Delete one manual workflow event."""
    memory = load_memory(memory_path)
    if job_id not in memory:
        raise KeyError(f"Unbekannte Job-ID: {job_id}")
    status = delete_history_event(
        memory[job_id],
        event_index,
        previous_status,
        previous_occurred_on,
        previous_scheduled_for,
    )
    save_memory(memory, memory_path)
    return {"workflow_status": status}


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

    def do_GET(self):
        """Return the page or the current joined recommendation data."""
        request_path = urlsplit(self.path).path
        if request_path in {"/", "/index.html"}:
            self.send_file(self.landing_page_path, "text/html; charset=utf-8")
            return
        if request_path in {"/review", "/review.html"}:
            self.send_file(self.page_path, "text/html; charset=utf-8")
            return
        if request_path in {"/applications", "/applications.html"}:
            self.send_file(
                self.applications_page_path,
                "text/html; charset=utf-8",
            )
            return
        if request_path == "/app.css":
            self.send_file(self.styles_path, "text/css; charset=utf-8")
            return
        if request_path == "/api/recommendations":
            self.send_json(
                {
                    "recommendations": load_review_jobs(
                        self.recommendations_path,
                        self.memory_path,
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
        """Persist a workflow status selected in the browser."""
        request_path = urlsplit(self.path).path
        if request_path not in {
            "/api/status",
            "/api/applications",
            "/api/review-status",
            "/api/review-undo",
            "/api/history",
            "/api/history/delete",
            "/api/manual-import",
        }:
            self.send_error(404)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length > MAX_REQUEST_BYTES:
                raise ValueError("Anfrage ist zu groß")
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            if request_path == "/api/manual-import":
                result = type(self).manual_importer(
                    payload.get("url"),
                    cache_path=self.manual_cache_path,
                    jobs_path=self.jobs_path,
                    memory_path=self.memory_path,
                    recommendations_path=self.recommendations_path,
                )
            elif request_path == "/api/applications":
                result = start_application(
                    payload["job_id"],
                    self.memory_path,
                    payload.get("documents"),
                    self.application_documents_dir,
                    salary_expectation=payload.get("salary_expectation"),
                )
            elif request_path == "/api/review-status":
                result = update_review_decision(
                    payload["job_id"],
                    payload["workflow_status"],
                    self.memory_path,
                )
            elif request_path == "/api/review-undo":
                result = undo_ignored_decision(
                    payload["job_id"],
                    payload["expected_status"],
                    self.memory_path,
                )
            elif request_path == "/api/status":
                result = {
                    "workflow_status": update_workflow_status(
                        payload["job_id"],
                        payload["workflow_status"],
                        self.memory_path,
                        payload.get("occurred_on"),
                        payload.get("scheduled_for"),
                    )
                }
            elif request_path == "/api/history":
                result = update_workflow_history(
                    payload["job_id"],
                    payload["event_index"],
                    payload["previous_status"],
                    payload.get("previous_occurred_on"),
                    payload["workflow_status"],
                    payload.get("occurred_on"),
                    self.memory_path,
                    scheduled_for=payload.get("scheduled_for"),
                    previous_scheduled_for=payload.get(
                        "previous_scheduled_for"
                    ),
                )
            else:
                result = delete_workflow_history(
                    payload["job_id"],
                    payload["event_index"],
                    payload["previous_status"],
                    payload.get("previous_occurred_on"),
                    self.memory_path,
                    previous_scheduled_for=payload.get(
                        "previous_scheduled_for"
                    ),
                )
        except (
            TypeError,
            ValueError,
            KeyError,
            OSError,
            RuntimeError,
            json.JSONDecodeError,
        ) as error:
            self.send_json({"error": str(error)}, status=400)
            return
        self.send_json(result)

    def send_application_document(self):
        """Return one document referenced by the matching memory entry."""
        query = parse_qs(urlsplit(self.path).query)
        try:
            job_id = first_query_value(query, "job_id")
            document_id = first_query_value(query, "document_id")
            memory = load_memory(self.memory_path)
            entry = memory[job_id]
            metadata = find_document(entry, document_id)
            path = document_path(
                job_id,
                metadata,
                self.application_documents_dir,
            )
            content = path.read_bytes()
        except (KeyError, ValueError, OSError):
            self.send_error(404)
            return
        content_type = mimetypes.guess_type(metadata["name"])[0]
        self.send_response(200)
        self.send_header("Content-Type", content_type or "application/octet-stream")
        self.send_header("Content-Length", str(len(content)))
        self.send_header(
            "Content-Disposition",
            f"attachment; filename*=UTF-8''{quote(metadata['name'])}",
        )
        self.end_headers()
        self.wfile.write(content)

    def send_file(self, path, content_type):
        """Return one UTF-8 page from disk."""
        try:
            content = Path(path).read_bytes()
        except FileNotFoundError:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def send_json(self, value, status=200):
        """Return one JSON response."""
        content = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
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
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true")
    return parser.parse_args()


def main():
    """Start the review server on the local computer only."""
    args = parse_args()
    address = ("127.0.0.1", args.port)
    server = HTTPServer(address, ReviewRequestHandler)
    url = f"http://{address[0]}:{address[1]}"
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
