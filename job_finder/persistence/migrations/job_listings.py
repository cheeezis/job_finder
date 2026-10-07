"""Frozen structure of revision 0005: the listings and links of a remembered job as tables.

During the transition the JSONB fields in job_state.extra stay the source;
the tables and columns are derived from them in SQL with the statements
below, by the migration, by every write and by the repair command, so an
earlier image can still read and write as before.
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY

REVISION = "0005_job_listings"
NEW_TABLES = ("job_listings", "job_links")
STATE_COLUMNS = ("first_seen_at", "last_seen_at", "locations")


def define(metadata):
    """Add the two tables to metadata and return the columns job_state gains."""
    sa.Table(
        "job_listings",
        metadata,
        sa.Column("scope", sa.Text, primary_key=True),
        sa.Column("job_id", sa.Text, primary_key=True),
        sa.Column("position", sa.Integer, primary_key=True),
        sa.Column("url", sa.Text),
        sa.Column("source_name", sa.Text),
        sa.ForeignKeyConstraint(["scope", "job_id"], ["job_state.scope", "job_state.job_id"], ondelete="CASCADE"),
        sa.CheckConstraint("url IS NOT NULL OR source_name IS NOT NULL", name="job_listings_url_or_source"),
        sa.Index("job_listings_url", "scope", "url"),
    )
    sa.Table(
        "job_links",
        metadata,
        sa.Column("scope", sa.Text, primary_key=True),
        sa.Column("job_id", sa.Text, primary_key=True),
        sa.Column("position", sa.Integer, primary_key=True),
        sa.Column("linked_job_id", sa.Text, nullable=False),
        sa.ForeignKeyConstraint(["scope", "job_id"], ["job_state.scope", "job_state.job_id"], ondelete="CASCADE"),
        sa.Index("job_links_linked_job_id", "scope", "linked_job_id"),
    )
    return [
        sa.Column("first_seen_at", sa.DateTime(timezone=True)),
        sa.Column("last_seen_at", sa.DateTime(timezone=True)),
        sa.Column("locations", ARRAY(sa.Text)),
    ]


def length(path):
    """Return SQL for the length of a JSONB array field, 0 when it is absent or not an array."""
    return f"CASE WHEN jsonb_typeof(s.extra->'{path}') = 'array' THEN jsonb_array_length(s.extra->'{path}') ELSE 0 END"


# {where} selects job_state rows as s; the parameters are named %(scope)s and, if used, %(job_ids)s.
LISTINGS_FROM_EXTRA = f"""
SELECT s.scope, s.job_id, i - 1, s.extra->'source_urls'->>(i - 1), s.extra->'source_names'->>(i - 1)
FROM job_state s CROSS JOIN LATERAL generate_series(1, greatest({length("source_urls")}, {length("source_names")})) i
WHERE {{where}} AND (s.extra->'source_urls'->>(i - 1) IS NOT NULL OR s.extra->'source_names'->>(i - 1) IS NOT NULL)
"""
LINKS_FROM_EXTRA = f"""
SELECT s.scope, s.job_id, i - 1, s.extra->'linked_job_ids'->>(i - 1)
FROM job_state s CROSS JOIN LATERAL generate_series(1, {length("linked_job_ids")}) i
WHERE {{where}} AND s.extra->'linked_job_ids'->>(i - 1) IS NOT NULL
"""
DERIVE_LISTINGS = "INSERT INTO job_listings (scope, job_id, position, url, source_name)" + LISTINGS_FROM_EXTRA
DERIVE_LINKS = "INSERT INTO job_links (scope, job_id, position, linked_job_id)" + LINKS_FROM_EXTRA
DERIVE_COLUMNS = """
UPDATE job_state s SET
    first_seen_at = (s.extra->>'first_seen_at')::timestamptz,
    last_seen_at = (s.extra->>'last_seen_at')::timestamptz,
    locations = CASE WHEN jsonb_typeof(s.extra->'locations') = 'array'
        THEN ARRAY(SELECT jsonb_array_elements_text(s.extra->'locations')) END
WHERE {where}
"""
# Give the new tables exactly the privileges each role holds on job_state, whatever roles an environment has.
# The driver reads % as a placeholder, hence %% for format().
MIRROR_GRANTS = """
DO $$
DECLARE grant_row record;
BEGIN
    FOR grant_row IN
        SELECT grantee, string_agg(privilege_type, ', ') AS privileges
        FROM information_schema.role_table_grants
        WHERE table_schema = 'public' AND table_name = 'job_state'
            AND privilege_type IN ('SELECT', 'INSERT', 'UPDATE', 'DELETE')
            AND grantee NOT IN (current_user, 'PUBLIC')
        GROUP BY grantee
    LOOP
        EXECUTE format('GRANT %%s ON public.job_listings, public.job_links TO %%I', grant_row.privileges, grant_row.grantee);
    END LOOP;
END $$
"""
