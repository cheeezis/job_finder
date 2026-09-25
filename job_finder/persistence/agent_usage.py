"""Ledger of the agent's model calls, the basis for its daily and monthly limits.

Rows are deliberately not tied to job_state: deleting a job must not delete
its costs, or the agent could spend the same money twice.
"""

from job_finder.persistence.database import transaction


def record_model_call(job_id, model, usage, cost_eur):
    """Append one call; commits on its own unless a transaction is already open."""
    with transaction() as connection:
        connection.execute(
            """
            INSERT INTO agent_usage (
                job_id, model, input_tokens, cached_input_tokens,
                output_tokens, reasoning_tokens, cost_eur
            ) VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (
                job_id,
                model,
                usage.input_tokens,
                usage.cached_input_tokens,
                usage.output_tokens,
                usage.reasoning_tokens,
                cost_eur,
            ),
        )


def spent_today_and_this_month(at=None):
    """Return euro totals since midnight and since the first of the month, German time.

    The database computes both boundaries with its own clock and time zone
    data, the same clock that stamps the calls; at replaces now() in tests.
    """
    with transaction() as connection:
        return connection.execute(
            """
            SELECT
                coalesce(sum(cost_eur) FILTER (
                    WHERE called_at >= date_trunc('day', moment, 'Europe/Berlin')
                ), 0),
                coalesce(sum(cost_eur) FILTER (
                    WHERE called_at >= date_trunc('month', moment, 'Europe/Berlin')
                ), 0)
            FROM agent_usage, (SELECT coalesce(%s::timestamptz, now()) AS moment) AS reference
            """,
            (at,),
        ).fetchone()
