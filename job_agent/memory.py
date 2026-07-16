"""Local memory for tracking jobs across agent runs."""

import json
from datetime import datetime, timezone
from pathlib import Path

from job_agent.models import WorkflowStatus
from job_agent.paths import MEMORY_FILE

MEMORY_VERSION = 2


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


def update_memory(jobs, memory):
    """Mark jobs as new or known and refresh their last-seen metadata."""
    now = datetime.now(timezone.utc)
    new_count = 0
    known_count = 0

    for job in jobs:
        if job.id in memory:
            known_count += 1
            entry = memory[job.id]
            job.is_new = False
            job.first_seen_at = datetime.fromisoformat(entry["first_seen_at"])
            job.last_seen_at = now
            job.workflow_status = WorkflowStatus(entry["workflow_status"])
            entry["last_seen_at"] = now.isoformat()
            entry["title"] = job.title
            entry["company"] = job.company
            entry["source_urls"] = [source.url for source in job.sources]
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
        }

    return {
        "new": new_count,
        "known": known_count,
    }
