"""PostgreSQL memory and source-independent job lifecycle rules."""

from collections import defaultdict
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone

from job_finder.matching.deduplication import normalize_company, normalize_title
from job_finder.models import APPLICATION_STATUSES, WorkflowStatus
from job_finder.paths import MEMORY_FILE
from job_finder.persistence.database import lock, memory_scope, snapshot, transaction
from job_finder.persistence.postgres_store import read_memory, write_memory
from job_finder.persistence.state_compat import MEMORY_VERSION as MEMORY_VERSION
from job_finder.persistence.state_compat import first_seen_date as first_seen_date
from job_finder.persistence.state_compat import restore_initial_discovery_date

INACTIVE_AFTER_MISSED_RUNS = 3


def load_memory(path=MEMORY_FILE):
    """Read a consistent PostgreSQL snapshot of the remembered jobs."""
    scope = memory_scope(path)
    with snapshot() as connection:
        memory = read_memory(connection, scope)
        for entry in memory.values():
            restore_initial_discovery_date(entry)
        return memory


def save_memory(memory, path=MEMORY_FILE):
    """Explicitly replace a scope; ordinary edits use edit_memory or edit_job."""
    with edit_memory(path) as current:
        current.clear()
        current.update(memory)


@contextmanager
def edit_memory(path=MEMORY_FILE):
    """Serialize bulk matching/merging and persist only changed job records."""
    scope = memory_scope(path)
    with transaction() as connection:
        lock(connection, "memory:" + scope)
        memory = read_memory(connection, scope)
        original = deepcopy(memory)
        for entry in memory.values():
            restore_initial_discovery_date(entry)
        yield memory
        write_memory(connection, scope, original, memory)


@contextmanager
def edit_job(job_id, path=MEMORY_FILE):
    """Lock one job, allowing unrelated review edits to proceed concurrently."""
    scope = memory_scope(path)
    with transaction() as connection:
        # Bulk discovery takes the exclusive version of this lock. Individual
        # edits share it and then lock their own row, always in that order.
        lock(connection, "memory:" + scope, shared=True)
        memory = read_memory(connection, scope, job_id, for_update=True)
        if job_id not in memory:
            raise KeyError(f"Unbekannte Job-ID: {job_id}")
        original = deepcopy(memory)
        restore_initial_discovery_date(memory[job_id])
        yield memory[job_id]
        write_memory(connection, scope, original, memory)


def update_memory(
    jobs,
    memory,
    successful_sources=None,
    inactive_after=INACTIVE_AFTER_MISSED_RUNS,
):
    """Update job identity and discovery state in the supplied objects.

    Mutate both memory and the Job objects in jobs: resolve canonical
    IDs, restore workflow status, and update discovery timestamps and
    is_new. Return counts keyed by new, known, inactive and reactivated.
    This function does not write the resulting state to disk.

    successful_sources=None disables missed-run accounting, as needed
    for a single manual import. Otherwise, count an absent job only if
    every known source completed successfully. Mark it inactive after
    inactive_after missed runs; do not change its workflow decision.
    """
    now = datetime.now(timezone.utc)
    new_count = 0
    known_count = 0
    inactive_count = 0
    reactivated_count = 0
    current_ids = set()
    memory_index = build_memory_index(memory)

    for job in jobs:
        job.id = resolve_memory_id(job, memory, memory_index)
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
            entry["locations"] = list(job.locations)
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
            add_memory_index_entry(memory_index, job.id, entry)
            continue

        new_count += 1
        job.is_new = True
        job.first_seen_at = now
        job.last_seen_at = now
        job.workflow_status = WorkflowStatus.NEW
        memory[job.id] = {
            "title": job.title,
            "company": job.company,
            "locations": list(job.locations),
            "first_seen_at": now.isoformat(),
            "last_seen_at": now.isoformat(),
            "workflow_status": WorkflowStatus.NEW.value,
            "source_urls": [source.url for source in job.sources],
            "source_names": job.source_names,
            "missed_runs": 0,
            "active": True,
        }
        add_memory_index_entry(memory_index, job.id, memory[job.id])

    if successful_sources is not None:
        successful = set(successful_sources)
        for job_id, entry in memory.items():
            if job_id in current_ids:
                continue
            if not sources_succeeded(job_id, entry, successful):
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


