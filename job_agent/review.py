"""Small local web interface for reviewing job recommendations."""

import argparse
import json
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from job_agent.applications import (
    delete_history_event,
    load_application_overview,
    record_status_change,
    update_history_event,
)
from job_agent.memory import load_memory, save_memory
from job_agent.models import WorkflowStatus
from job_agent.paths import MEMORY_FILE, RECOMMENDATIONS_JSON


REVIEW_PAGE = Path(__file__).with_name("review.html")
APPLICATIONS_PAGE = Path(__file__).with_name("applications.html")


def load_review_jobs(
    recommendations_path=RECOMMENDATIONS_JSON,
    memory_path=MEMORY_FILE,
):
    """Combine compact recommendations with their persisted workflow status."""
    path = Path(recommendations_path)
    if not path.exists():
        return []

    document = json.loads(path.read_text(encoding="utf-8"))
    recommendations = document.get("recommendations", [])
    memory = load_memory(memory_path)
    review_jobs = []
    for recommendation in recommendations:
        job = dict(recommendation)
        entry = memory.get(job["id"], {})
        job["workflow_status"] = entry.get(
            "workflow_status",
            WorkflowStatus.NEW.value,
        )
        job["review_note"] = entry.get("review_note", "")
        review_jobs.append(job)
    return review_jobs


def update_workflow_status(
    job_id,
    workflow_status,
    memory_path=MEMORY_FILE,
    occurred_on=None,
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
    )
    save_memory(memory, memory_path)
    return current_status


def update_workflow_history(
    job_id,
    event_index,
    previous_status,
    previous_occurred_on,
    workflow_status,
    occurred_on,
    memory_path=MEMORY_FILE,
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
    )
    save_memory(memory, memory_path)
    return result


def delete_workflow_history(
    job_id,
    event_index,
    previous_status,
    previous_occurred_on,
    memory_path=MEMORY_FILE,
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
    )
    save_memory(memory, memory_path)
    return {"workflow_status": status}


def update_review_note(job_id, review_note, memory_path=MEMORY_FILE):
    """Persist an optional note alongside one unified workflow status."""
    memory = load_memory(memory_path)
    if job_id not in memory:
        raise KeyError(f"Unbekannte Job-ID: {job_id}")
    entry = memory[job_id]
    if not isinstance(review_note, str):
        raise ValueError("Notiz muss Text sein")
    if len(review_note) > 2000:
        raise ValueError("Notiz darf maximal 2000 Zeichen lang sein")
    entry["review_note"] = review_note.strip()
    save_memory(memory, memory_path)
    return {
        "review_note": entry.get("review_note", ""),
    }


class ReviewRequestHandler(BaseHTTPRequestHandler):
    """Serve the review page and its small JSON API."""

    recommendations_path = RECOMMENDATIONS_JSON
    memory_path = MEMORY_FILE
    page_path = REVIEW_PAGE
    applications_page_path = APPLICATIONS_PAGE

    def do_GET(self):
        """Return the page or the current joined recommendation data."""
        if self.path in {"/", "/index.html"}:
            self.send_file(self.page_path, "text/html; charset=utf-8")
            return
        if self.path in {"/applications", "/applications.html"}:
            self.send_file(
                self.applications_page_path,
                "text/html; charset=utf-8",
            )
            return
        if self.path == "/api/recommendations":
            self.send_json(
                {
                    "recommendations": load_review_jobs(
                        self.recommendations_path,
                        self.memory_path,
                    ),
                    "workflow_statuses": [status.value for status in WorkflowStatus],
                }
            )
            return
        if self.path == "/api/applications":
            self.send_json(load_application_overview(self.memory_path))
            return
        self.send_error(404)

    def do_POST(self):
        """Persist a workflow status selected in the browser."""
        if self.path not in {
            "/api/status",
            "/api/note",
            "/api/history",
            "/api/history/delete",
        }:
            self.send_error(404)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            if self.path == "/api/status":
                result = {
                    "workflow_status": update_workflow_status(
                        payload["job_id"],
                        payload["workflow_status"],
                        self.memory_path,
                        payload.get("occurred_on"),
                    )
                }
            elif self.path == "/api/note":
                result = update_review_note(
                    payload["job_id"],
                    payload.get("review_note"),
                    self.memory_path,
                )
            elif self.path == "/api/history":
                result = update_workflow_history(
                    payload["job_id"],
                    payload["event_index"],
                    payload["previous_status"],
                    payload.get("previous_occurred_on"),
                    payload["workflow_status"],
                    payload.get("occurred_on"),
                    self.memory_path,
                )
            else:
                result = delete_workflow_history(
                    payload["job_id"],
                    payload["event_index"],
                    payload["previous_status"],
                    payload.get("previous_occurred_on"),
                    self.memory_path,
                )
        except (TypeError, ValueError, KeyError, json.JSONDecodeError) as error:
            self.send_json({"error": str(error)}, status=400)
            return
        self.send_json(result)

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
    server = ThreadingHTTPServer(address, ReviewRequestHandler)
    url = f"http://{address[0]}:{address[1]}"
    if not args.no_browser:
        threading.Timer(0.3, webbrowser.open, args=(url,)).start()
    print(f"Job-Review geoeffnet unter {url}")
    print("Dieses Fenster schliessen, um den Review zu beenden.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
