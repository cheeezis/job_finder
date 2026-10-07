"""The review as a FastAPI app: pages, JSON API and document downloads.

Pydantic describes each payload, OpenAPI lists them under /openapi.json and
Swagger UI shows them at /docs. Every request passes the same checks: one
allowed host, same origin, JSON for every change, a size limit and hardening
headers on every response. The endpoints are plain functions, so FastAPI runs
the synchronous database work in its thread pool.
"""

import mimetypes
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import quote

import psycopg
from fastapi import FastAPI, Form, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel

from job_finder.matching.config import route_origin as configured_route_origin
from job_finder.models import WorkflowStatus
from job_finder.paths import APPLICATION_DOCUMENTS_DIR, JOBS_FILE, MANUAL_CACHE_FILE, MEMORY_FILE, RECOMMENDATIONS_JSON
from job_finder.persistence.application_documents import MAX_DOCUMENT_BYTES, find_document, read_document
from job_finder.persistence.runs import latest_runs
from job_finder.sources.registry import SOURCE_LABELS
from job_finder.telemetry import annotate, span, step
from job_finder.workflow.applications import load_application_overview
from job_finder.workflow.linked_listings import link_listing_to_application
from job_finder.workflow.manual_import import import_manual_url
from job_finder.workflow.memory import load_job
from job_finder.workflow.review_actions import (
    delete_workflow_history,
    request_fact_sheet_rerun,
    start_application,
    undo_ignored_decision,
    update_application_salary,
    update_review_decision,
    update_review_note,
    update_workflow_history,
    update_workflow_status,
)
from job_finder.workflow.review_data import attach_fact_sheets, load_review_jobs

PACKAGE = Path(__file__).parent
HTML = "text/html; charset=utf-8"
JAVASCRIPT = "text/javascript; charset=utf-8"
# Browser paths only select one of these packaged files; they never become file paths.
STATIC_FILES = {
    "/": ("landing.html", HTML),
    "/index.html": ("landing.html", HTML),
    "/review": ("review.html", HTML),
    "/review.html": ("review.html", HTML),
    "/applications": ("applications.html", HTML),
    "/applications.html": ("applications.html", HTML),
    "/app.css": ("app.css", "text/css; charset=utf-8"),
    "/app.js": ("app.js", JAVASCRIPT),
    "/landing.js": ("landing.js", JAVASCRIPT),
    "/review.js": ("review.js", JAVASCRIPT),
    "/applications.js": ("applications.js", JAVASCRIPT),
    "/docs": ("docs.html", HTML),
    "/docs.js": ("docs.js", JAVASCRIPT),
}
MAX_JSON_BYTES = 1024 * 1024
# Two documents of at most 15 MB each plus the form fields.
MAX_UPLOAD_BYTES = 2 * MAX_DOCUMENT_BYTES + 1024 * 1024
UPLOAD_PATHS = {"/api/applications"}
# Only the review's own pages send it; another site would need a CORS
# preflight for it, and the app grants none.
UPLOAD_HEADER = "x-jobfinder-upload"
LOCAL_HOST_PATTERN = re.compile(r"^(?:127\.0\.0\.1|localhost)(?::\d{1,5})?$")
SECURITY_HEADERS = {
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "same-origin",
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
        "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'"
    ),
}
# Only the API page loads Swagger UI, and only these two files of one version;
# docs.html pins their checksums, so the browser refuses any other bytes.
SWAGGER = "https://cdn.jsdelivr.net/npm/swagger-ui-dist@5.33.1"
DOCS_POLICY = (
    f"default-src 'self'; script-src 'self' {SWAGGER}/swagger-ui-bundle.js; "
    f"style-src 'self' {SWAGGER}/swagger-ui.css; img-src 'self' data:; "
    "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'"
)


@dataclass(frozen=True)
class ReviewPaths:
    """Where the review reads and writes; tests and the demo point it elsewhere."""

    recommendations: Path = RECOMMENDATIONS_JSON
    memory: Path = MEMORY_FILE
    jobs: Path = JOBS_FILE
    manual_cache: Path = MANUAL_CACHE_FILE
    documents: Path = APPLICATION_DOCUMENTS_DIR


class JobRequest(BaseModel):
    job_id: str


class ManualImport(BaseModel):
    url: Any = None


class ApplicationListing(JobRequest):
    application_id: str


class ApplicationSalary(JobRequest):
    salary_expectation_eur: Any = None
    salary_period: str = "year"


class ReviewDecision(JobRequest):
    workflow_status: str


class ReviewNote(JobRequest):
    review_note: Any = None


class ReviewUndo(JobRequest):
    expected_status: str


class StatusChange(JobRequest):
    workflow_status: str
    occurred_on: str | None = None
    scheduled_for: str | None = None


class HistoryEvent(JobRequest):
    event_index: int
    previous_status: str
    previous_occurred_on: str | None = None
    previous_scheduled_for: str | None = None


class HistoryEdit(HistoryEvent):
    workflow_status: str
    occurred_on: str | None = None
    scheduled_for: str | None = None


