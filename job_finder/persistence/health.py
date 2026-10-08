"""Backlogs worth watching after a run: unsent Discord messages and fact sheets that ended early."""

from datetime import UTC, datetime

from psycopg import errors

from job_finder.persistence.database import snapshot


def backlog(now=None):
    """Return counts and the age of the oldest pending message; an older database without them gives zeros."""
    now = now or datetime.now(UTC)
    try:
        with snapshot() as connection:
            outbox = connection.execute(
                "SELECT count(*), min(extra->>'created_at') FROM notifications WHERE delivery_state = 'pending'"
            ).fetchone()
            sheets = connection.execute(
                "SELECT count(*) FILTER (WHERE NOT complete), count(*) FILTER (WHERE retryable) FROM agent_fact_sheets"
            ).fetchone()
    except (errors.UndefinedTable, errors.UndefinedColumn):
        return {"outbox_pending": 0, "outbox_oldest_hours": 0, "fact_sheets_aborted": 0, "fact_sheets_retryable": 0}
    # Aggregates always return a row; the fallbacks only satisfy the type checker.
    pending, oldest = outbox or (0, None)
    aborted, retryable = sheets or (0, 0)
    return {
        "outbox_pending": pending,
        "outbox_oldest_hours": age_hours(oldest, now),
        "fact_sheets_aborted": aborted,
        "fact_sheets_retryable": retryable,
    }


def age_hours(timestamp, now):
    """Hours since an ISO timestamp, read as UTC without a zone; 0 without one."""
    if not timestamp:
        return 0
    moment = datetime.fromisoformat(timestamp)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return round(max((now - moment).total_seconds(), 0) / 3600, 1)
