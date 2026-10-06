"""Frozen columns of revision 0003; the migration adds them and the validation expects them."""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY, JSONB

REVISION = "0003_agent_fact_sheet_state"
TABLE = "agent_fact_sheets"


def added_columns():
    """Return new column objects: whether one more attempt is due, how many there were, the basis and its changes."""
    return [
        sa.Column("retryable", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("attempts", sa.Integer, nullable=False, server_default=sa.text("1")),
        sa.Column("versions", JSONB, nullable=True),
        sa.Column("outdated", ARRAY(sa.Text), nullable=True),
    ]
