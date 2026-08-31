"""Local memory for tracking jobs across Job Finder runs."""

import json
import sqlite3
from collections import defaultdict
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from job_finder.deduplication import normalize_company, normalize_title
from job_finder.models import WorkflowStatus
from job_finder.paths import LEGACY_MEMORY_FILE, MEMORY_FILE

MEMORY_VERSION = 2
DATABASE_SCHEMA_VERSION = 1
INACTIVE_AFTER_MISSED_RUNS = 3
APPLICATION_STATUSES = {
    WorkflowStatus.APPLIED.value,
    WorkflowStatus.RESPONSE.value,
    WorkflowStatus.INTERVIEW.value,
    WorkflowStatus.REJECTED.value,
    WorkflowStatus.NO_RESPONSE.value,
    WorkflowStatus.OFFER.value,
    WorkflowStatus.CLOSED.value,
}


def load_memory(path=MEMORY_FILE):
    """Load job state from SQLite or an explicitly requested legacy JSON file."""
    memory_path = Path(path)
    if is_sqlite_path(memory_path):
        migrate_legacy_memory(memory_path)
        with database_connection(memory_path) as connection:
            return load_sqlite_memory(connection)
    return load_json_memory(memory_path)


def load_json_memory(memory_path):
    """Load the versioned JSON format retained for migration and isolated tests."""
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
    """Persist job state transactionally in SQLite or atomically in legacy JSON."""
    memory_path = Path(path)
    if is_sqlite_path(memory_path):
        with database_connection(memory_path) as connection:
            replace_sqlite_memory(connection, memory)
        return
    save_json_memory(memory, memory_path)


def save_json_memory(memory, memory_path):
    """Atomically replace a versioned JSON state file."""
    memory_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = memory_path.with_suffix(f"{memory_path.suffix}.tmp")
    temporary.write_text(
        json.dumps(
            {"version": MEMORY_VERSION, "jobs": memory},
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    temporary.replace(memory_path)


@contextmanager
def edit_memory(path=MEMORY_FILE):
    """Lock, expose, and commit one complete state mutation.

    SQLite uses ``BEGIN IMMEDIATE`` so the read-modify-write sequence cannot
    overwrite a concurrent review or collection update. Explicit JSON paths
    remain available for backwards-compatible tests and manual recovery.
    """
    memory_path = Path(path)
    if not is_sqlite_path(memory_path):
        memory = load_json_memory(memory_path)
        yield memory
        save_json_memory(memory, memory_path)
        return

    migrate_legacy_memory(memory_path)
    with database_connection(memory_path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        memory = load_sqlite_memory(connection)
        try:
            yield memory
        except Exception:
            connection.rollback()
            raise
        replace_sqlite_memory(connection, memory, commit=False)
        connection.commit()


def is_sqlite_path(path):
    """Identify database paths without probing or exposing their contents."""
    return Path(path).suffix.casefold() in {".sqlite", ".sqlite3", ".db"}


@contextmanager
def database_connection(path):
    """Open the local state database with safe concurrency defaults."""
    database_path = Path(path)
    database_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(database_path, timeout=20, isolation_level=None)
    try:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=20000")
        initialize_database(connection)
        yield connection
    finally:
        connection.close()


def initialize_database(connection):
    """Create the minimal versioned schema used for mutable job state."""
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS metadata (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS job_state (
            job_id TEXT PRIMARY KEY,
            payload_json TEXT NOT NULL
        );
        """
    )
    connection.execute(
        "INSERT OR IGNORE INTO metadata(key, value) VALUES('schema_version', ?)",
        (str(DATABASE_SCHEMA_VERSION),),
    )
    version = connection.execute(
        "SELECT value FROM metadata WHERE key='schema_version'"
    ).fetchone()[0]
    if version != str(DATABASE_SCHEMA_VERSION):
        raise ValueError(f"Nicht unterstützte Datenbankversion: {version}")


def load_sqlite_memory(connection):
    """Decode every state row while rejecting malformed database content."""
    memory = {}
    for job_id, payload in connection.execute(
        "SELECT job_id, payload_json FROM job_state"
    ):
        entry = json.loads(payload)
        if not isinstance(entry, dict):
            raise ValueError(f"Ungültiger Zustand für Job {job_id}")
        memory[job_id] = entry
    return memory


def replace_sqlite_memory(connection, memory, *, commit=True):
    """Replace all state rows inside one SQLite transaction."""
    if commit:
        connection.execute("BEGIN IMMEDIATE")
    connection.execute("DELETE FROM job_state")
    connection.executemany(
        "INSERT INTO job_state(job_id, payload_json) VALUES(?, ?)",
        [
            (job_id, json.dumps(entry, ensure_ascii=False, separators=(",", ":")))
            for job_id, entry in memory.items()
        ],
    )
    if commit:
        connection.commit()


def migrate_legacy_memory(database_path, legacy_path=None):
    """Import the old JSON state once, preserving that file as a fallback."""
    database_path = Path(database_path)
    if legacy_path is not None:
        source = Path(legacy_path)
    elif database_path.resolve() == Path(MEMORY_FILE).resolve():
        source = Path(LEGACY_MEMORY_FILE)
    else:
        source = database_path.with_name("seen_jobs.json")
    if database_path.exists() or not source.exists():
        return False
    memory = load_json_memory(source)
    with database_connection(database_path) as connection:
        replace_sqlite_memory(connection, memory)
        connection.execute(
            "INSERT OR REPLACE INTO metadata(key, value) VALUES('migrated_from', ?)",
            (source.name,),
        )
    return True


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
            if job.content_changed:
                entry["review_update_pending"] = True
            job.review_update_pending = bool(
                entry.get("review_update_pending", False)
            )
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
            "review_update_pending": False,
        }
        add_memory_index_entry(memory_index, job.id, memory[job.id])

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
    return (
        entry.get("workflow_status") == WorkflowStatus.IGNORED.value
        or has_application_state(entry)
    )


def preferred_memory_id(candidates, memory, current_job_id):
    """Prefer application history, then reviewed and stable memory entries."""
    application_candidates = [
        job_id for job_id in candidates if has_application_state(memory[job_id])
    ]
    manual_candidates = [
        job_id for job_id in candidates if has_manual_state(memory[job_id])
    ]
    preferred = application_candidates or manual_candidates or candidates
    if len(preferred) > 1 and current_job_id in preferred:
        return current_job_id
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
    return list(
        dict.fromkeys(value for group in groups for value in group if value)
    )


def inferred_sources(job_id):
    """Recover the source of older memory entries from their stable ID."""
    source, separator, _identifier = job_id.partition(":")
    return [source] if separator and source else []
