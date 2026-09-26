"""Transactional actions for review decisions and application history."""

from job_finder.models import WorkflowStatus
from job_finder.paths import APPLICATION_DOCUMENTS_DIR, MEMORY_FILE
from job_finder.persistence.application_documents import remove_documents, store_documents
from job_finder.workflow.applications import (
    delete_history_event,
    is_application,
    record_status_change,
    synchronize_current_status,
    update_history_event,
)
from job_finder.workflow.memory import edit_job

MAX_REVIEW_NOTE_CHARS = 2000


def update_workflow_status(
    job_id, workflow_status, memory_path=MEMORY_FILE, occurred_on=None, scheduled_for=None
):
    """Validate and persist one manual workflow decision."""
    status = WorkflowStatus(workflow_status)
    with edit_job(job_id, memory_path) as entry:
        return record_status_change(entry, status, occurred_on, scheduled_for)


def update_review_decision(job_id, workflow_status, memory_path=MEMORY_FILE):
    """Persist a review decision without changing an existing application."""
    status = WorkflowStatus(workflow_status)
    if status not in {WorkflowStatus.INTERESTING, WorkflowStatus.INQUIRY, WorkflowStatus.IGNORED}:
        raise ValueError("Ungueltiger Review-Status")
    with edit_job(job_id, memory_path) as entry:
        if is_application(entry):
            return status_result(entry.get("workflow_status", WorkflowStatus.APPLIED.value), True)
        return status_result(record_status_change(entry, status), False)


def update_review_note(job_id, review_note, memory_path=MEMORY_FILE):
    """Store the user's own note on a job without touching its decision; empty removes it."""
    if not isinstance(review_note, str):
        raise ValueError("Notiz muss Text sein")
    note = review_note.strip()
    if len(note) > MAX_REVIEW_NOTE_CHARS:
        raise ValueError(f"Notiz darf höchstens {MAX_REVIEW_NOTE_CHARS} Zeichen lang sein")
    with edit_job(job_id, memory_path) as entry:
        if note:
            entry["review_note"] = note
        else:
            entry.pop("review_note", None)
    return {"review_note": note}


def undo_ignored_decision(job_id, expected_status, memory_path=MEMORY_FILE):
    """Remove the latest ignored transition and restore its prior status."""
    with edit_job(job_id, memory_path) as entry:
        if is_application(entry):
            raise ValueError("Bewerbungsstatus kann hier nicht rückgängig gemacht werden")
        if entry.get("workflow_status") != WorkflowStatus(expected_status).value:
            raise ValueError("Die Stelle wurde zwischenzeitlich geändert")
        if expected_status != WorkflowStatus.IGNORED.value:
            raise ValueError("Nur die letzte Nicht-interessant-Entscheidung ist rückgängig")
        history = entry.get("workflow_history")
        if not isinstance(history, list) or not history:
            raise ValueError("Keine Entscheidung zum Rückgängigmachen gefunden")
        last_event = history[-1]
        if not isinstance(last_event, dict) or last_event.get("status") != expected_status:
            raise ValueError("Die letzte Entscheidung hat sich zwischenzeitlich geändert")
        history.pop()
        return status_result(synchronize_current_status(entry), False)


def start_application(
    job_id,
    memory_path=MEMORY_FILE,
    documents=None,
    documents_dir=APPLICATION_DOCUMENTS_DIR,
    salary_expectation_eur=None,
    salary_period="year",
):
    """Record the first application without overwriting later progress."""
    stored_documents = []
    try:
        with edit_job(job_id, memory_path) as entry:
            if is_application(entry):
                return status_result(
                    entry.get("workflow_status", WorkflowStatus.APPLIED.value), True
                )
            salary_eur = validated_salary_expectation_eur(salary_expectation_eur, salary_period)
            stored_documents = store_documents(
                job_id,
                documents,
                documents_dir,
                company=entry.get("company", ""),
                title=entry.get("title", ""),
            )
            if stored_documents:
                entry["application_documents"] = stored_documents
            if salary_eur is not None:
                entry["salary_expectation_eur"] = salary_eur
            return status_result(record_status_change(entry, WorkflowStatus.APPLIED), True)
    except Exception:
        # Files are created before the database commit and must not survive a
        # failed transaction as unreferenced application documents.
        remove_documents(job_id, stored_documents, documents_dir)
        raise


def status_result(workflow_status, application_tracked):
    """Describe the stored workflow status and whether an application is tracked."""
    return {"workflow_status": workflow_status, "application_tracked": application_tracked}


def validated_salary_expectation_eur(value, period="year"):
    """Return one optional positive annual gross salary in whole euros."""
    if period not in {"year", "month"}:
        raise ValueError("Gehaltszeitraum muss Jahr oder Monat sein")
    if value is None or value == "":
        return None
    if isinstance(value, str) and value.strip().isdecimal():
        salary = int(value.strip())
    elif isinstance(value, int) and not isinstance(value, bool):
        salary = value
    else:
        raise ValueError("Gehaltsvorstellung muss eine ganze Zahl sein")
    if period == "month":
        salary *= 12
    if salary <= 0 or salary > 10_000_000:
        raise ValueError("Gehaltsvorstellung liegt außerhalb des gültigen Bereichs")
    return salary


def update_application_salary(job_id, value, period="year", memory_path=MEMORY_FILE):
    """Change the salary without changing application status or history."""
    salary = validated_salary_expectation_eur(value, period)
    with edit_job(job_id, memory_path) as entry:
        if not is_application(entry):
            raise ValueError("Für diese Stelle ist noch keine Bewerbung gespeichert")
        if salary is None:
            entry.pop("salary_expectation_eur", None)
        else:
            entry["salary_expectation_eur"] = salary
    return {"salary_expectation_eur": salary}


def update_workflow_history(
    job_id,
    event_index,
    previous_status,
    previous_occurred_on,
    workflow_status,
    occurred_on,
    memory_path=MEMORY_FILE,
    scheduled_for=None,
    previous_scheduled_for=None,
):
    """Edit one manual workflow event."""
    with edit_job(job_id, memory_path) as entry:
        return update_history_event(
            entry,
            event_index,
            previous_status,
            previous_occurred_on,
            workflow_status,
            occurred_on,
            scheduled_for,
            previous_scheduled_for,
        )


def delete_workflow_history(
    job_id,
    event_index,
    previous_status,
    previous_occurred_on,
    memory_path=MEMORY_FILE,
    previous_scheduled_for=None,
):
    """Delete one manual workflow event."""
    with edit_job(job_id, memory_path) as entry:
        status = delete_history_event(
            entry, event_index, previous_status, previous_occurred_on, previous_scheduled_for
        )
    return {"workflow_status": status}
