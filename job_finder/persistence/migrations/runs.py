"""Frozen structure of revision 0006: one row per finder run, wherever it ran."""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY

REVISION = "0006_runs"


def define(metadata):
    """Add the runs table to metadata and return it."""
    return sa.Table(
        "runs",
        metadata,
        sa.Column("run_id", sa.Text, primary_key=True),
        sa.Column("runner", sa.Text, nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("outcome", sa.Text, nullable=False, server_default=sa.text("'running'::text")),
        sa.Column("sources", ARRAY(sa.Text), nullable=False),
        sa.Column("jobs_total", sa.Integer),
        sa.Column("jobs_new", sa.Integer),
        sa.Column("review_new", sa.Integer),
        sa.Column("sources_partial", sa.Integer),
        sa.Column("sources_failed", sa.Integer),
        sa.CheckConstraint(
            "outcome = ANY (ARRAY['running'::text, 'finished'::text, 'failed'::text])", name="runs_outcome"
        ),
        sa.CheckConstraint("runner = ANY (ARRAY['cloud'::text, 'hybrid'::text, 'local'::text])", name="runs_runner"),
        sa.Index("runs_started_at", "runner", "started_at"),
    )


# The finder writes runs, the review only reads them: the same privileges each role has on the
# agent's cost ledger. The driver reads % as a placeholder, hence %% for format().
MIRROR_GRANTS = """
DO $$
DECLARE grant_row record;
BEGIN
    FOR grant_row IN
        SELECT grantee, string_agg(privilege_type, ', ') AS privileges
        FROM information_schema.role_table_grants
        WHERE table_schema = 'public' AND table_name = 'agent_usage'
            AND privilege_type IN ('SELECT', 'INSERT', 'UPDATE', 'DELETE')
            AND grantee NOT IN (current_user, 'PUBLIC')
        GROUP BY grantee
    LOOP
        EXECUTE format('GRANT %%s ON public.runs TO %%I', grant_row.privileges, grant_row.grantee);
    END LOOP;
END $$
"""
