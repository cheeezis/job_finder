"""Baseline: preserve the existing PostgreSQL v2 application schema."""

from alembic import op

from job_finder.persistence.migrations.baseline import metadata

revision = "0001_baseline"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    """Create an empty database through the same revision adopted by existing databases."""
    connection = op.get_bind()
    metadata.create_all(connection, checkfirst=False)
    # Retain this marker so the previous app image remains compatible with the baseline.
    connection.execute(metadata.tables["schema_version"].insert().values(version=2))


def downgrade():
    """Refuse to erase application data; an app rollback needs no baseline downgrade."""
    raise RuntimeError("Die Baseline wird nicht zurückgebaut. Restore nur in ein getrenntes, leeres Ziel.")
