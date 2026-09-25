"""The user's own review decisions, read for the agent's past-decisions tool."""

from job_finder.persistence.database import snapshot


def decided_jobs(scope="default"):
    """Return (job_id, title, company, status, rating, note, last status date) per decision.

    Undecided jobs are left out, and so are jobs the availability check set to
    ignored on its own: nobody decided on those. The snapshot is read-only,
    so this lookup cannot change anything.
    """
    with snapshot() as connection:
        return connection.execute(
            """
            SELECT s.job_id, s.title, s.company, s.workflow_status, s.personal_rating,
                   s.review_note, max(h.occurred_on)
            FROM job_state s
            LEFT JOIN workflow_history h ON h.scope = s.scope AND h.job_id = s.job_id
            WHERE s.scope = %s
              AND s.workflow_status NOT IN ('new', 'review')
              AND NOT (s.workflow_status = 'ignored' AND s.extra ? 'availability_checked_at')
            GROUP BY s.job_id, s.title, s.company, s.workflow_status, s.personal_rating,
                     s.review_note
            """,
            (scope,),
        ).fetchall()
