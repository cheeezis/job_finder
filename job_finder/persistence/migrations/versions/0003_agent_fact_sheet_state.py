"""Remember retries, the basis of each fact sheet and what has changed since."""

from alembic import op

from job_finder.persistence.migrations.fact_sheet_state import REVISION, TABLE, added_columns

revision = REVISION
down_revision = "0002_runtime_boundaries"
branch_labels = None
depends_on = None


def upgrade():
    """Only add columns; through their defaults the previous image keeps writing as before."""
    for column in added_columns():
        op.add_column(TABLE, column)


def downgrade():
    """Refuse: an image rollback needs no downgrade, and dropping the columns would lose the stamps."""
    raise RuntimeError("Die Spalten bleiben beim Image-Rollback bestehen; kein automatischer Downgrade.")
