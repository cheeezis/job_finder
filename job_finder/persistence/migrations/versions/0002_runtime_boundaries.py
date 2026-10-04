"""Protect worker-owned dataset headers from review writes and cascading deletes."""

from alembic import op

from job_finder.persistence.migrations.runtime_boundaries import dataset_predicate

revision = "0002_runtime_boundaries"
down_revision = "0001_baseline"
branch_labels = None
depends_on = None


def upgrade():
    """Add a role-aware dataset policy without changing rows or legacy credentials."""
    predicate = dataset_predicate()
    op.execute("ALTER TABLE public.datasets ENABLE ROW LEVEL SECURITY")
    op.execute(f"CREATE POLICY runtime_datasets ON public.datasets USING ({predicate}) WITH CHECK ({predicate})")


def downgrade():
    """Keep the access boundary during image rollback; removing it needs a new revision."""
    raise RuntimeError("Die Rechte-Grenze bleibt beim Image-Rollback bestehen; kein automatischer Downgrade.")
