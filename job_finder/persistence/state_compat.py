"""Decoding helpers for persisted workflow and notification state.

These helpers do not perform I/O or change database schemas.
"""

from datetime import datetime

NOTIFICATION_STATE_VERSION = 3


def first_seen_date(entry):
    """Recover only the initial discovery date, never a later decision date."""
    try:
        timestamp = datetime.fromisoformat(entry["first_seen_at"])
    except (KeyError, TypeError, ValueError):
        return None
    return timestamp.astimezone().date().isoformat()


def decode_notification_state(document):
    """Decode the current version into stable sent and pending job-ID mappings."""
    if document.get("version") != NOTIFICATION_STATE_VERSION:
        raise ValueError("Benachrichtigungsstatus verwendet eine unbekannte Version")
    sent = {entry.get("job_id", key): entry for key, entry in document.get("sent", {}).items()}
    pending = {
        entry["job_id"]: entry
        for entry in document.get("pending", {}).values()
        if entry.get("job_id") and entry["job_id"] not in sent
    }
    return {"sent": sent, "pending": pending}
