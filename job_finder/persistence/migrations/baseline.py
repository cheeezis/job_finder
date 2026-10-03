"""Immutable snapshot of the pre-Alembic v2 schema; add future changes as new revisions."""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID

metadata = sa.MetaData()
legacy_metadata = sa.MetaData()
# Kept on existing databases since 2e62ac9; never recreated or removed by the baseline.
sa.Table(
    "migration_runs",
    legacy_metadata,
    sa.Column("source_fingerprint", sa.Text, primary_key=True),
    sa.Column("completed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    sa.Column("summary", JSONB, nullable=False),
)

sa.Table("schema_version", metadata, sa.Column("version", sa.Integer, primary_key=True, autoincrement=False))
sa.Table(
    "job_state",
    metadata,
    sa.Column("scope", sa.Text, primary_key=True),
    sa.Column("job_id", sa.Text, primary_key=True),
    sa.Column("title", sa.Text),
    sa.Column("company", sa.Text),
    sa.Column("workflow_status", sa.Text),
    sa.Column("active", sa.Boolean),
    sa.Column("missed_runs", sa.Integer),
    sa.Column("salary_expectation_eur", sa.BigInteger),
    sa.Column("personal_rating", sa.Text),
    sa.Column("review_note", sa.Text),
    sa.Column("present", ARRAY(sa.Text), nullable=False),
    sa.Column("extra", JSONB, nullable=False),
    sa.Index("job_state_status", "scope", "workflow_status"),
)
sa.Table(
    "workflow_history",
    metadata,
    sa.Column("scope", sa.Text, primary_key=True),
    sa.Column("job_id", sa.Text, primary_key=True),
    sa.Column("position", sa.Integer, primary_key=True),
    sa.Column("status", sa.Text),
    sa.Column("occurred_on", sa.Date),
    sa.Column("scheduled_for", sa.DateTime),
    sa.Column("present", ARRAY(sa.Text), nullable=False),
    sa.Column("extra", JSONB, nullable=False),
    sa.ForeignKeyConstraint(["scope", "job_id"], ["job_state.scope", "job_state.job_id"], ondelete="CASCADE"),
)
sa.Table(
    "application_documents",
    metadata,
    sa.Column("scope", sa.Text, primary_key=True),
    sa.Column("job_id", sa.Text, primary_key=True),
    sa.Column("position", sa.Integer, primary_key=True),
    sa.Column("id", sa.Text),
    sa.Column("name", sa.Text),
    sa.Column("kind", sa.Text),
    sa.Column("stored_name", sa.Text),
    sa.Column("folder_name", sa.Text),
    sa.Column("present", ARRAY(sa.Text), nullable=False),
    sa.Column("extra", JSONB, nullable=False),
    sa.ForeignKeyConstraint(["scope", "job_id"], ["job_state.scope", "job_state.job_id"], ondelete="CASCADE"),
)
sa.Table(
    "datasets",
    metadata,
    sa.Column("name", sa.Text, primary_key=True),
    sa.Column("metadata", JSONB, nullable=False),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
)
sa.Table(
    "jobs",
    metadata,
    sa.Column("dataset", sa.Text, sa.ForeignKey("datasets.name", ondelete="CASCADE"), primary_key=True),
    sa.Column("job_id", sa.Text, nullable=False),
    sa.Column("position", sa.Integer, primary_key=True),
    sa.Column("title", sa.Text),
    sa.Column("company", sa.Text),
    sa.Column("present", ARRAY(sa.Text), nullable=False),
    sa.Column("extra", JSONB, nullable=False),
)
sa.Table(
    "recommendations",
    metadata,
    sa.Column("dataset", sa.Text, sa.ForeignKey("datasets.name", ondelete="CASCADE"), primary_key=True),
    sa.Column("job_id", sa.Text, nullable=False),
    sa.Column("position", sa.Integer, primary_key=True),
    sa.Column("title", sa.Text),
    sa.Column("company", sa.Text),
    sa.Column("match_percent", sa.Float),
    sa.Column("present", ARRAY(sa.Text), nullable=False),
    sa.Column("extra", JSONB, nullable=False),
)
sa.Table(
    "notifications",
    metadata,
    sa.Column("dataset", sa.Text, sa.ForeignKey("datasets.name", ondelete="CASCADE"), primary_key=True),
    sa.Column("notification_key", sa.Text, primary_key=True),
    sa.Column("delivery_state", sa.Text, primary_key=True),
    sa.Column("job_id", sa.Text),
    sa.Column("sent_at", sa.Text),
    sa.Column("attempts", sa.Integer),
    sa.Column("present", ARRAY(sa.Text), nullable=False),
    sa.Column("extra", JSONB, nullable=False),
    sa.CheckConstraint(
        "delivery_state = ANY (ARRAY['sent'::text, 'pending'::text])", name="notifications_delivery_state_check"
    ),
)
sa.Table(
    "source_cache",
    metadata,
    sa.Column("dataset", sa.Text, sa.ForeignKey("datasets.name", ondelete="CASCADE"), primary_key=True),
    sa.Column("cache_key", sa.Text, primary_key=True),
    sa.Column("position", sa.Integer, nullable=False),
    sa.Column("payload", JSONB, nullable=False),
    sa.Column("stored_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
)
sa.Table(
    "manual_sources",
    metadata,
    sa.Column("dataset", sa.Text, sa.ForeignKey("datasets.name", ondelete="CASCADE"), primary_key=True),
    sa.Column("url", sa.Text, primary_key=True),
    sa.Column("position", sa.Integer, nullable=False),
    sa.Column("payload", JSONB, nullable=False),
)
sa.Table(
    "agent_usage",
    metadata,
    sa.Column("id", UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
    sa.Column("called_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    sa.Column("job_id", sa.Text, nullable=False),
    sa.Column("model", sa.Text, nullable=False),
    sa.Column("input_tokens", sa.Integer, nullable=False),
    sa.Column("cached_input_tokens", sa.Integer, nullable=False),
    sa.Column("output_tokens", sa.Integer, nullable=False),
    sa.Column("reasoning_tokens", sa.Integer, nullable=False),
    sa.Column("cost_eur", sa.Numeric, nullable=False),
    sa.Column("web_searches", sa.Integer, nullable=False, server_default=sa.text("0")),
)
sa.Table(
    "agent_fact_sheets",
    metadata,
    sa.Column("scope", sa.Text, primary_key=True),
    sa.Column("job_id", sa.Text, primary_key=True),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    sa.Column("model", sa.Text, nullable=False),
    sa.Column("complete", sa.Boolean, nullable=False),
    sa.Column("note", sa.Text),
    sa.Column("fact_sheet", JSONB),
    sa.Column("cost_eur", sa.Numeric, nullable=False),
)
