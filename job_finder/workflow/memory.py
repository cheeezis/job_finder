"""PostgreSQL memory and source-independent job lifecycle rules."""

from collections import defaultdict
from contextlib import contextmanager
from copy import deepcopy
from datetime import UTC, datetime

from job_finder.matching.deduplication import (
    companies_match,
    fully_remote,
    locations_match,
    normalize_company,
    normalize_location,
    normalize_title,
)
from job_finder.models import APPLICATION_STATUSES, WorkflowStatus
from job_finder.paths import MEMORY_FILE
from job_finder.persistence.database import lock, memory_scope, snapshot, transaction
from job_finder.persistence.postgres_store import read_memory, write_memory

INACTIVE_AFTER_MISSED_RUNS = 3


def load_memory(path=MEMORY_FILE):
    """Read a consistent PostgreSQL snapshot of the remembered jobs."""
    scope = memory_scope(path)
    with snapshot() as connection:
        return read_memory(connection, scope)


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
        yield memory[job_id]
        write_memory(connection, scope, original, memory)


def update_memory(jobs, memory, successful_sources=None, run_sources=None):
    """Update job identity and discovery state in the supplied objects.

    Mutate both memory and the Job objects in jobs: resolve canonical
    IDs, restore workflow status, and update discovery timestamps and
    is_new. Return counts keyed by new, known, inactive and reactivated.
    This function does not write the resulting state to disk.

    Every listing of one job gets the same ID, also across portals and
    runs (see resolve_memory_id), so several jobs may share one; a known
    entry collects the places and URLs of all of them.

    successful_sources=None disables missed-run accounting, as needed
    for a single manual import. Otherwise, count an absent job only if
    every known source this run collected completed successfully (see
    sources_succeeded). Mark it inactive after INACTIVE_AFTER_MISSED_RUNS
    missed runs; do not change its workflow decision.
    """
    now = datetime.now(UTC)
    counts = dict.fromkeys(("new", "known", "inactive", "reactivated"), 0)
    current_ids = set()
    memory_index = build_memory_index(memory)

    for job in jobs:
        job.id = resolve_memory_id(job, memory, memory_index)
        current_ids.add(job.id)
        if job.id in memory:
            counts["known"] += 1
            entry = memory[job.id]
            if not entry.get("active", True):
                counts["reactivated"] += 1
            job.is_new = False
            job.first_seen_at = datetime.fromisoformat(entry["first_seen_at"])
            job.last_seen_at = now
            job.workflow_status = WorkflowStatus(entry["workflow_status"])
            entry["last_seen_at"] = now.isoformat()
            entry["title"] = job.title
            entry["company"] = job.company
            entry["locations"] = unique_values(entry.get("locations") or [], job.locations)
            entry["source_urls"] = unique_values(
                entry.get("source_urls", []), [source.url for source in job.sources]
            )
            entry["source_names"] = unique_values(entry.get("source_names", []), job.source_names)
            entry["missed_runs"] = 0
            entry["active"] = True
            if remote_job(job):
                entry["fully_remote"] = True
            add_memory_index_entry(memory_index, job.id, entry)
            continue

        counts["new"] += 1
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
        if remote_job(job):
            memory[job.id]["fully_remote"] = True
        add_memory_index_entry(memory_index, job.id, memory[job.id])

    if successful_sources is not None:
        successful = set(successful_sources)
        for job_id, entry in memory.items():
            if job_id in current_ids:
                continue
            if not sources_succeeded(job_id, entry, successful, run_sources):
                continue
            entry["missed_runs"] = entry.get("missed_runs", 0) + 1
            if entry["missed_runs"] >= INACTIVE_AFTER_MISSED_RUNS and entry.get("active", True):
                entry["active"] = False
                counts["inactive"] += 1

    return counts


def resolve_memory_id(job, memory, memory_index=None):
    """Reuse a known canonical ID for the same URL or another listing of the same job.

    Entries found by URL are this listing's own; entries found by title
    (same_job_ids) belong to other listings of the job. Undecided
    candidates fold into the canonical entry, except title matches whose
    places the canonical decision was not made for.
    """
    index = memory_index or build_memory_index(memory)
    current_urls = {source.url for source in job.sources if source.url}
    candidates = unique_values([job.id], *[index["urls"].get(url, []) for url in current_urls])
    candidates = [job_id for job_id in candidates if job_id in memory]
    by_title = []
    if not any(has_manual_state(memory[job_id]) for job_id in candidates):
        # A decision may take this listing only together with its own entries.
        by_title = [
            job_id
            for job_id in same_job_ids(job, memory, index)
            if job_id not in candidates
            and all(may_share_decision(memory[own], memory[job_id]) for own in candidates)
        ]
        candidates += by_title
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
        if candidate_id in by_title and not may_share_decision(candidate, canonical):
            continue
        canonical["source_urls"] = unique_values(
            canonical.get("source_urls", []), candidate.get("source_urls", [])
        )
        canonical["source_names"] = unique_values(
            canonical.get("source_names", []), candidate.get("source_names", [])
        )
        canonical["locations"] = unique_values(
            canonical.get("locations") or [], candidate.get("locations") or []
        )
        if candidate.get("fully_remote"):
            canonical["fully_remote"] = True
        del memory[candidate_id]
    return canonical_id


