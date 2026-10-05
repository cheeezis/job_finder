"""Review data joined with persisted workflow decisions."""

from contextlib import nullcontext
from pathlib import Path

from psycopg import errors

from job_finder.matching.deduplication import companies_match, normalize_company
from job_finder.models import WorkflowStatus
from job_finder.paths import MEMORY_FILE, RECOMMENDATIONS_JSON
from job_finder.persistence.database import snapshot
from job_finder.persistence.fact_sheets import fact_sheets
from job_finder.persistence.storage import dataset_name, read_object
from job_finder.workflow.applications import OPEN_APPLICATION_STATUSES, application_row, is_application
from job_finder.workflow.memory import (
    clear_studysmarter_board_companies,
    load_memory,
    memory_id_finder,
    memory_source_links,
    preferred_memory_id,
    studysmarter_board_company,
)
from job_finder.workflow.reporting import is_international_listing

PERSISTED_REVIEW_STATUSES = {
    WorkflowStatus.INTERESTING.value,
    WorkflowStatus.INQUIRY.value,
    WorkflowStatus.WAITING.value,
}


def load_review_jobs(recommendations_path=RECOMMENDATIONS_JSON, memory_path=MEMORY_FILE):
    """Combine compact review jobs with their persisted workflow status."""
    path = Path(recommendations_path)
    with snapshot() if dataset_name(path) else nullcontext():
        document = read_object(path, {})
        memory = load_memory(memory_path)
    # Normalize the in-memory view only; the next worker run persists cleanup.
    clear_studysmarter_board_companies(memory)
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
        if entry.get("linked_job_ids"):
            job["title"] = entry.get("title") or job["title"]
            job["company"] = entry.get("company") or job.get("company")
        # The listing's own id finds its details in the jobs dataset.
        job["recommendation_id"] = job["id"]
        job["id"] = memory_id
        if studysmarter_board_company(
            job["recommendation_id"], {"company": job.get("company"), "source_names": entry.get("source_names")}
        ):
            job["company"] = entry.get("company") or ""
        job["workflow_status"] = entry.get("workflow_status", WorkflowStatus.NEW.value)
        # ``is_new`` describes the collection run, while a persisted workflow
        # status records that the user has already decided on the job. Never
        # resurrect that transient run marker after the review page reloads.
        job["is_new"] = bool(job.get("is_new")) and (job["workflow_status"] == WorkflowStatus.NEW.value)
        job["application_tracked"] = is_application(entry)
        job["review_note"] = entry.get("review_note") or ""
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
    cards = one_card_per_job(review_jobs)
    applications = company_applications(memory)
    for card in cards:
        card["company_applications"] = same_company_applications(card, applications)
    return cards


def company_applications(memory, as_of=None):
    """List the user's applications by normalized company, with the status the applications page shows."""
    rows = []
    for job_id, entry in memory.items():
        company = normalize_company(entry.get("company") or "")
        if company and is_application(entry):
            rows.append(
                (job_id, company, entry.get("title") or "", application_row(job_id, entry, as_of)["workflow_status"])
            )
    return rows


def same_company_applications(job, applications):
    """Return the applications at the job's company, open ones first, for the review's hint."""
    company = normalize_company(job.get("company") or "")
    if not company:
        return []
    found = [
        {"title": title, "workflow_status": status, "open": status in OPEN_APPLICATION_STATUSES}
        for job_id, other, title, status in applications
        if job_id != job["id"] and companies_match(company, other)
    ]
    return sorted(found, key=lambda item: not item["open"])


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
        card["locations"] = list(dict.fromkeys([*(card.get("locations") or []), *(job.get("locations") or [])]))
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
        availability_warning = "Anzeige nicht mehr verfügbar; automatisch auf Nicht interessant gesetzt."
    elif entry.get("active", True):
        availability_warning = "Im aktuellen Lauf nicht gefunden; Verfügbarkeit bitte über die Anzeige prüfen."
    else:
        availability_warning = (
            "Seit mehreren vollständigen Läufen nicht gefunden; die Stelle ist möglicherweise nicht mehr verfügbar."
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
        "review_note": entry.get("review_note") or "",
        "current_snapshot_missing": True,
        "prefilter_warning": availability_warning,
    }
    job["international"] = is_international_listing(job)
    return job