def create_app(paths=ReviewPaths(), *, deployed_host="", manual_importer=import_manual_url, route_origin=None):
    """Build the review app; deployed_host allows exactly that hostname over HTTPS instead of localhost."""
    app = FastAPI(title="Job Finder Review", docs_url=None, redoc_url=None, openapi_url="/openapi.json")
    route_origin = configured_route_origin() if route_origin is None else route_origin
    deployed_host = deployed_host.casefold()
    started, served = time.monotonic(), {"any": False}

    @app.middleware("http")
    async def guard(request: Request, call_next):
        problem = request_problem(request, deployed_host)
        if problem:
            response = error(*problem)
        elif request.url.path.startswith("/api/"):
            # One span per API call: route, status and whether the process had just started, never ids or text.
            # Own names: the Azure exporter drops the standard http.* attributes of an internal span.
            with span(
                "review_request",
                **{
                    "jobfinder.method": request.method,
                    "jobfinder.first_request": not served["any"],
                    "jobfinder.uptime_s": round(time.monotonic() - started),
                },
            ) as current:
                served["any"] = True
                response = await call_next(request)
                route = request.scope.get("route")
                annotate(
                    current,
                    **{
                        "jobfinder.route": getattr(route, "path", "other"),
                        "jobfinder.status_code": response.status_code,
                    },
                )
        else:
            response = await call_next(request)
        response.headers.update(SECURITY_HEADERS)
        if request.url.path == "/docs":
            response.headers["Content-Security-Policy"] = DOCS_POLICY
        return response

    @app.exception_handler(RequestValidationError)
    async def invalid_payload(_request, exc: RequestValidationError):
        return error(400, validation_message(exc.errors()))

    @app.exception_handler(psycopg.Error)
    async def database_failed(request: Request, _exc):
        if request.method == "GET":
            return error(503, "Datenbank vorübergehend nicht erreichbar.")
        return error(503, "Datenbankänderung fehlgeschlagen; bitte erneut versuchen.")

    for kind in (TypeError, ValueError, KeyError, OSError, RuntimeError):
        # The domain functions explain a refused change in their message.
        app.add_exception_handler(kind, lambda _request, exc: error(400, str(exc)))

    for path, (name, content_type) in STATIC_FILES.items():
        app.add_api_route(path, page(PACKAGE / name, content_type), methods=["GET"], include_in_schema=False)

    @app.get("/api/recommendations")
    def recommendations(archived: str | None = None):
        """Return the review's cards; archived=1 returns only those of listings that went offline."""
        jobs = load_review_jobs(paths.recommendations, paths.memory, archived=archived == "1")
        return JSONResponse(
            {
                "recommendations": attach_fact_sheets(jobs),
                "workflow_statuses": [status.value for status in WorkflowStatus],
                "route_origin": route_origin,
            }
        )

    @app.get("/api/sources")
    def sources():
        """Return the display name of every source, keyed by its internal name."""
        return JSONResponse({"labels": SOURCE_LABELS})

    @app.get("/api/runs")
    def runs():
        """Return the newest finder run of each runner (cloud, hybrid, local) with its key figures."""
        return JSONResponse({"runs": latest_runs()})

    @app.get("/api/applications")
    def applications():
        """Return open and completed applications with their statistics."""
        return JSONResponse(load_application_overview(paths.memory, recommendations_path=paths.recommendations))

    @app.get("/api/application-document")
    def application_document(request: Request):
        """Return one document of a job's application as a download; 404 for anything else."""
        try:
            job_id = single_query_value(request, "job_id")
            document_id = single_query_value(request, "document_id")
            with step("read_memory"):
                metadata = find_document(load_job(job_id, paths.memory), document_id)
            with step("read_document"):
                content = read_document(job_id, metadata, paths.documents)
        except (KeyError, ValueError, OSError):
            return error(404, "Nicht gefunden")
        return Response(
            content,
            media_type=mimetypes.guess_type(metadata["name"])[0] or "application/octet-stream",
            headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(metadata['name'])}"},
        )

    @app.post("/api/manual-import")
    def manual_import(body: ManualImport):
        """Import one listing from a URL the user pasted."""
        return manual_importer(
            body.url,
            cache_path=paths.manual_cache,
            jobs_path=paths.jobs,
            memory_path=paths.memory,
            recommendations_path=paths.recommendations,
        )

    @app.post("/api/applications")
    def application_start(
        job_id: Annotated[str, Form()],
        salary_expectation_eur: Annotated[str | None, Form()] = None,
        salary_period: Annotated[str, Form()] = "year",
        cover_letter: UploadFile | None = None,
        resume: UploadFile | None = None,
    ):
        """Record an application with its documents (a form upload) and salary expectation."""
        files = (("cover_letter", cover_letter), ("resume", resume))
        documents = [uploaded_document(kind, file) for kind, file in files if file is not None]
        return start_application(
            job_id,
            paths.memory,
            documents,
            paths.documents,
            salary_expectation_eur=salary_expectation_eur,
            salary_period=salary_period,
        )

    @app.post("/api/application-salary")
    def application_salary(body: ApplicationSalary):
        """Change the salary expectation of an application."""
        return update_application_salary(body.job_id, body.salary_expectation_eur, body.salary_period, paths.memory)

    @app.post("/api/application-listing")
    def application_listing(body: ApplicationListing):
        """Attach a new listing to an existing application."""
        return link_listing_to_application(body.job_id, body.application_id, paths.memory)

    @app.post("/api/review-status")
    def review_status(body: ReviewDecision):
        """Decide on a job in the review."""
        return update_review_decision(body.job_id, body.workflow_status, paths.memory)

    @app.post("/api/review-note")
    def review_note(body: ReviewNote):
        """Save the user's note on a job; an empty one removes it."""
        return update_review_note(body.job_id, body.review_note, paths.memory)

    @app.post("/api/fact-sheet-rerun")
    def fact_sheet_rerun(body: JobRequest):
        """Ask the next agent run for a new fact sheet; the review calls no model itself."""
        return request_fact_sheet_rerun(body.job_id, paths.memory)

    @app.post("/api/review-undo")
    def review_undo(body: ReviewUndo):
        """Undo the latest "not interested" if nothing changed it since."""
        return undo_ignored_decision(body.job_id, body.expected_status, paths.memory)

    @app.post("/api/status")
    def status(body: StatusChange):
        """Record a dated step of an application."""
        result = update_workflow_status(
            body.job_id, body.workflow_status, paths.memory, body.occurred_on, body.scheduled_for
        )
        return {"workflow_status": result}

    @app.post("/api/history")
    def history_edit(body: HistoryEdit):
        """Correct one step in an application's history."""
        return update_workflow_history(
            body.job_id,
            body.event_index,
            body.previous_status,
            body.previous_occurred_on,
            body.workflow_status,
            body.occurred_on,
            paths.memory,
            scheduled_for=body.scheduled_for,
            previous_scheduled_for=body.previous_scheduled_for,
        )

    @app.post("/api/history/delete")
    def history_delete(body: HistoryEvent):
        """Remove one step from an application's history."""
        return delete_workflow_history(
            body.job_id,
            body.event_index,
            body.previous_status,
            body.previous_occurred_on,
            paths.memory,
            previous_scheduled_for=body.previous_scheduled_for,
        )

    return app


