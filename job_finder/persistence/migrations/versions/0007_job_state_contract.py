"""Make the listing tables and job_state columns the store; drop the JSONB fields (F17, stage 4)."""

from alembic import op

from job_finder.persistence.migrations.job_state_contract import REVISION, downgrade as restore, upgrade as contract

revision = REVISION
down_revision = "0006_runs"
branch_labels = None
depends_on = None


def upgrade():
    """Remove the fields from extra; an image from before stage 4 needs the downgrade first."""
    contract(op.get_bind())


def downgrade():
    """Write the fields back from the tables, so the previous image can run again."""
    restore(op.get_bind())
