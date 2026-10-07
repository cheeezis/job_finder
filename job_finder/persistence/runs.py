"""Finder runs: started, finished or failed, with their key figures, for the review and for queries."""

import os

from psycopg import errors

from job_finder.persistence.database import snapshot, transaction

RUNNERS = ("cloud", "hybrid", "local")
# The worker job sets cloud, the local hybrid script hybrid; anything else is a local run.
RUNNER_ENV = "JOBFINDER_RUNNER"
FIGURES = ("jobs_total", "jobs_new", "review_new", "sources_partial", "sources_failed")


def runner(environ=os.environ):
    value = environ.get(RUNNER_ENV, "local")
    return value if value in RUNNERS else "local"


def start_run(run_id, sources, environ=os.environ):
    """Record that a run began, with the sources it searches."""
    with transaction() as connection:
        connection.execute(
            "INSERT INTO runs (run_id, runner, sources) VALUES (%s, %s, %s)", (run_id, runner(environ), sorted(sources))
        )


def finish_run(run_id, outcome, **figures):
    """Record how a run ended; figures are the key numbers of a finished run."""
    values = {name: figures.get(name) for name in FIGURES}
    with transaction() as connection:
        connection.execute(
            "UPDATE runs SET finished_at = now(), outcome = %(outcome)s, jobs_total = %(jobs_total)s,"
            " jobs_new = %(jobs_new)s, review_new = %(review_new)s, sources_partial = %(sources_partial)s,"
            " sources_failed = %(sources_failed)s WHERE run_id = %(run_id)s",
            {"run_id": run_id, "outcome": outcome, **values},
        )


def latest_runs():
    """Return the newest run of each runner, newest first; empty before revision 0006."""
    columns = ("run_id", "runner", "started_at", "finished_at", "outcome", *FIGURES)
    try:
        with snapshot() as connection:
            rows = connection.execute(
                f"SELECT DISTINCT ON (runner) {', '.join(columns)} FROM runs ORDER BY runner, started_at DESC"
            ).fetchall()
    except errors.UndefinedTable:
        return []
    runs = [dict(zip(columns, row)) for row in rows]
    for run in runs:
        for key in ("started_at", "finished_at"):
            run[key] = run[key].isoformat() if run[key] else None
    return sorted(runs, key=lambda run: run["started_at"], reverse=True)
