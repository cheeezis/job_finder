"""Fact sheets of the agent, one per job, kept apart from the user's own decisions."""

from psycopg.types.json import Jsonb

from job_finder.persistence.database import transaction


def save_fact_sheet(job_id, model, sheet, cost_eur, scope="default"):
    """Store a finished fact sheet; a later one for the same job replaces it."""
    store(scope, job_id, model, True, None, Jsonb(sheet), cost_eur)


def save_aborted(job_id, model, reason, cost_eur, scope="default"):
    """Remember that a limit ended the job: the review names the reason, no retry follows."""
    store(scope, job_id, model, False, reason, None, cost_eur)


def store(scope, job_id, model, complete, note, sheet, cost_eur):
    with transaction() as connection:
        connection.execute(
            """
            INSERT INTO agent_fact_sheets (
                scope, job_id, model, complete, note, fact_sheet, cost_eur
            ) VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (scope, job_id) DO UPDATE SET
                created_at = now(), model = EXCLUDED.model, complete = EXCLUDED.complete,
                note = EXCLUDED.note, fact_sheet = EXCLUDED.fact_sheet,
                cost_eur = EXCLUDED.cost_eur
            """,
            (scope, job_id, model, complete, note, sheet, cost_eur),
        )


def fact_sheets(job_ids=None, scope="default"):
    """Return {job_id: {model, complete, note, fact_sheet, cost_eur, created_at}}, all without ids."""
    where = "scope=%s" + ("" if job_ids is None else " AND job_id = ANY(%s)")
    params = (scope,) if job_ids is None else (scope, list(job_ids))
    with transaction() as connection:
        rows = connection.execute(
            "SELECT job_id, model, complete, note, fact_sheet, cost_eur, created_at "
            f"FROM agent_fact_sheets WHERE {where}",
            params,
        ).fetchall()
    return {
        job_id: {
            "model": model,
            "complete": complete,
            "note": note,
            "fact_sheet": sheet,
            "cost_eur": cost_eur,
            "created_at": created_at,
        }
        for job_id, model, complete, note, sheet, cost_eur, created_at in rows
    }
