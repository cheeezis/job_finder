"""Listings and links of remembered jobs, stored in their own tables.

A memory entry keeps its listing URLs, source names and linked job ids as
lists (source_urls, source_names, linked_job_ids). Since F17 stage 4 the
tables job_listings and job_links are their only store: write() turns the
lists into rows, attach() turns the rows back into lists. The two lists of a
listing share one row per position, so lists of different length survive.
"""

LISTING_FIELDS = ("source_urls", "source_names")
LINK_FIELD = "linked_job_ids"
FIELDS = (*LISTING_FIELDS, LINK_FIELD)


def rows(scope, job_id, entry):
    """Return the job_listings and job_links rows of one memory entry."""
    urls, names = (_strings(entry.get(field)) for field in LISTING_FIELDS)
    listings = [
        (scope, job_id, position, url, name)
        for position, (url, name) in enumerate(_zip_longest(urls, names))
        if url is not None or name is not None
    ]
    links = [
        (scope, job_id, position, linked)
        for position, linked in enumerate(_strings(entry.get(LINK_FIELD)))
        if linked is not None
    ]
    return listings, links


def write(connection, scope, entries):
    """Replace the listing and link rows of the given {job_id: entry}."""
    if not entries:
        return
    job_ids = list(entries)
    for table in ("job_listings", "job_links"):
        connection.execute(f"DELETE FROM {table} WHERE scope=%s AND job_id=ANY(%s)", (scope, job_ids))
    listings, links = [], []
    for job_id, entry in entries.items():
        entry_listings, entry_links = rows(scope, job_id, entry)
        listings += entry_listings
        links += entry_links
    with connection.cursor() as cursor:
        if listings:
            cursor.executemany(
                "INSERT INTO job_listings (scope, job_id, position, url, source_name) VALUES (%s,%s,%s,%s,%s)", listings
            )
        if links:
            cursor.executemany(
                "INSERT INTO job_links (scope, job_id, position, linked_job_id) VALUES (%s,%s,%s,%s)", links
            )


def attach(connection, memory, present, where, params):
    """Add the lists back to the entries that had them, from their rows.

    present maps job_id to the fields an entry had when it was written, so a
    list that was empty or absent stays empty or absent.
    """
    lists = {job_id: {field: [] for field in FIELDS} for job_id in memory}
    for job_id, position, url, name in connection.execute(
        f"SELECT job_id, position, url, source_name FROM job_listings WHERE {where} ORDER BY job_id, position", params
    ):
        if job_id in lists:
            _put(lists[job_id]["source_urls"], position, url)
            _put(lists[job_id]["source_names"], position, name)
    for job_id, position, linked in connection.execute(
        f"SELECT job_id, position, linked_job_id FROM job_links WHERE {where} ORDER BY job_id, position", params
    ):
        if job_id in lists:
            _put(lists[job_id][LINK_FIELD], position, linked)
    for job_id, entry in memory.items():
        for field in FIELDS:
            if field in present.get(job_id, ()):
                entry[field] = _trimmed(lists[job_id][field])
    return memory


def _strings(values):
    return list(values) if isinstance(values, list) else []


def _zip_longest(first, second):
    length = max(len(first), len(second))
    return [(_at(first, i), _at(second, i)) for i in range(length)]


def _at(values, index):
    return values[index] if index < len(values) else None


def _put(values, position, value):
    values.extend([None] * (position + 1 - len(values)))
    values[position] = value


def _trimmed(values):
    """Drop the padding a shorter list of a listing pair gets."""
    while values and values[-1] is None:
        values.pop()
    return values
