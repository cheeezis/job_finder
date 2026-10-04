"""PostgreSQL memory and source-independent job lifecycle rules."""

from collections import defaultdict
from contextlib import contextmanager
from copy import deepcopy
from datetime import UTC, date, datetime

from job_finder.matching.deduplication import (
    BOARD_NAMES,
    companies_match,
    fully_remote,
    locations_match,
    normalize_company,
    normalize_location,
    normalize_title,
)
from job_finder.models import APPLICATION_STATUSES, COMPLETED_APPLICATION_STATUSES, WorkflowStatus
from job_finder.paths import MEMORY_FILE
from job_finder.persistence.database import lock, memory_scope, snapshot, transaction
from job_finder.persistence.postgres_store import read_memory, write_memory

INACTIVE_AFTER_MISSED_RUNS = 3
COMPLETED_APPLICATION_REPOST_DAYS = 30


def load_memory(path=MEMORY_FILE):
    """Read a consistent PostgreSQL snapshot of the remembered jobs."""
    scope = memory_scope(path)
    with snapshot() as connection:
        return read_memory(connection, scope)


def load_workflow_statuses(job_ids, path=MEMORY_FILE):
    """Read only the current decisions needed to dispatch queued cards."""
    with snapshot() as connection:
        return dict(
            connection.execute(
                "SELECT job_id,workflow_status FROM job_state WHERE scope=%s AND job_id=ANY(%s)",
                (memory_scope(path), list(job_ids)),
            ).fetchall()
        )


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


def update_memory(jobs, memory, successful_sources=None, run_sources=None, *, aliases=None):
    """Update job identity and discovery state, without saving it.

    Mutate memory and the Job objects: resolve canonical IDs, restore
    workflow status and update discovery timestamps and is_new; return
    counts keyed by new, known, inactive and reactivated. If supplied,
    aliases collects merged old IDs for related durable records. Every listing
    of one job gets the same ID, also across portals and runs (see
    resolve_memory_id), so several jobs may share one; a known entry
    collects the places and URLs of all of them.

    successful_sources=None disables missed-run accounting, as a single
    manual import needs. Otherwise an absent job counts only if every
    known source this run collected succeeded (see sources_succeeded);
    after INACTIVE_AFTER_MISSED_RUNS missed runs it becomes inactive and
    keeps its workflow decision.
    """
    now = datetime.now(UTC)
    counts = dict.fromkeys(("new", "known", "inactive", "reactivated"), 0)
    current_ids = set()
    clear_studysmarter_board_companies(memory)
    memory_index = build_memory_index(memory)

    for job in jobs:
        job.id = resolve_memory_id(job, memory, memory_index, aliases=aliases)
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
            # A listing without employer keeps the company another listing named.
            entry["company"] = job.company or entry.get("company") or ""
            job.company = entry["company"]
            entry["locations"] = unique_values(entry.get("locations") or [], job.locations)
            entry["source_urls"] = unique_values(entry.get("source_urls", []), [source.url for source in job.sources])
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
        if job.published_at is not None:
            memory[job.id]["published_at"] = job.published_at.isoformat()
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


def clear_studysmarter_board_companies(memory):
    """Remove legacy board placeholders, including listings absent from this run.

    Share the source adapter's names and restrict cleanup to StudySmarter
    provenance. Other employers and all workflow fields stay untouched.
    """
    for job_id, entry in memory.items():
        if studysmarter_board_company(job_id, entry):
            entry["company"] = ""


def studysmarter_board_company(job_id, entry):
    """Recognize the source adapter's employer placeholders using saved provenance."""
    sources = entry.get("source_names") or inferred_sources(job_id)
    return "studysmarter" in sources and str(entry.get("company") or "").strip().casefold() in BOARD_NAMES


def resolve_memory_id(job, memory, memory_index=None, *, aliases=None):
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
            if job_id not in candidates and all(may_share_decision(memory[own], memory[job_id]) for own in candidates)
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
        for field in ("source_urls", "source_names"):
            canonical[field] = unique_values(canonical.get(field, []), candidate.get(field, []))
        canonical["locations"] = unique_values(canonical.get("locations") or [], candidate.get("locations") or [])
        if candidate.get("fully_remote"):
            canonical["fully_remote"] = True
        if aliases is not None:
            aliases[candidate_id] = canonical_id
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
    if not title:
        return []
    if not company:
        return applications_with_same_title_and_place(job, title, memory, index)
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
        and may_reuse_application(job, memory[job_id], memory)
    ]


