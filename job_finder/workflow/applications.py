"""Local application history and derived workflow statistics."""

from datetime import date, datetime, timedelta
from pathlib import Path

from job_finder.models import APPLICATION_STATUSES, OPEN_APPLICATION_STATUSES, WorkflowStatus
from job_finder.paths import MEMORY_FILE, RECOMMENDATIONS_JSON
from job_finder.persistence.application_documents import public_documents
from job_finder.persistence.storage import read_object
from job_finder.workflow.memory import (
    has_application_state as is_application,
    load_memory,
    memory_id_finder,
    memory_source_links,
    preferred_memory_id,
)

RESPONSE_STATUSES = {
    WorkflowStatus.RESPONSE.value,
    WorkflowStatus.INTERVIEW.value,
    WorkflowStatus.REJECTED.value,
    WorkflowStatus.OFFER.value,
}
NO_RESPONSE_AFTER_DAYS = 14


def record_status_change(entry, workflow_status, occurred_on=None, scheduled_for=None):
    """Set the current status and append one dated manual transition."""
    status = WorkflowStatus(workflow_status).value
    explicit_event = occurred_on is not None or scheduled_for is not None
    event = history_event(status, validated_date(occurred_on), scheduled_for)
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
            first_seen = first_seen_date(entry) if previous_status == "new" else None
            history.append(history_event(previous_status, first_seen))
            history_changed = True
    if (previous_status != status or explicit_event) and event not in history:
        history.append(event)
        history_changed = True
    if history_changed:
        return synchronize_current_status(entry)
    entry["workflow_status"] = status
    return status


def first_seen_date(entry):
    """Recover only the initial discovery date, never a later decision date."""
    try:
        timestamp = datetime.fromisoformat(entry["first_seen_at"])
    except (KeyError, TypeError, ValueError):
        return None
    return timestamp.astimezone().date().isoformat()


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


def load_application_overview(memory_path=MEMORY_FILE, as_of=None, recommendations_path=RECOMMENDATIONS_JSON):
    """Return open and completed applications plus statistics for all."""
    memory = load_memory(memory_path)
    reference_date = as_of or date.today()
    links = review_links(memory, recommendations_path)
    all_applications = [
        application_row(job_id, entry, reference_date, links.get(job_id, ()))
        for job_id, entry in memory.items()
        if is_application(entry)
    ]
    all_applications.sort(key=lambda item: item["applied_on"] or item["last_event_on"] or "", reverse=True)
    applications = [item for item in all_applications if item["workflow_status"] in OPEN_APPLICATION_STATUSES]
    completed_applications = [
        item for item in all_applications if item["workflow_status"] not in OPEN_APPLICATION_STATUSES
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
    scheduled_for=None,
    previous_scheduled_for=None,
):
    """Edit one stored workflow event and recalculate the current status."""
    history, index = editable_history_event(
        entry, event_index, previous_status, previous_occurred_on, previous_scheduled_for
    )
    updated_event = history_event(workflow_status, occurred_on, scheduled_for)
    if updated_event in map(normalized_history_event, history[:index] + history[index + 1 :]):
        raise ValueError("Dieses Verlaufsereignis existiert bereits")
    history[index] = updated_event
    current_status = synchronize_current_status(entry)
    return {
        "event_index": index,
        "status": updated_event["status"],
        "occurred_on": updated_event["occurred_on"],
        "scheduled_for": updated_event.get("scheduled_for"),
        "workflow_status": current_status,
    }


def delete_history_event(entry, event_index, previous_status, previous_occurred_on, previous_scheduled_for=None):
    """Delete one stored workflow event and recalculate the current status."""
    history, index = editable_history_event(
        entry, event_index, previous_status, previous_occurred_on, previous_scheduled_for
    )
    history.pop(index)
    return synchronize_current_status(entry)


def editable_history_event(entry, event_index, previous_status, previous_occurred_on, previous_scheduled_for=None):
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
    expected_event = history_event(previous_status, previous_occurred_on, previous_scheduled_for)
    current_event.pop("reason", None)
    if current_event != expected_event:
        raise ValueError("Verlauf wurde zwischenzeitlich geändert; Seite neu laden")
    return history, event_index


def synchronize_current_status(entry):
    """Use the chronologically latest valid event as current status."""
    history = valid_history(entry.get("workflow_history", []))
    status = history[-1]["status"] if history else WorkflowStatus.NEW.value
    entry["workflow_status"] = status
    return status