def same_job_ids(job, memory, index):
    """Return the entries other listings of this job created: same title, matching company.

    An undecided entry takes listings from any portal and place, so they
    share one card. A decided entry takes only listings that bring no new
    place, unless both are fully remote: a job declined in one city must
    still reach the user when it opens in another. An entry whose ads are
    gone only takes reposts of a decision that outlasts them (ignoring or
    applying), as before.
    """
    title = normalize_title(job.title)
    company = normalize_company(job.company)
    if not title or not company:
        return []
    listing = {"locations": job.locations, "fully_remote": remote_job(job)}
    return [
        job_id
        for job_id in index["titles"].get(title, [])
        if job_id in memory
        # The index keeps titles an entry has since changed.
        and normalize_title(memory[job_id].get("title") or "") == title
        and companies_match(company, normalize_company(memory[job_id].get("company") or ""))
        and (memory[job_id].get("active", True) or repost_decision_is_reusable(memory[job_id]))
        and may_share_decision(listing, memory[job_id])
    ]


def may_share_decision(entry, other):
    """Return whether entry may join other without other's decision covering a new place."""
    if not has_manual_state(other) or (remote_entry(entry) and remote_entry(other)):
        return True
    known = other.get("locations") or []
    return all(
        locations_match([place], known)
        for place in entry.get("locations") or []
        if normalize_location(place)
    )


def remote_job(job):
    """Return whether a listing is fully remote or names remote as its place."""
    return fully_remote(job) or names_remote(job.locations)


def remote_entry(entry):
    """Return whether a remembered job was seen fully remote or names remote as its place."""
    return bool(entry.get("fully_remote")) or names_remote(entry.get("locations") or [])


def names_remote(places):
    """Return whether one of the places is remote work rather than a city."""
    return any("remote" in normalize_location(place) for place in places)


def build_memory_index(memory):
    """Index URLs and normalized titles once per complete update."""
    index = {"urls": defaultdict(list), "titles": defaultdict(list)}
    for job_id, entry in memory.items():
        add_memory_index_entry(index, job_id, entry)
    return index


def add_memory_index_entry(index, job_id, entry):
    """Add one current memory entry to the in-process lookup index."""
    for url in entry.get("source_urls", []):
        if job_id not in index["urls"][url]:
            index["urls"][url].append(job_id)
    title = normalize_title(entry.get("title") or "")
    if title and job_id not in index["titles"][title]:
        index["titles"][title].append(job_id)


def repost_decision_is_reusable(entry):
    """Return whether a decision also applies to a repost once the ads are gone."""
    return entry.get("workflow_status") == WorkflowStatus.IGNORED.value or has_application_state(
        entry
    )


def preferred_memory_id(candidates, memory, current_job_id):
    """Prefer application history, then reviewed and stable memory entries."""
    application_candidates = [
        job_id for job_id in candidates if has_application_state(memory[job_id])
    ]
    manual_candidates = [job_id for job_id in candidates if has_manual_state(memory[job_id])]
    preferred = application_candidates or manual_candidates or candidates
    return min(
        preferred, key=lambda job_id: memory_candidate_key(job_id, memory[job_id], current_job_id)
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
        isinstance(event, dict) and event.get("status") in APPLICATION_STATUSES for event in history
    )


def unique_values(*groups):
    """Combine ordered scalar lists without duplicates or empty values."""
    return list(dict.fromkeys(value for group in groups for value in group if value))


def inferred_sources(job_id):
    """Recover the source of older memory entries from their stable ID."""
    source, separator, _identifier = job_id.partition(":")
    return [source] if separator and source else []


def sources_succeeded(job_id, entry, successful_sources, run_sources=None):
    """Require complete coverage by every known source before treating a job as missing.

    With run_sources, only the known sources this run collected count: in a
    split schedule each run answers for its own sources, also for a job that
    the other run's sources list too. Without them every known source must
    have succeeded.
    """
    known = set(entry.get("source_names") or inferred_sources(job_id))
    if run_sources is not None:
        known &= set(run_sources)
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
            if index < len(names) and (not validate_names or isinstance(names[index], str))
            else "listing",
            "url": url,
        }
        for index, url in enumerate(urls)
        if isinstance(url, str) and url
    ]
