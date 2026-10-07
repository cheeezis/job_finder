"""Listings, links and sighting columns of remembered jobs, derived from job_state.extra.

While the JSONB fields are still the source (F17, stage 1), these tables are
rebuilt from them in SQL whenever a job is written; drift() shows whether
anything got out of step, e.g. through an earlier image, and resync() repairs it.
"""

from job_finder.persistence.migrations.job_listings import (
    DERIVE_COLUMNS,
    DERIVE_LINKS,
    DERIVE_LISTINGS,
    LINKS_FROM_EXTRA,
    LISTINGS_FROM_EXTRA,
)

SELECTED = "s.scope = %(scope)s AND s.job_id = ANY(%(job_ids)s)"
EVERY = "s.scope = %(scope)s"


def derive(connection, scope, job_ids=None):
    """Rebuild the tables and columns of the given jobs (all of the scope without ids) from their JSONB fields."""
    params = {"scope": scope, "job_ids": list(job_ids or [])}
    where = EVERY if job_ids is None else SELECTED
    for table in ("job_listings", "job_links"):
        condition = "scope = %(scope)s" + ("" if job_ids is None else " AND job_id = ANY(%(job_ids)s)")
        connection.execute(f"DELETE FROM {table} WHERE {condition}", params)
    for statement in (DERIVE_LISTINGS, DERIVE_LINKS, DERIVE_COLUMNS):
        connection.execute(statement.format(where=where), params)


def drift(connection, scope="default"):
    """Count rows of the tables and jobs whose columns differ from what the JSONB fields say; zeros mean in step."""
    params = {"scope": scope}
    listings = LISTINGS_FROM_EXTRA.format(where=EVERY)
    links = LINKS_FROM_EXTRA.format(where=EVERY)
    counts = {}
    for name, derived, stored in (
        ("listings", listings, "scope, job_id, position, url, source_name FROM job_listings WHERE scope = %(scope)s"),
        ("links", links, "scope, job_id, position, linked_job_id FROM job_links WHERE scope = %(scope)s"),
    ):
        counts[name] = connection.execute(
            f"SELECT count(*) FROM (({derived} EXCEPT SELECT {stored}) UNION ALL (SELECT {stored} EXCEPT {derived})) d",
            params,
        ).fetchone()[0]
    counts["columns"] = connection.execute(
        """
        SELECT count(*) FROM job_state s WHERE s.scope = %(scope)s AND (
            s.first_seen_at IS DISTINCT FROM (s.extra->>'first_seen_at')::timestamptz
            OR s.last_seen_at IS DISTINCT FROM (s.extra->>'last_seen_at')::timestamptz
            OR s.locations IS DISTINCT FROM CASE WHEN jsonb_typeof(s.extra->'locations') = 'array'
                THEN ARRAY(SELECT jsonb_array_elements_text(s.extra->'locations')) END)
        """,
        params,
    ).fetchone()[0]
    return counts


def resync(connection, scope="default"):
    """Rebuild everything from the JSONB fields; return the drift before."""
    before = drift(connection, scope)
    derive(connection, scope)
    return before