def review_links(memory, recommendations_path):
    """Return the listing links of current recommendations per memory id, joined as the review joins them."""
    find_memory_ids = memory_id_finder(memory)
    links = {}
    for recommendation in read_object(Path(recommendations_path), {}).get("recommendations", []):
        if candidates := find_memory_ids(recommendation):
            memory_id = preferred_memory_id(candidates, memory, recommendation["id"])
            links.setdefault(memory_id, []).extend(recommendation.get("source_links") or [])
    return links


def application_row(job_id, entry, as_of=None, listing_links=()):
    """Build one compact row with its complete manual timeline and every known listing link."""
    history = valid_history(entry.get("workflow_history", []))
    applied_on = first_event_date(history, {WorkflowStatus.APPLIED.value})
    response_on = first_event_date(history, RESPONSE_STATUSES, not_before=applied_on)
    current_status = entry.get("workflow_status", WorkflowStatus.NEW.value)
    statuses = {event["status"] for event in history}
    if current_status in APPLICATION_STATUSES:
        statuses.add(current_status)
    reference_date = as_of or date.today()
    since = waiting_since(current_status, history, statuses, applied_on)
    if since is not None and date.fromisoformat(since) + timedelta(days=NO_RESPONSE_AFTER_DAYS) <= reference_date:
        current_status = WorkflowStatus.NO_RESPONSE.value
    days_to_response = None
    if applied_on and response_on:
        difference = date.fromisoformat(response_on) - date.fromisoformat(applied_on)
        if difference.days >= 0:
            days_to_response = difference.days
    source_links = memory_source_links(entry, validate_names=True)
    known = {link["url"] for link in source_links}
    for link in listing_links:
        if link.get("url") and link["url"] not in known:
            known.add(link["url"])
            source_links.append({"source": link.get("source") or "listing", "url": link["url"]})
    return {
        "id": job_id,
        "title": entry.get("title", "Unbekannte Stelle"),
        "company": entry.get("company", "Unbekanntes Unternehmen"),
        "url": source_links[0]["url"] if source_links else "",
        "source_links": source_links,
        "active": entry.get("active", True),
        "workflow_status": current_status,
        "review_note": entry.get("review_note", ""),
        "salary_expectation_eur": application_salary_expectation_eur(entry),
        "applied_on": applied_on,
        "response_on": response_on,
        "days_to_response": days_to_response,
        "next_interview_at": (
            first_upcoming_interview(history) if current_status in OPEN_APPLICATION_STATUSES else None
        ),
        "last_interview_at": last_past_interview(history),
        "last_event_on": max(
            (event["occurred_on"] for event in history if event["occurred_on"] is not None), default=None
        ),
        "workflow_history": history,
        "documents": public_documents(entry),
        "linked_listings": [
            {
                "title": linked.get("title", ""),
                "company": linked.get("company", ""),
                "review_note": linked.get("review_note", ""),
            }
            for linked in (entry.get("linked_review_entries") or {}).values()
        ],
        "automatic_no_response": (
            current_status == WorkflowStatus.NO_RESPONSE.value and WorkflowStatus.NO_RESPONSE.value not in statuses
        ),
        "has_response": bool(statuses & RESPONSE_STATUSES),
        "has_interview": WorkflowStatus.INTERVIEW.value in statuses,
        "has_rejection": WorkflowStatus.REJECTED.value in statuses,
        "has_no_response": (
            current_status == WorkflowStatus.NO_RESPONSE.value
            or (current_status == WorkflowStatus.CLOSED.value and WorkflowStatus.NO_RESPONSE.value in statuses)
        ),
        "has_offer": WorkflowStatus.OFFER.value in statuses,
        "has_withdrawal": WorkflowStatus.WITHDRAWN.value in statuses,
    }


def waiting_since(current_status, history, statuses, applied_on):
    """Return the date since which an open application waits for news, or None.

    After applying without any answer the application date counts. After a
    response or an interview the latest event or interview appointment
    counts, so a future appointment keeps the application open.
    """
    if current_status == WorkflowStatus.APPLIED.value:
        return None if statuses & RESPONSE_STATUSES else applied_on
    if current_status not in (WorkflowStatus.RESPONSE.value, WorkflowStatus.INTERVIEW.value):
        return None
    dates = [event["occurred_on"] for event in history if event["occurred_on"] is not None]
    dates += [event["scheduled_for"][:10] for event in history if "scheduled_for" in event]
    return max(dates, default=None)