def resolve_memory_id(job, memory, memory_index=None):
    """Reuse a known canonical ID for the same URL or a decided repost."""
    index = memory_index or build_memory_index(memory)
    current_urls = {source.url for source in job.sources if source.url}
    candidates = [job.id] if job.id in memory else []
    candidates = unique_values(
        candidates,
        *[index["urls"].get(url, []) for url in current_urls],
    )
    candidates = [job_id for job_id in candidates if job_id in memory]
    if not any(has_manual_state(memory[job_id]) for job_id in candidates):
        fingerprint = repost_fingerprint(job.title, job.company, job.locations)
        candidates = unique_values(
            candidates,
            index["reposts"].get(fingerprint, []) if fingerprint else [],
        )
        candidates = [job_id for job_id in candidates if job_id in memory]
    if not candidates:
        return job.id

    canonical_id = preferred_memory_id(candidates, memory, job.id)
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


def build_memory_index(memory):
    """Index URLs and decided repost fingerprints once per complete update."""
    index = {"urls": defaultdict(list), "reposts": defaultdict(list)}
    for job_id, entry in memory.items():
        add_memory_index_entry(index, job_id, entry)
    return index


def add_memory_index_entry(index, job_id, entry):
    """Add one current memory entry to the in-process lookup index."""
    for url in entry.get("source_urls", []):
        if job_id not in index["urls"][url]:
            index["urls"][url].append(job_id)
    if not repost_decision_is_reusable(entry):
        return
    fingerprint = repost_fingerprint(
        entry.get("title", ""), entry.get("company", ""), entry.get("locations", [])
    )
    if fingerprint and job_id not in index["reposts"][fingerprint]:
        index["reposts"][fingerprint].append(job_id)


def repost_fingerprint(title_value, company_value, locations=None):
    """Build a conservative title/company/location key for decided ads."""
    title = normalize_title(title_value)
    company = normalize_company(company_value)
    normalized_locations = sorted(
        {
            " ".join(normalize_title(value).split())
            for value in (locations or [])
            if value
        }
    )
    if not title or not company or not normalized_locations:
        return None
    return title, company, tuple(normalized_locations)


def repost_decision_is_reusable(entry):
    """Limit fuzzy repost matching to explicit rejection or application state."""
    return entry.get(
        "workflow_status"
    ) == WorkflowStatus.IGNORED.value or has_application_state(entry)


def preferred_memory_id(candidates, memory, current_job_id):
    """Prefer application history, then reviewed and stable memory entries."""
    application_candidates = [
        job_id for job_id in candidates if has_application_state(memory[job_id])
    ]
    manual_candidates = [
        job_id for job_id in candidates if has_manual_state(memory[job_id])
    ]
    preferred = application_candidates or manual_candidates or candidates
    return min(
        preferred,
        key=lambda job_id: memory_candidate_key(
            job_id,
            memory[job_id],
            current_job_id,
        ),
    )


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


def has_application_state(entry):
    """Return whether an entry represents a current or past application."""
    history = entry.get("workflow_history", [])
    if not isinstance(history, list):
        history = []
    return entry.get("workflow_status") in APPLICATION_STATUSES or any(
        isinstance(event, dict) and event.get("status") in APPLICATION_STATUSES
        for event in history
    )


def unique_values(*groups):
    """Combine ordered scalar lists without duplicates or empty values."""
    return list(dict.fromkeys(value for group in groups for value in group if value))


def inferred_sources(job_id):
    """Recover the source of older memory entries from their stable ID."""
    source, separator, _identifier = job_id.partition(":")
    return [source] if separator and source else []


def sources_succeeded(job_id, entry, successful_sources):
    """Require complete coverage of every known source before treating a job as missing."""
    known = set(entry.get("source_names") or inferred_sources(job_id))
    return bool(known) and known.issubset(successful_sources)


def memory_source_links(entry, *, validate_names=False):
    """Pair persisted URLs with available labels, retaining older sparse data."""
    names = entry.get("source_names", [])
    if not isinstance(names, list):
        names = []
    urls = entry.get("source_urls", [])
    if validate_names and not isinstance(urls, list):
        urls = []
    return [
        {
            "source": names[index]
            if index < len(names)
            and (not validate_names or isinstance(names[index], str))
            else "listing",
            "url": url,
        }
        for index, url in enumerate(urls)
        if isinstance(url, str) and url
    ]
