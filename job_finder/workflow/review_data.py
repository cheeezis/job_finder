"""Review data joined with persisted workflow decisions."""

from contextlib import nullcontext
from pathlib import Path

from psycopg import errors

from job_finder.models import WorkflowStatus
from job_finder.paths import MEMORY_FILE, RECOMMENDATIONS_JSON
from job_finder.persistence.database import snapshot
from job_finder.persistence.fact_sheets import fact_sheets
from job_finder.persistence.storage import dataset_name, read_json
from job_finder.workflow.applications import is_application
from job_finder.workflow.memory import load_memory, memory_source_links, preferred_memory_id
from job_finder.workflow.reporting import is_international_listing

PERSISTED_REVIEW_STATUSES = {WorkflowStatus.INTERESTING.value, WorkflowStatus.INQUIRY.value}


def load_review_jobs(recommendations_path=RECOMMENDATIONS_JSON, memory_path=MEMORY_FILE):
    """Combine compact review jobs with their persisted workflow status."""
    path = Path(recommendations_path)
    with snapshot() if dataset_name(path) else nullcontext():
        document = read_json(path, {})
        memory = load_memory(memory_path)
    recommendations = document.get("recommendations", [])
    find_memory_ids = memory_id_finder(memory)
    review_jobs = []
    represented_memory_ids = set()
    for recommendation in recommendations:
        job = dict(recommendation)
        job["international"] = bool(job.get("international")) or is_international_listing(job)
        candidates = find_memory_ids(job)
        represented_memory_ids.update(candidates)
        memory_id = preferred_memory_id(candidates, memory, job["id"]) if candidates else job["id"]
        entry = memory.get(memory_id, {})
        # The listing's own id finds its details in the jobs dataset.
        job["recommendation_id"] = job["id"]
        job["id"] = memory_id
        job["workflow_status"] = entry.get("workflow_status", WorkflowStatus.NEW.value)
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
            or (entry.get("workflow_status") == "ignored" and entry.get("availability_checked_at"))
        ):
            continue
        review_jobs.append(remembered_review_job(job_id, entry))
    return one_card_per_job(review_jobs)


def one_card_per_job(review_jobs):
    """Show every recommendation that belongs to one remembered job on one card.

    Recommendations come best first, so the first card of a job leads and
    later ones add their links and places. Such cards remain until both runs
    have published the job again under the ID memory now gives all of it.
    """
    cards = {}
    for job in review_jobs:
        card = cards.setdefault(job["id"], job)
        if card is job:
            continue
        known = {link.get("url") for link in card.get("source_links") or []}
        card["source_links"] = [
            *(card.get("source_links") or []),
            *(link for link in job.get("source_links") or [] if link.get("url") not in known),
        ]
        card["locations"] = list(
            dict.fromkeys([*(card.get("locations") or []), *(job.get("locations") or [])])
        )
        # Hidden as international only if no listing of the job is a German one.
        card["international"] = bool(card.get("international") and job.get("international"))
        card["is_new"] = bool(card.get("is_new") or job.get("is_new"))
    return list(cards.values())


def attach_fact_sheets(jobs):
    """Add the agent's fact sheet, or the reason it stopped, to each job it worked on."""
    try:
        sheets = fact_sheets([job["id"] for job in jobs])
    except errors.UndefinedTable:
        # The database lacks the agent's tables until `job_finder.db init`;
        # the review must keep working in between.
        return jobs
    for job in jobs:
        entry = sheets.get(job["id"])
        if entry:
            job["fact_sheet"] = {
                "model": entry["model"],
                "complete": entry["complete"],
                "note": entry["note"],
                "sheet": entry["fact_sheet"],
                "cost_eur": float(entry["cost_eur"]),
                "created_at": entry["created_at"].isoformat(),
            }
    return jobs


def remembered_review_job(job_id, entry):
    """Keep a manual shortlist entry until the user changes its status."""
    source_links = memory_source_links(entry)
    if entry.get("availability_checked_at") and entry.get("workflow_status") == "ignored":
        availability_warning = (
            "Anzeige nicht mehr verfügbar; automatisch auf Nicht interessant gesetzt."
        )
    elif entry.get("active", True):
        availability_warning = (
            "Im aktuellen Lauf nicht gefunden; Verfügbarkeit bitte über die Anzeige prüfen."
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


def memory_id_finder(memory):
    """Look up the memory rows of each recommendation through one per-request URL index.

    The rows keep the memory's insertion order. If an entry's source_urls cannot
    be indexed, every lookup falls back to scanning, so such data fails exactly
    where it failed before instead of already while indexing.
    """
    index = {}
    try:
        for memory_id, entry in memory.items():
            for url in entry.get("source_urls", []):
                index.setdefault(url, []).append(memory_id)
    except Exception:
        return lambda job: memory_ids_for_job(job, memory)
    positions = {memory_id: position for position, memory_id in enumerate(memory)}

    def find(job):
        found = {memory_id for url in job_urls(job) for memory_id in index.get(url, [])}
        if job["id"] in memory:
            found.add(job["id"])
        return sorted(found, key=positions.__getitem__)

    return find


def memory_ids_for_job(job, memory):
    """Return every memory row represented by one merged recommendation."""
    job_id = job["id"]
    urls = job_urls(job)
    return [
        memory_id
        for memory_id, entry in memory.items()
        if memory_id == job_id or urls.intersection(entry.get("source_urls", []))
    ]


def job_urls(job):
    """Return the listing URLs a recommendation can be matched by."""
    urls = {
        link.get("url")
        for link in job.get("source_links", [])
        if isinstance(link, dict) and link.get("url")
    }
    if job.get("url"):
        urls.add(job["url"])
    return urls