def applications_with_same_title_and_place(job, title, memory, index):
    """Return applications a listing without employer belongs to: same title, every place known.

    Some portals name the board they took an ad from instead of the employer.
    Such a listing joins only an application, where a second job with the same
    title in the same city is unlikely; being remote is not enough here.
    """
    places = [place for place in job.locations if normalize_location(place)]
    return [
        job_id
        for job_id in index["titles"].get(title, [])
        if job_id in memory
        and normalize_title(memory[job_id].get("title") or "") == title
        and has_application_state(memory[job_id])
        and may_reuse_application(job, memory[job_id], memory)
        and places
        and all(locations_match([place], memory[job_id].get("locations") or []) for place in places)
    ]


def may_reuse_application(job, entry, memory):
    """Keep distant listings apart after an application has completed.

    Only title-based matching calls this guard: an existing source ID or URL
    still identifies the same listing. Missing dates cannot establish that a
    completed application belongs to the same advertising period.
    """
    if entry.get("workflow_status") not in COMPLETED_APPLICATION_STATUSES:
        return True
    previous_date = listing_date(entry)
    current_date = (
        job.published_at
        or listing_date(memory.get(job.id, {}))
        or stored_date(job.first_seen_at)
        or datetime.now(UTC).date()
    )
    return previous_date is not None and abs((current_date - previous_date).days) <= COMPLETED_APPLICATION_REPOST_DAYS


def listing_date(entry):
    """Prefer the original publication date, falling back to first discovery."""
    return stored_date(entry.get("published_at")) or stored_date(entry.get("first_seen_at"))


def stored_date(value):
    """Read one persisted ISO date or timestamp without using the last crawl date."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value).date()
    except ValueError:
        return None


def may_share_decision(entry, other):
    """Return whether entry may join other without other's decision covering a new place."""
    if not has_manual_state(other) or (remote_entry(entry) and remote_entry(other)):
        return True
    known = other.get("locations") or []
    return all(locations_match([place], known) for place in entry.get("locations") or [] if normalize_location(place))


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
    return entry.get("workflow_status") == WorkflowStatus.IGNORED.value or has_application_state(entry)


def preferred_memory_id(candidates, memory, current_job_id):
    """Prefer application history, then reviewed and stable memory entries."""
    application_candidates = [job_id for job_id in candidates if has_application_state(memory[job_id])]
    manual_candidates = [job_id for job_id in candidates if has_manual_state(memory[job_id])]
    preferred = application_candidates or manual_candidates or candidates
    return min(preferred, key=lambda job_id: memory_candidate_key(job_id, memory[job_id], current_job_id))


def memory_candidate_key(job_id, entry, current_job_id):
    """Prefer reviewed history, then the oldest stable memory entry."""
    return (not has_manual_state(entry), entry.get("first_seen_at", "9999"), job_id != current_job_id, job_id)


def has_manual_state(entry):
    """Return whether removing an entry could discard a manual decision."""
    return bool(
        entry.get("workflow_status") not in {None, WorkflowStatus.NEW.value, WorkflowStatus.REVIEW.value}
        or entry.get("workflow_history")
        or entry.get("review_note")
        or entry.get("personal_rating")
    )


def has_application_state(entry):
    """Return whether an entry represents a current or past application."""
    history = entry.get("workflow_history", [])
    return entry.get("workflow_status") in APPLICATION_STATUSES or any(
        isinstance(event, dict) and event.get("status") in APPLICATION_STATUSES
        for event in (history if isinstance(history, list) else [])
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


def memory_id_finder(memory):
    """Find each recommendation's memory rows, in memory order, via one per-request URL index."""
    index = {}
    for memory_id, entry in memory.items():
        for url in entry.get("source_urls", []):
            index.setdefault(url, []).append(memory_id)
    positions = {memory_id: position for position, memory_id in enumerate(memory)}

    def find(job):
        found = {memory_id for url in job_urls(job) for memory_id in index.get(url, [])}
        if job["id"] in memory:
            found.add(job["id"])
        return sorted(found, key=positions.__getitem__)

    return find


def job_urls(job):
    """Return the listing URLs a recommendation can be matched by."""
    urls = {link.get("url") for link in job.get("source_links", []) if isinstance(link, dict) and link.get("url")}
    if job.get("url"):
        urls.add(job["url"])
    return urls
