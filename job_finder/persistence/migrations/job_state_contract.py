"""Frozen steps of revision 0007: the listing tables and job_state columns become the store.

Until revision 0006 the JSONB fields below were the source and the tables
were derived from them. The upgrade derives everything one last time, then
removes the fields from job_state.extra and drops the two GIN indexes that
only served them. The downgrade writes the fields back from the tables, so an
image from before stage 4 can run again after it.
"""

from datetime import UTC

from job_finder.persistence.migrations.job_listings import DERIVE_COLUMNS, DERIVE_LINKS, DERIVE_LISTINGS
from job_finder.persistence.migrations.review_indexes import INDEXES, create_statement

REVISION = "0007_job_state_contract"
LIST_FIELDS = ("source_urls", "source_names", "linked_job_ids")
TIME_FIELDS = ("first_seen_at", "last_seen_at")
FIELDS = (*LIST_FIELDS, *TIME_FIELDS, "locations")

DERIVE_ALL = (
    "DELETE FROM job_listings",
    "DELETE FROM job_links",
    DERIVE_LISTINGS.format(where="TRUE"),
    DERIVE_LINKS.format(where="TRUE"),
    DERIVE_COLUMNS.format(where="TRUE"),
)
STRIP_FIELDS = (
    "UPDATE job_state SET extra = extra"
    + "".join(f" - '{field}'" for field in FIELDS)
    + (" WHERE extra ?| array[" + ", ".join(f"'{field}'" for field in FIELDS) + "]")
)
DROP_INDEXES = [f"DROP INDEX public.{name}" for name in INDEXES]


def upgrade(connection):
    for statement in (*DERIVE_ALL, STRIP_FIELDS, *DROP_INDEXES):
        connection.exec_driver_sql(statement)


def downgrade(connection):
    """Write the fields back into extra for every entry that had them, then restore the indexes."""
    from psycopg.types.json import Jsonb

    rows = connection.exec_driver_sql(
        "SELECT scope, job_id, present, first_seen_at, last_seen_at, locations FROM job_state"
    ).fetchall()
    lists = {(scope, job_id): {field: [] for field in LIST_FIELDS} for scope, job_id, *_ in rows}
    for scope, job_id, position, url, name in connection.exec_driver_sql(
        "SELECT scope, job_id, position, url, source_name FROM job_listings"
    ):
        _put(lists[(scope, job_id)]["source_urls"], position, url)
        _put(lists[(scope, job_id)]["source_names"], position, name)
    for scope, job_id, position, linked in connection.exec_driver_sql(
        "SELECT scope, job_id, position, linked_job_id FROM job_links"
    ):
        _put(lists[(scope, job_id)]["linked_job_ids"], position, linked)
    updates = []
    for scope, job_id, present, first_seen, last_seen, locations in rows:
        values: dict[str, object] = {field: _trimmed(lists[(scope, job_id)][field]) for field in LIST_FIELDS}
        values.update(
            first_seen_at=first_seen.astimezone(UTC).isoformat() if first_seen else None,
            last_seen_at=last_seen.astimezone(UTC).isoformat() if last_seen else None,
            locations=locations,
        )
        restored = {field: values[field] for field in FIELDS if field in (present or ())}
        if restored:
            updates.append((Jsonb(restored), scope, job_id))
    if updates:
        with connection.connection.cursor() as cursor:
            cursor.executemany("UPDATE job_state SET extra = extra || %s WHERE scope = %s AND job_id = %s", updates)
    for name, key in INDEXES.items():
        connection.exec_driver_sql(create_statement(name, key))


def _put(values, position, value):
    values.extend([None] * (position + 1 - len(values)))
    values[position] = value


def _trimmed(values):
    while values and values[-1] is None:
        values.pop()
    return values
