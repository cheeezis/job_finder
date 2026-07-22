"""Small local web interface for reviewing job recommendations."""

import argparse
import json
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from job_agent.memory import load_memory, save_memory
from job_agent.models import WorkflowStatus
from job_agent.paths import MEMORY_FILE, RECOMMENDATIONS_JSON


REVIEW_PAGE = Path(__file__).with_name("review.html")
RATING_STATUS_MIGRATION = {
    "very_interesting": WorkflowStatus.INTERESTING.value,
    "interesting": WorkflowStatus.INTERESTING.value,
    "not_interesting": WorkflowStatus.IGNORED.value,
}


def migrate_personal_ratings(memory_path=MEMORY_FILE):
    """Translate completed calibration ratings into their workflow status once."""
    memory = load_memory(memory_path)
    migrated = 0
    for entry in memory.values():
        if entry.get("workflow_status") != WorkflowStatus.NEW.value:
            continue
        target_status = RATING_STATUS_MIGRATION.get(entry.get("personal_rating"))
        if target_status is None:
            continue
        entry["workflow_status"] = target_status
        migrated += 1
    if migrated:
        save_memory(memory, memory_path)
    return migrated


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


def update_workflow_status(job_id, workflow_status, memory_path=MEMORY_FILE):
    """Validate and persist one manual workflow decision."""
    status = WorkflowStatus(workflow_status)
    memory = load_memory(memory_path)
    if job_id not in memory:
        raise KeyError(f"Unbekannte Job-ID: {job_id}")
    memory[job_id]["workflow_status"] = status.value
    save_memory(memory, memory_path)
    return status.value


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

    def do_GET(self):
        """Return the page or the current joined recommendation data."""
        if self.path in {"/", "/index.html"}:
            self.send_file(self.page_path, "text/html; charset=utf-8")
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
        self.send_error(404)

    def do_POST(self):
        """Persist a workflow status selected in the browser."""
        if self.path not in {"/api/status", "/api/note"}:
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
                    )
                }
            else:
                result = update_review_note(
                    payload["job_id"],
                    payload.get("review_note"),
                    self.memory_path,
                )
        except (ValueError, KeyError, json.JSONDecodeError) as error:
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
    migrated = migrate_personal_ratings()
    if migrated:
        print(f"{migrated} Kalibrierungsbewertungen in Status uebertragen")
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
