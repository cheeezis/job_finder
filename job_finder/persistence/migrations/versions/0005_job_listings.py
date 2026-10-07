"""Keep the listings, links, sighting times and places of a remembered job in tables and columns."""

import sqlalchemy as sa
from alembic import op

from job_finder.persistence.migrations.job_listings import (
    DERIVE_COLUMNS,
    DERIVE_LINKS,
    DERIVE_LISTINGS,
    MIRROR_GRANTS,
    REVISION,
    define,
)

revision = REVISION
down_revision = "0004_review_lookup_indexes"
branch_labels = None
depends_on = None


def upgrade():
    """Only add; the JSONB fields stay filled, so the previous image keeps working unchanged."""
    connection = op.get_bind()
    tables = sa.MetaData()
    # The foreign keys need job_state in the same metadata; it exists already and is not created again.
    sa.Table(
        "job_state",
        tables,
        sa.Column("scope", sa.Text, primary_key=True),
        sa.Column("job_id", sa.Text, primary_key=True),
    )
    for column in define(tables):
        op.add_column("job_state", column)
    for name in ("job_listings", "job_links"):
        tables.tables[name].create(connection)
    every_row = {"where": "TRUE"}
    for statement in (DERIVE_LISTINGS, DERIVE_LINKS, DERIVE_COLUMNS):
        connection.exec_driver_sql(statement.format(**every_row))
    connection.exec_driver_sql(MIRROR_GRANTS)


def downgrade():
    """Refuse: an image rollback needs no downgrade, and the JSONB fields still hold everything."""
    raise RuntimeError("Die Tabellen bleiben beim Image-Rollback bestehen; kein automatischer Downgrade.")