def application_salary_expectation_eur(entry):
    """Return the stored positive salary in whole euros, or None."""
    value = entry.get("salary_expectation_eur")
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return value
    return None


def valid_history(history):
    """Keep only well-formed status events from local memory."""
    if not isinstance(history, list):
        return []
    events = (normalized_history_event(event, index) for index, event in enumerate(history))
    return sorted(
        (event for event in events if event is not None),
        key=lambda event: (event["occurred_on"] is not None, event["occurred_on"] or ""),
    )


def normalized_history_event(event, event_index=None):
    """Normalize one stored event without mutating local memory."""
    if not isinstance(event, dict):
        return None
    try:
        normalized = history_event(event.get("status"), event.get("occurred_on"), event.get("scheduled_for"))
    except (TypeError, ValueError):
        return None
    if event.get("reason") == "listing_unavailable":
        normalized["reason"] = "listing_unavailable"
    if event_index is not None:
        normalized["event_index"] = event_index
    return normalized


def history_event(workflow_status, occurred_on, scheduled_for=None):
    """Build one validated event while preserving an unknown event date."""
    status = WorkflowStatus(workflow_status).value
    event = {"status": status, "occurred_on": validated_optional_date(occurred_on)}
    appointment = validated_scheduled_for(status, scheduled_for)
    if appointment is not None:
        event["scheduled_for"] = appointment
    return event


def validated_optional_date(value):
    """Return a canonical date while preserving a deliberately unknown date."""
    if value is None or value == "":
        return None
    return validated_date(value)


def validated_scheduled_for(status, value):
    """Return one optional local interview timestamp at minute precision."""
    if value is None or value == "":
        return None
    if status != WorkflowStatus.INTERVIEW.value:
        raise ValueError("Ein Gesprächstermin ist nur beim Status Gespräch möglich")
    if not isinstance(value, str):
        raise ValueError("Gesprächstermin muss als Datum und Uhrzeit angegeben werden")
    try:
        appointment = datetime.strptime(value, "%Y-%m-%dT%H:%M")
    except ValueError as error:
        raise ValueError("Ungültiger Gesprächstermin; erwartet wird YYYY-MM-DDTHH:MM") from error
    return appointment.strftime("%Y-%m-%dT%H:%M")


def first_upcoming_interview(history):
    """Return the next scheduled interview from a normalized history."""
    current = datetime.now().strftime("%Y-%m-%dT%H:%M")
    appointments = [
        event["scheduled_for"]
        for event in history
        if event["status"] == WorkflowStatus.INTERVIEW.value and event.get("scheduled_for", "") >= current
    ]
    return min(appointments, default=None)


def last_past_interview(history):
    """Return the appointment of the latest event while it is a past interview."""
    latest = history[-1] if history else {}
    appointment = latest.get("scheduled_for")
    if latest.get("status") != WorkflowStatus.INTERVIEW.value or not appointment:
        return None
    return appointment if appointment < datetime.now().strftime("%Y-%m-%dT%H:%M") else None


def first_event_date(history, statuses, not_before=None):
    """Return the earliest date for any selected status, optionally on or after a bound."""
    dates = [
        event["occurred_on"]
        for event in history
        if event["status"] in statuses
        and event["occurred_on"] is not None
        and (not_before is None or event["occurred_on"] >= not_before)
    ]
    return min(dates, default=None)


def application_statistics(applications):
    """Derive compact application funnel metrics."""
    total = len(applications)
    response_days = [item["days_to_response"] for item in applications if item["days_to_response"] is not None]
    responses = sum(item["has_response"] for item in applications)
    open_count = sum(item["workflow_status"] in OPEN_APPLICATION_STATUSES for item in applications)
    completed = [item for item in applications if item["workflow_status"] not in OPEN_APPLICATION_STATUSES]
    completed_responses = sum(item["has_response"] for item in completed)
    return {
        "total": total,
        "open": open_count,
        "completed": total - open_count,
        "responses": responses,
        "interviews": sum(item["has_interview"] for item in applications),
        "rejections": sum(item["has_rejection"] for item in applications),
        "no_responses": sum(item["has_no_response"] for item in applications),
        "offers": sum(item["has_offer"] for item in applications),
        "withdrawals": sum(item["has_withdrawal"] for item in applications),
        "response_rate_percent": (round(completed_responses / len(completed) * 100) if completed else 0),
        "average_response_days": (round(sum(response_days) / len(response_days), 1) if response_days else None),
        "response_time_samples": len(response_days),
    }
