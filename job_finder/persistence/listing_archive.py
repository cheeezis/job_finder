"""Keep a compressed snapshot of every run's listings for later market analysis.

Each run writes one gzip-compressed JSON Lines file with the listing fields of
all jobs it saw, before listings of one job are merged into one card. The user's
decisions, notes and scores are not part of it. Archiving is switched on by
JOBFINDER_ARCHIVE_CONTAINER, a container in JOBFINDER_STORAGE_ACCOUNT; without
it, as locally and in tests, nothing is written.
"""

import gzip
import json
import os
from datetime import UTC, datetime

from job_finder.persistence.document_store import blob_container

ARCHIVED_FIELDS = (
    "id",
    "title",
    "company",
    "locations",
    "sources",
    "description_clean",
    "work_mode",
    "remote_percentage",
    "employment_type",
    "career_levels",
    "salary_min_eur",
    "salary_max_eur",
    "published_at",
    "fetched_at",
    "first_seen_at",
)


def listing_records(jobs):
    """Return the archived fields of each job; call it before listings are merged."""
    records = []
    for job in jobs:
        values = job.to_dict()
        records.append({name: values[name] for name in ARCHIVED_FIELDS})
    return records


def snapshot(records, *, run_id, runner, seen_at):
    """Compress the records, each line tagged with the run that saw it."""
    run = {"run_id": run_id, "runner": runner, "seen_at": seen_at.isoformat()}
    lines = (json.dumps({**record, **run}, ensure_ascii=False, sort_keys=True) for record in records)
    return gzip.compress(("\n".join(lines) + "\n").encode("utf-8"))


def archive_name(seen_at, run_id):
    """Name the file by day, so a reader can select a period by its prefix."""
    return f"{seen_at:%Y/%m/%d}/{seen_at:%H%M%S}-{run_id}.jsonl.gz"


def archive_listings(records, *, run_id, now=None):
    """Upload the run's snapshot; return (name, size in bytes), or None when archiving is off."""
    container = os.environ.get("JOBFINDER_ARCHIVE_CONTAINER")
    if not container:
        return None
    seen_at = now or datetime.now(UTC)
    data = snapshot(records, run_id=run_id, runner=os.environ.get("JOBFINDER_RUNNER", "local"), seen_at=seen_at)
    name = archive_name(seen_at, run_id)
    # Runs never share a name; overwrite=False refuses to replace an earlier snapshot.
    blob_container(container).upload_blob(name, data, overwrite=False)
    return name, len(data)
