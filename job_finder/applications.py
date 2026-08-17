"""Local application history and derived workflow statistics."""

from datetime import date

from job_finder.memory import load_memory
from job_finder.models import WorkflowStatus
from job_finder.paths import MEMORY_FILE


APPLICATION_STATUSES = (
    WorkflowStatus.APPLIED.value,
    WorkflowStatus.RESPONSE.value,
    WorkflowStatus.INTERVIEW.value,
    WorkflowStatus.REJECTED.value,
    WorkflowStatus.NO_RESPONSE.value,
    WorkflowStatus.OFFER.value,
    WorkflowStatus.CLOSED.value,
)
OPEN_APPLICATION_STATUSES = {
    WorkflowStatus.APPLIED.value,
    WorkflowStatus.RESPONSE.value,
    WorkflowStatus.INTERVIEW.value,
}
RESPONSE_STATUSES = {
    WorkflowStatus.RESPONSE.value,
    WorkflowStatus.INTERVIEW.value,
    WorkflowStatus.REJECTED.value,
    WorkflowStatus.OFFER.value,
}


def record_status_change(entry, workflow_status, occurred_on=None):
    """Set the current status and append one dated manual transition."""
    status = WorkflowStatus(workflow_status).value
    explicit_date = occurred_on is not None
    event = {
        "status": status,
        "occurred_on": validated_date(occurred_on),
    }
    previous_status = entry.get("workflow_status", WorkflowStatus.NEW.value)
    history = entry.get("workflow_history")
    if not isinstance(history, list):
        history = []
        entry["workflow_history"] = history
    history_changed = False
    if previous_status != status and not history:
        try:
            previous_status = WorkflowStatus(previous_status).value
        except ValueError:
            pass
        else:
            history.append(
                {
                    "status": previous_status,
                    "occurred_on": None,
                }
            )
            history_changed = True
    if (previous_status != status or explicit_date) and event not in history:
        history.append(event)
        history_changed = True
    if history_changed:
        return synchronize_current_status(entry)
    entry["workflow_status"] = status
    return status


def validated_date(value):
    """Return one canonical ISO date, defaulting to today."""
    if value is None:
        return date.today().isoformat()
    if not isinstance(value, str):
        raise ValueError("Datum muss als YYYY-MM-DD angegeben werden")
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError as error:
        raise ValueError("Ungueltiges Datum; erwartet wird YYYY-MM-DD") from error


def load_application_overview(memory_path=MEMORY_FILE):
    """Return open and completed applications plus statistics for all."""
    memory = load_memory(memory_path)
    all_applications = [
        application_row(job_id, entry)
        for job_id, entry in memory.items()
        if is_application(entry)
    ]
    all_applications.sort(
        key=lambda item: item["applied_on"] or item["last_event_on"] or "",
        reverse=True,
    )
    applications = [
        item
        for item in all_applications
        if item["workflow_status"] in OPEN_APPLICATION_STATUSES
    ]
    completed_applications = [
        item
        for item in all_applications
        if item["workflow_status"] not in OPEN_APPLICATION_STATUSES
    ]
    return {
        "applications": applications,
        "completed_applications": completed_applications,
        "statistics": application_statistics(all_applications),
        "application_statuses": list(APPLICATION_STATUSES),
        "workflow_statuses": [status.value for status in WorkflowStatus],
    }


def update_history_event(
    entry,
    event_index,
    previous_status,
    previous_occurred_on,
    workflow_status,
    occurred_on,
):
    """Edit one stored workflow event and recalculate the current status."""
    history, index = editable_history_event(
        entry,
        event_index,
        previous_status,
        previous_occurred_on,
    )
    status = WorkflowStatus(workflow_status).value
    event_date = validated_optional_date(occurred_on)
    for other_index, other_event in enumerate(history):
        if other_index == index:
            continue
        normalized = normalized_history_event(other_event)
        if normalized is None:
            continue
        if (
            normalized["status"] == status
            and normalized["occurred_on"] == event_date
        ):
            raise ValueError("Dieses Verlaufsereignis existiert bereits")
    updated_event = dict(history[index])
    updated_event.update({"status": status, "occurred_on": event_date})
    history[index] = updated_event
    current_status = synchronize_current_status(entry)
    return {
        "event_index": index,
        "status": status,
        "occurred_on": event_date,
        "workflow_status": current_status,
    }


def delete_history_event(
    entry,
    event_index,
    previous_status,
    previous_occurred_on,
):
    """Delete one stored workflow event and recalculate the current status."""
    history, index = editable_history_event(
        entry,
        event_index,
        previous_status,
        previous_occurred_on,
    )
    history.pop(index)
    return synchronize_current_status(entry)


def editable_history_event(
    entry,
    event_index,
    previous_status,
    previous_occurred_on,
):
    """Return a mutable history and one validated raw event index."""
    if isinstance(event_index, bool) or not isinstance(event_index, int):
        raise ValueError("Verlaufsindex muss eine Zahl sein")
    history = entry.get("workflow_history")
    if not isinstance(history, list):
        raise ValueError("Für diese Bewerbung ist kein Verlauf gespeichert")
    if event_index < 0 or event_index >= len(history):
        raise ValueError("Verlaufsereignis wurde nicht gefunden")
    current_event = normalized_history_event(history[event_index])
    if current_event is None:
        raise ValueError("Verlaufsereignis ist ungültig")
    expected_event = {
        "status": WorkflowStatus(previous_status).value,
        "occurred_on": validated_optional_date(previous_occurred_on),
    }
    if current_event != expected_event:
        raise ValueError(
            "Verlauf wurde zwischenzeitlich geändert; Seite neu laden"
        )
    return history, event_index


