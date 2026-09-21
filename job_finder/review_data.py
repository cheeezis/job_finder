"""Review data joined with persisted workflow decisions."""

from contextlib import nullcontext
from pathlib import Path

from job_finder.applications import (
    is_application,
)
from job_finder.database import snapshot
from job_finder.memory import (
    load_memory,
    memory_source_links,
    preferred_memory_id,
)
from job_finder.models import WorkflowStatus
from job_finder.paths import (
    MEMORY_FILE,
    RECOMMENDATIONS_JSON,
)
from job_finder.reporting import is_international_listing
from job_finder.storage import dataset_name, read_json

PERSISTED_REVIEW_STATUSES = {
    WorkflowStatus.INTERESTING.value,
    WorkflowStatus.INQUIRY.value,
}


def load_review_jobs(
    recommendations_path=RECOMMENDATIONS_JSON,
    memory_path=MEMORY_FILE,
):
    """Combine compact review jobs with their persisted workflow status."""
    path = Path(recommendations_path)
    with snapshot() if dataset_name(path) else nullcontext():
        document = read_json(path, {})
        memory = load_memory(memory_path)
    recommendations = document.get("recommendations", [])
    review_jobs = []
    represented_memory_ids = set()
    for recommendation in recommendations:
        job = dict(recommendation)
        job["international"] = bool(
            job.get("international")
        ) or is_international_listing(job)
        candidates = memory_ids_for_job(job, memory)
        represented_memory_ids.update(candidates)
        memory_id = (
            preferred_memory_id(candidates, memory, job["id"])
            if candidates
            else job["id"]
        )
        entry = memory.get(memory_id, {})
        job["id"] = memory_id
        job["workflow_status"] = entry.get(
            "workflow_status",
            WorkflowStatus.NEW.value,
        )
        # ``is_new`` describes the collection run, while a persisted workflow
        # status records that the user has already decided on the job. Never
        # resurrect that transient run marker after the review page reloads.
        job["is_new"] = bool(job.get("is_new")) and (
            job["workflow_status"] == WorkflowStatus.NEW.value
        )
        job["application_tracked"] = is_application(entry)
        if not job.get("source_links"):
            job["source_links"] = memory_source_links(entry)
        review_jobs.append(job)

    for job_id, entry in memory.items():
        if job_id in represented_memory_ids or not (
            entry.get("workflow_status") in PERSISTED_REVIEW_STATUSES
            or (
                entry.get("workflow_status") == "ignored"
                and entry.get("availability_checked_at")
            )
        ):
            continue
        review_jobs.append(remembered_review_job(job_id, entry))
    return review_jobs


def remembered_review_job(job_id, entry):
    """Keep a manual shortlist entry until the user changes its status."""
    source_links = memory_source_links(entry)
    if (
        entry.get("availability_checked_at")
        and entry.get("workflow_status") == "ignored"
    ):
        availability_warning = (
            "Anzeige nicht mehr verfügbar; automatisch auf Nicht interessant gesetzt."
        )
    elif entry.get("active", True):
        availability_warning = (
            "Im aktuellen Lauf nicht gefunden; Verfügbarkeit bitte über die "
            "Anzeige prüfen."
        )
    else:
        availability_warning = (
            "Seit mehreren vollständigen Läufen nicht gefunden; die Stelle ist "
            "möglicherweise nicht mehr verfügbar."
        )
    job = {
        "id": job_id,
        "title": entry.get("title", "Unbekannte Stelle"),
        "company": entry.get("company", "Unbekanntes Unternehmen"),
        "locations": list(entry.get("locations") or []),
        "source_links": source_links,
        "url": source_links[0]["url"] if source_links else "",
        "workflow_status": entry["workflow_status"],
        "is_new": False,
        "application_tracked": is_application(entry),
        "current_snapshot_missing": True,
        "prefilter_warning": availability_warning,
    }
    job["international"] = is_international_listing(job)
    return job


def memory_ids_for_job(job, memory):
    """Return every memory row represented by one merged recommendation."""
    job_id = job["id"]
    urls = {
        link.get("url")
        for link in job.get("source_links", [])
        if isinstance(link, dict) and link.get("url")
    }
    if job.get("url"):
        urls.add(job["url"])
    return [
        memory_id
        for memory_id, entry in memory.items()
        if memory_id == job_id or urls.intersection(entry.get("source_urls", []))
    ]


def memory_entry_for_job(job, memory):
    """Resolve stale recommendation IDs through an exact known source URL."""
    candidates = memory_ids_for_job(job, memory)
    if not candidates:
        return job["id"], {}
    memory_id = preferred_memory_id(candidates, memory, job["id"])
    return memory_id, memory[memory_id]
