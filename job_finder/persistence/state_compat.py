"""Compatibility decoding for supported persisted state formats.

These helpers do not perform I/O or change database schemas. Keep support
for old documents separate from normal workflow updates.
"""

import re
from datetime import datetime

MEMORY_VERSION = 2
NOTIFICATION_STATE_VERSION = 3


def decode_legacy_memory(values):
    """Decode the supported JSON memory document, preserving all job fields."""
    if values.get("version") != MEMORY_VERSION:
        raise ValueError(
            "seen_jobs.json verwendet das alte Format; Datei vor dem ersten neuen Lauf loeschen"
        )
    return values.get("jobs", {})


def restore_initial_discovery_date(entry):
    """Fill only a missing initial discovery date in a loaded mutable entry."""
    history = entry.get("workflow_history")
    if isinstance(history, list) and history:
        first = history[0]
        if (
            isinstance(first, dict)
            and first.get("status") == "new"
            and first.get("occurred_on") is None
        ):
            first["occurred_on"] = first_seen_date(entry)


def first_seen_date(entry):
    """Recover only the initial discovery date, never a later decision date."""
    try:
        timestamp = datetime.fromisoformat(entry["first_seen_at"])
    except (KeyError, TypeError, ValueError):
        return None
    return timestamp.astimezone().date().isoformat()


def legacy_salary_expectation(legacy):
    """Read one formerly formatted salary string without guessing missing values."""
    if not isinstance(legacy, str):
        return None
    match = re.search(r"\b(\d{2,3}(?:[.\s]\d{3})+|\d{4,7})\b", legacy)
    if not match:
        return None
    return int(re.sub(r"\D", "", match.group(1)))


def decode_notification_state(document):
    """Decode versions 1–3 into stable sent and pending job-ID mappings."""
    version = document.get("version")
    if version not in {1, 2, NOTIFICATION_STATE_VERSION}:
        raise ValueError("Benachrichtigungsstatus verwendet eine unbekannte Version")
    sent = {entry.get("job_id", key): entry for key, entry in document.get("sent", {}).items()}
    pending = (
        {}
        if version == 1
        else {
            entry["job_id"]: entry
            for entry in document.get("pending", {}).values()
            if entry.get("job_id") and entry["job_id"] not in sent
        }
    )
    return {"sent": sent, "pending": pending}