def synchronize_current_status(entry):
    """Use the chronologically latest valid event as current status."""
    history = valid_history(entry.get("workflow_history", []))
    status = (
        history[-1]["status"]
        if history
        else WorkflowStatus.NEW.value
    )
    entry["workflow_status"] = status
    return status


def is_application(entry):
    """Recognize current and historical applications."""
    current_status = entry.get("workflow_status")
    history = entry.get("workflow_history", [])
    if not isinstance(history, list):
        history = []
    return current_status in APPLICATION_STATUSES or any(
        isinstance(event, dict)
        and event.get("status") in APPLICATION_STATUSES
        for event in history
    )


def application_row(job_id, entry):
    """Build one compact row with its complete manual timeline."""
    history = valid_history(entry.get("workflow_history", []))
    applied_on = first_event_date(history, {WorkflowStatus.APPLIED.value})
    response_on = first_response_date(history, applied_on)
    current_status = entry.get("workflow_status", WorkflowStatus.NEW.value)
    statuses = {event["status"] for event in history}
    if current_status in APPLICATION_STATUSES:
        statuses.add(current_status)
    days_to_response = None
    if applied_on and response_on:
        difference = date.fromisoformat(response_on) - date.fromisoformat(
            applied_on
        )
        if difference.days >= 0:
            days_to_response = difference.days
    source_urls = entry.get("source_urls", [])
    if not isinstance(source_urls, list):
        source_urls = []
    url = next(
        (
            source_url
            for source_url in source_urls
            if isinstance(source_url, str)
        ),
        "",
    )
    return {
        "id": job_id,
        "title": entry.get("title", "Unbekannte Stelle"),
        "company": entry.get("company", "Unbekanntes Unternehmen"),
        "url": url,
        "active": entry.get("active", True),
        "workflow_status": current_status,
        "review_note": entry.get("review_note", ""),
        "applied_on": applied_on,
        "response_on": response_on,
        "days_to_response": days_to_response,
        "last_event_on": max(
            (
                event["occurred_on"]
                for event in history
                if event["occurred_on"] is not None
            ),
            default=None,
        ),
        "workflow_history": history,
        "has_response": bool(statuses & RESPONSE_STATUSES),
        "has_interview": WorkflowStatus.INTERVIEW.value in statuses,
        "has_rejection": WorkflowStatus.REJECTED.value in statuses,
        "has_no_response": (
            current_status == WorkflowStatus.NO_RESPONSE.value
            or (
                current_status == WorkflowStatus.CLOSED.value
                and WorkflowStatus.NO_RESPONSE.value in statuses
            )
        ),
        "has_offer": WorkflowStatus.OFFER.value in statuses,
    }


def valid_history(history):
    """Keep only well-formed status events from local memory."""
    if not isinstance(history, list):
        return []
    valid = []
    for event_index, event in enumerate(history):
        normalized = normalized_history_event(event, event_index)
        if normalized is None:
            continue
        valid.append(normalized)
    valid.sort(
        key=lambda event: (
            event["occurred_on"] is not None,
            event["occurred_on"] or "",
        )
    )
    return valid


def normalized_history_event(event, event_index=None):
    """Normalize one stored event without mutating local memory."""
    if not isinstance(event, dict):
        return None
    try:
        status = WorkflowStatus(event.get("status")).value
        occurred_on = validated_optional_date(event.get("occurred_on"))
    except (TypeError, ValueError):
        return None
    normalized = {"status": status, "occurred_on": occurred_on}
    if event_index is not None:
        normalized["event_index"] = event_index
    return normalized


def validated_optional_date(value):
    """Return a canonical date while preserving a deliberately unknown date."""
    if value is None or value == "":
        return None
    return validated_date(value)


def first_event_date(history, statuses):
    """Return the earliest date for any selected status."""
    dates = [
        event["occurred_on"]
        for event in history
        if event["status"] in statuses
        and event["occurred_on"] is not None
    ]
    return min(dates, default=None)


def first_response_date(history, applied_on):
    """Return the first dated response on or after the application."""
    dates = [
        event["occurred_on"]
        for event in history
        if event["status"] in RESPONSE_STATUSES
        and event["occurred_on"] is not None
        and (applied_on is None or event["occurred_on"] >= applied_on)
    ]
    return min(dates, default=None)


def application_statistics(applications):
    """Derive compact application funnel metrics."""
    total = len(applications)
    response_days = [
        item["days_to_response"]
        for item in applications
        if item["days_to_response"] is not None
    ]
    responses = sum(item["has_response"] for item in applications)
    open_count = sum(
        item["workflow_status"] in OPEN_APPLICATION_STATUSES
        for item in applications
    )
    return {
        "total": total,
        "open": open_count,
        "completed": total - open_count,
        "responses": responses,
        "interviews": sum(item["has_interview"] for item in applications),
        "rejections": sum(item["has_rejection"] for item in applications),
        "no_responses": sum(
            item["has_no_response"]
            for item in applications
        ),
        "offers": sum(item["has_offer"] for item in applications),
        "response_rate_percent": round(responses / total * 100) if total else 0,
        "average_response_days": (
            round(sum(response_days) / len(response_days), 1)
            if response_days
            else None
        ),
        "response_time_samples": len(response_days),
    }