def request_problem(request, deployed_host):
    """Return (status, message) when a request must not reach the app: rebinding, cross-site or oversized."""
    host = request.headers.get("host", "").casefold()
    if deployed_host:
        if host != deployed_host:
            return 403, "Ungültiger Host"
        scheme = "https"
    else:
        if not LOCAL_HOST_PATTERN.fullmatch(host):
            return 403, "Ungültiger lokaler Host"
        scheme = "http"
    origin = request.headers.get("origin")
    if origin and origin.casefold() != f"{scheme}://{host}":
        return 403, "Ungültiger Ursprung"
    if request.method != "POST":
        return None
    content_type = request.headers.get("content-type", "").partition(";")[0].strip().casefold()
    if request.url.path in UPLOAD_PATHS:
        # A form upload needs no preflight, so it must carry the review's own header and origin.
        if content_type != "multipart/form-data":
            return 415, "Formular-Upload erforderlich"
        if request.headers.get(UPLOAD_HEADER) != "1" or not origin:
            return 403, "Upload nur aus der Review"
        limit = MAX_UPLOAD_BYTES
    else:
        # Browsers cannot send a JSON POST to another site without a preflight, and no CORS permission is given.
        if content_type != "application/json":
            return 415, "JSON-Inhalt erforderlich"
        limit = MAX_JSON_BYTES
    try:
        length = int(request.headers.get("content-length", "0"))
    except ValueError:
        length = 0
    # Only the announced size counts, so an oversized body is refused before it is read.
    if length <= 0 or length > limit:
        return 400, "Anfrage ist leer oder zu groß"
    return None


def validation_message(errors):
    """Name the first problem of a payload in the words the review shows."""
    first = errors[0] if errors else {}
    location = [str(part) for part in first.get("loc", ()) if part != "body"]
    if first.get("type") == "json_invalid":
        return "Ungültiges JSON"
    if not location or first.get("type") in {"model_attributes_type", "dict_type"}:
        return "JSON-Objekt erforderlich"
    field = ".".join(location)
    return f"Feld fehlt: {field}" if first.get("type") == "missing" else f"Ungültiger Wert: {field}"


def uploaded_document(kind, file):
    """Read one uploaded file, at most one byte beyond the limit, as the documents check expects it."""
    content = file.file.read(MAX_DOCUMENT_BYTES + 1)
    if len(content) > MAX_DOCUMENT_BYTES:
        raise ValueError("Eine Datei darf höchstens 15 MB groß sein")
    return {"kind": kind, "name": file.filename, "content": content}


def single_query_value(request, name):
    """Require exactly one non-empty query parameter."""
    values = request.query_params.getlist(name)
    if len(values) != 1 or not values[0]:
        raise ValueError(f"Fehlender Parameter: {name}")
    return values[0]


def page(path, content_type):
    """Return an endpoint that serves one packaged file as it is on disk."""

    def serve():
        return Response(path.read_bytes(), media_type=content_type)

    return serve


def error(status, message):
    return JSONResponse({"error": message}, status_code=status)
