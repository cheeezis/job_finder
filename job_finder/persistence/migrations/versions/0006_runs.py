"""Record every finder run in a table, the local hybrid runs included."""

import sqlalchemy as sa
from alembic import op

from job_finder.persistence.migrations.runs import MIRROR_GRANTS, REVISION, define

revision = REVISION
down_revision = "0005_job_listings"
branch_labels = None
depends_on = None


def upgrade():
    """Only add a table; the previous image does not know it and keeps working."""
    connection = op.get_bind()
    define(sa.MetaData()).create(connection)
    connection.exec_driver_sql(MIRROR_GRANTS)


def downgrade():
    """Refuse: an image rollback needs no downgrade."""
    raise RuntimeError("Die Tabelle bleibt beim Image-Rollback bestehen; kein automatischer Downgrade.")
