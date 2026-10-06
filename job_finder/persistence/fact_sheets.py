"""Fact sheets of the agent, one per job, kept apart from the user's own decisions."""

from psycopg.types.json import Jsonb

from job_finder.persistence.database import transaction

FIELDS = (
    "model",
    "complete",
    "note",
    "fact_sheet",
    "cost_eur",
    "created_at",
    "retryable",
    "attempts",
    "versions",
    "outdated",
)


def save_fact_sheet(job_id, model, sheet, cost_eur, scope="default", *, attempt=1, versions=None):
    """Store a finished fact sheet with the basis it was written on; a later one replaces it."""
    store(scope, job_id, model, True, None, Jsonb(sheet), cost_eur, False, attempt, versions)


def save_aborted(job_id, model, reason, cost_eur, scope="default", *, retryable=False, attempt=1, versions=None):
    """Remember why a job ended: the review names the reason, and only a retryable one is tried again."""
    store(scope, job_id, model, False, reason, None, cost_eur, retryable, attempt, versions)


def store(scope, job_id, model, complete, note, sheet, cost_eur, retryable, attempt, versions):
    # A new sheet starts without changes against its basis.
    with transaction() as connection:
        connection.execute(
            """
            INSERT INTO agent_fact_sheets (
                scope, job_id, model, complete, note, fact_sheet, cost_eur, retryable, attempts, versions
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (scope, job_id) DO UPDATE SET
                created_at = now(), model = EXCLUDED.model, complete = EXCLUDED.complete,
                note = EXCLUDED.note, fact_sheet = EXCLUDED.fact_sheet,
                cost_eur = EXCLUDED.cost_eur, retryable = EXCLUDED.retryable,
                attempts = EXCLUDED.attempts, versions = EXCLUDED.versions, outdated = NULL
            """,
            (
                scope,
                job_id,
                model,
                complete,
                note,
                sheet,
                cost_eur,
                retryable,
                attempt,
                None if versions is None else Jsonb(versions),
            ),
        )


def mark_outdated(changes, scope="default"):
    """Store per job which parts of its basis changed since the sheet; an empty list clears the hint."""
    if not changes:
        return
    with transaction() as connection, connection.cursor() as cursor:
        cursor.executemany(
            "UPDATE agent_fact_sheets SET outdated = %s WHERE scope = %s AND job_id = %s",
            [(parts or None, scope, job_id) for job_id, parts in changes.items()],
        )


def fact_sheets(job_ids=None, scope="default"):
    """Return {job_id: {model, complete, note, fact_sheet, cost_eur, ...}}, all without ids."""
    where = "scope=%s" + ("" if job_ids is None else " AND job_id = ANY(%s)")
    params = (scope,) if job_ids is None else (scope, list(job_ids))
    with transaction() as connection:
        rows = connection.execute(
            f"SELECT job_id, {', '.join(FIELDS)} FROM agent_fact_sheets WHERE {where}", params
        ).fetchall()
    return {row[0]: dict(zip(FIELDS, row[1:])) for row in rows}
