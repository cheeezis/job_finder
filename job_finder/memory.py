"""Local memory for tracking jobs across Job Finder runs."""

import json
from datetime import datetime, timezone
from pathlib import Path

from job_finder.models import WorkflowStatus
from job_finder.paths import MEMORY_FILE

MEMORY_VERSION = 2
INACTIVE_AFTER_MISSED_RUNS = 3


def load_memory(path=MEMORY_FILE):
    """Load the current job memory format."""
    memory_path = Path(path)
    if not memory_path.exists():
        return {}
    values = json.loads(memory_path.read_text(encoding="utf-8"))
    if values.get("version") != MEMORY_VERSION:
        raise ValueError(
            "seen_jobs.json verwendet das alte Format; Datei vor dem "
            "ersten neuen Lauf loeschen"
        )
    return values.get("jobs", {})


def save_memory(memory, path=MEMORY_FILE):
    """Persist the local job memory as UTF-8 JSON."""
    memory_path = Path(path)
    memory_path.parent.mkdir(parents=True, exist_ok=True)
    memory_path.write_text(
        json.dumps(
            {"version": MEMORY_VERSION, "jobs": memory},
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def update_memory(
    jobs,
    memory,
    successful_sources=None,
    inactive_after=INACTIVE_AFTER_MISSED_RUNS,
):
    """Mark jobs as new or known and refresh their last-seen metadata."""
    now = datetime.now(timezone.utc)
    new_count = 0
    known_count = 0
    inactive_count = 0
    reactivated_count = 0
    current_ids = set()

    for job in jobs:
        job.id = resolve_memory_id(job, memory)
        current_ids.add(job.id)
        if job.id in memory:
            known_count += 1
            entry = memory[job.id]
            if not entry.get("active", True):
                reactivated_count += 1
            job.is_new = False
            job.first_seen_at = datetime.fromisoformat(entry["first_seen_at"])
            job.last_seen_at = now
            job.workflow_status = WorkflowStatus(entry["workflow_status"])
            entry["last_seen_at"] = now.isoformat()
            entry["title"] = job.title
            entry["company"] = job.company
            entry["source_urls"] = unique_values(
                entry.get("source_urls", []),
                [source.url for source in job.sources],
            )
            entry["source_names"] = unique_values(
                entry.get("source_names", []),
                job.source_names,
            )
            entry["missed_runs"] = 0
            entry["active"] = True
            continue

        new_count += 1
        job.is_new = True
        job.first_seen_at = now
        job.last_seen_at = now
        job.workflow_status = WorkflowStatus.NEW
        memory[job.id] = {
            "title": job.title,
            "company": job.company,
            "first_seen_at": now.isoformat(),
            "last_seen_at": now.isoformat(),
            "workflow_status": WorkflowStatus.NEW.value,
            "source_urls": [source.url for source in job.sources],
            "source_names": job.source_names,
            "missed_runs": 0,
            "active": True,
        }

    if successful_sources is not None:
        successful = set(successful_sources)
        for job_id, entry in memory.items():
            if job_id in current_ids:
                continue
            known_sources = set(entry.get("source_names") or inferred_sources(job_id))
            if not known_sources or not known_sources.issubset(successful):
                continue
            entry["missed_runs"] = entry.get("missed_runs", 0) + 1
            if entry["missed_runs"] >= inactive_after and entry.get("active", True):
                entry["active"] = False
                inactive_count += 1

    return {
        "new": new_count,
        "known": known_count,
        "inactive": inactive_count,
        "reactivated": reactivated_count,
    }


def resolve_memory_id(job, memory):
    """Reuse a known canonical ID when an exact source URL reappears."""
    current_urls = {source.url for source in job.sources if source.url}
    candidates = [
        job_id
        for job_id, entry in memory.items()
        if job_id == job.id
        or current_urls.intersection(entry.get("source_urls", []))
    ]
    if not candidates:
        return job.id

    manual_candidates = [
        job_id for job_id in candidates if has_manual_state(memory[job_id])
    ]
    if len(manual_candidates) > 1 and job.id in manual_candidates:
        canonical_id = job.id
    else:
        canonical_id = min(
            candidates,
            key=lambda job_id: memory_candidate_key(
                job_id,
                memory[job_id],
                job.id,
            ),
        )
    canonical = memory[canonical_id]
    for candidate_id in candidates:
        if candidate_id == canonical_id:
            continue
        candidate = memory[candidate_id]
        if has_manual_state(candidate):
            continue
        canonical["source_urls"] = unique_values(
            canonical.get("source_urls", []),
            candidate.get("source_urls", []),
        )
        canonical["source_names"] = unique_values(
            canonical.get("source_names", []),
            candidate.get("source_names", []),
        )
        del memory[candidate_id]
    return canonical_id


def memory_candidate_key(job_id, entry, current_job_id):
    """Prefer reviewed history, then the oldest stable memory entry."""
    return (
        not has_manual_state(entry),
        entry.get("first_seen_at", "9999"),
        job_id != current_job_id,
        job_id,
    )


def has_manual_state(entry):
    """Return whether removing an entry could discard a manual decision."""
    return bool(
        entry.get("workflow_status")
        not in {None, WorkflowStatus.NEW.value, WorkflowStatus.REVIEW.value}
        or entry.get("workflow_history")
        or entry.get("review_note")
        or entry.get("personal_rating")
    )


def unique_values(*groups):
    """Combine ordered scalar lists without duplicates or empty values."""
    return list(
        dict.fromkeys(value for group in groups for value in group if value)
    )


def inferred_sources(job_id):
    """Recover the source of older memory entries from their stable ID."""
    source, separator, _identifier = job_id.partition(":")
    return [source] if separator and source else []
