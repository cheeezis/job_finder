"""Index the listing URLs and linked ids the review looks up in job_state.extra."""

from alembic import op

from job_finder.persistence.migrations.review_indexes import INDEXES, REVISION, create_statement

revision = REVISION
down_revision = "0003_agent_fact_sheet_state"
branch_labels = None
depends_on = None


def upgrade():
    """Only add indexes; the previous image reads and writes as before."""
    for name, key in INDEXES.items():
        op.execute(create_statement(name, key))


def downgrade():
    """Refuse: an image rollback needs no downgrade, and the indexes do no harm."""
    raise RuntimeError("Die Indizes bleiben beim Image-Rollback bestehen; kein automatischer Downgrade.")
