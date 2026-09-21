"""Relational core records with JSONB only for additional source attributes."""

from copy import deepcopy
from datetime import date, datetime

from psycopg import sql
from psycopg.types.json import Jsonb

from job_finder.database import lock, snapshot, transaction

STATE_FIELDS = (
    "title",
    "company",
    "workflow_status",
    "active",
    "missed_runs",
    "salary_expectation_eur",
    "personal_rating",
    "review_note",
)
HISTORY_FIELDS = ("status", "occurred_on", "scheduled_for")
DOCUMENT_FIELDS = ("id", "name", "kind", "stored_name", "folder_name")


def prune_cache(days=30):
    """Remove old automatic cache entries, never manual input or job history."""
    if days < 14:
        raise ValueError("Cache-Aufbewahrung muss mindestens 14 Tage betragen.")
    with transaction() as connection:
        rows = connection.execute(
            "DELETE FROM source_cache WHERE stored_at < now() - (%s * interval '1 day')",
            (days,),
        ).rowcount
        return rows


def parts(record, fields):
    """Retain missing-vs-null distinctions and unknown fields during migration."""
    return (
        *[record.get(field) for field in fields],
        list(record),
        Jsonb({key: value for key, value in record.items() if key not in fields}),
    )


def unpack(row, fields):
    """Reconstruct a record without losing absent fields or date precision."""
    present, extra = row[-2:]
    result = dict(extra)
    for field, value in zip(fields, row):
        if field in present:
            if isinstance(value, datetime) and field == "scheduled_for":
                value = value.isoformat(timespec="minutes")
            elif isinstance(value, date):
                value = value.isoformat()
            result[field] = value
    return result


def upsert_records(connection, table, keys, fields, records):
    """All identifiers are internal constants; values always use parameters."""
    if not records:
        return
    columns = (*keys, *fields, "present", "extra")
    statement = sql.SQL(
        "INSERT INTO {} ({}) VALUES ({}) ON CONFLICT ({}) DO UPDATE SET {}"
    ).format(
        sql.Identifier(table),
        sql.SQL(",").join(map(sql.Identifier, columns)),
        sql.SQL(",").join(sql.Placeholder() for _ in columns),
        sql.SQL(",").join(map(sql.Identifier, keys)),
        sql.SQL(",").join(
            sql.SQL("{}=EXCLUDED.{}").format(sql.Identifier(c), sql.Identifier(c))
            for c in columns[len(keys) :]
        ),
    )
    with connection.cursor() as cursor:
        cursor.executemany(statement, records)


def read_memory(connection, scope, job_id=None, *, for_update=False):
    """Load core records and their ordered history and document metadata."""
    where = "scope=%s" + (" AND job_id=%s" if job_id is not None else "")
    params = (scope,) if job_id is None else (scope, job_id)
    rows = connection.execute(
        f"SELECT job_id,{','.join(STATE_FIELDS)},present,extra FROM job_state "
        f"WHERE {where}" + (" FOR UPDATE" if for_update else ""),
        params,
    )
    memory = {row[0]: unpack(row[1:], STATE_FIELDS) for row in rows}
    for table, field, fields in (
        ("workflow_history", "workflow_history", HISTORY_FIELDS),
        ("application_documents", "application_documents", DOCUMENT_FIELDS),
    ):
        rows = connection.execute(
            f"SELECT job_id,{','.join(fields)},present,extra FROM {table} "
            f"WHERE {where} ORDER BY job_id,position",
            params,
        )
        for row in rows:
            memory[row[0]].setdefault(field, []).append(unpack(row[1:], fields))
    return memory


def write_memory(connection, scope, before, after):
    """Persist only added, removed and changed records inside a transaction."""
    removed = list(before.keys() - after.keys())
    if removed:
        connection.execute(
            "DELETE FROM job_state WHERE scope=%s AND job_id=ANY(%s)", (scope, removed)
        )
    changed = {key: value for key, value in after.items() if before.get(key) != value}
    records = []
    children = {"workflow_history": [], "application_documents": []}
    for job_id, entry in changed.items():
        core = deepcopy(entry)
        for field, fields in (
            ("workflow_history", HISTORY_FIELDS),
            ("application_documents", DOCUMENT_FIELDS),
        ):
            values = core.get(field)
            if isinstance(values, list):
                # An empty marker preserves the presence of an empty list.
                core[field] = []
                for position, value in enumerate(values):
                    if not isinstance(value, dict):
                        raise ValueError(f"Ungültiger Eintrag in {field}")
                    children[field].append(
                        (scope, job_id, position, *parts(value, fields))
                    )
        records.append((scope, job_id, *parts(core, STATE_FIELDS)))
    upsert_records(connection, "job_state", ("scope", "job_id"), STATE_FIELDS, records)
    for table, fields in (
        ("workflow_history", HISTORY_FIELDS),
        ("application_documents", DOCUMENT_FIELDS),
    ):
        if changed:
            connection.execute(
                f"DELETE FROM {table} WHERE scope=%s AND job_id=ANY(%s)",
                (scope, list(changed)),
            )
        upsert_records(
            connection, table, ("scope", "job_id", "position"), fields, children[table]
        )


def read_dataset(name, default=None):
    """Read metadata and rows from the same nonblocking database snapshot."""
    with snapshot() as connection:
        row = connection.execute(
            "SELECT metadata FROM datasets WHERE name=%s", (name,)
        ).fetchone()
        if row is None:
            return deepcopy(default)
        info = row[0]
        kind = info["kind"]
        result = deepcopy(info["header"])
        if kind in {"jobs", "recommendations"}:
            fields = ("title", "company") + (
                ("match_percent",) if kind == "recommendations" else ()
            )
            values = [
                {"id": row[0], **unpack(row[1:], fields)}
                for row in connection.execute(
                    f"SELECT job_id,{','.join(fields)},present,extra FROM {kind} "
                    "WHERE dataset=%s ORDER BY position",
                    (name,),
                )
            ]
            if kind == "jobs":
                return values
            result["recommendations"] = values
        elif kind == "notifications":
            fields = ("job_id", "sent_at", "attempts")
            for row in connection.execute(
                "SELECT notification_key,delivery_state,job_id,sent_at,attempts,present,extra "
                "FROM notifications WHERE dataset=%s",
                (name,),
            ):
                result.setdefault(row[1], {})[row[0]] = unpack(row[2:], fields)
        elif kind in {"cache", "manual"}:
            table, key = (
                ("manual_sources", "url")
                if kind == "manual"
                else ("source_cache", "cache_key")
            )
            values = connection.execute(
                f"SELECT {key},payload FROM {table} WHERE dataset=%s ORDER BY position",
                (name,),
            ).fetchall()
            result[info["field"]] = (
                [row[1] for row in values] if info["list"] else dict(values)
            )
        return result


def write_dataset(name, value):
    """Store one dataset atomically; nested callers can group related datasets."""
    with transaction() as connection:
        lock(connection, "dataset:" + name)
        if name.endswith("/jobs.json"):
            kind, header = "jobs", {}
        elif name.endswith("/recommendations.json"):
            kind, header = "recommendations", {**value, "recommendations": []}
        elif name.endswith("/notifications.json"):
            kind, header = "notifications", {**value, "sent": {}, "pending": {}}
        elif isinstance(value, dict) and any(
            key in value for key in ("jobs", "checks")
        ):
            kind = "manual" if name.endswith("/manual_jobs_cache.json") else "cache"
            field = "jobs" if "jobs" in value else "checks"
            header = {k: v for k, v in value.items() if k != field}
        else:
            kind, header = "opaque", value
        info = {"kind": kind, "header": header}
        if kind in {"cache", "manual"}:
            info.update(field=field, list=isinstance(value[field], list))
        connection.execute(
            "INSERT INTO datasets(name,metadata) VALUES (%s,%s) "
            "ON CONFLICT(name) DO UPDATE SET metadata=EXCLUDED.metadata,updated_at=now()",
            (name, Jsonb(info)),
        )
        if kind in {"jobs", "recommendations"}:
            values = value if kind == "jobs" else value["recommendations"]
            fields = ("title", "company") + (
                ("match_percent",) if kind == "recommendations" else ()
            )
            connection.execute(f"DELETE FROM {kind} WHERE dataset=%s", (name,))
            rows = []
            for position, item in enumerate(values):
                data = {k: v for k, v in item.items() if k != "id"}
                rows.append((name, position, item["id"], *parts(data, fields)))
            # Position is a regular column, not part of record attributes.
            upsert_records(
                connection, kind, ("dataset", "position"), ("job_id", *fields), rows
            )
        elif kind == "notifications":
            connection.execute("DELETE FROM notifications WHERE dataset=%s", (name,))
            fields = ("job_id", "sent_at", "attempts")
            rows = [
                (name, key, state, *parts(item, fields))
                for state in ("sent", "pending")
                for key, item in value.get(state, {}).items()
            ]
            upsert_records(
                connection,
                "notifications",
                ("dataset", "notification_key", "delivery_state"),
                fields,
                rows,
            )
        elif kind in {"cache", "manual"}:
            table, key_column = (
                ("manual_sources", "url")
                if kind == "manual"
                else ("source_cache", "cache_key")
            )
            entries = enumerate(value[field]) if info["list"] else value[field].items()
            rows = [
                (name, str(key), position, Jsonb(item))
                for position, (key, item) in enumerate(entries)
            ]
            if kind != "manual":
                connection.execute(
                    f"DELETE FROM {table} WHERE dataset=%s AND NOT ({key_column}=ANY(%s))",
                    (name, [row[1] for row in rows]),
                )
            with connection.cursor() as cursor:
                cursor.executemany(
                    f"INSERT INTO {table}(dataset,{key_column},position,payload) VALUES (%s,%s,%s,%s) "
                    f"ON CONFLICT(dataset,{key_column}) DO UPDATE SET payload=EXCLUDED.payload,position=EXCLUDED.position"
                    + (",stored_at=now()" if kind == "cache" else "")
                    + f" WHERE {table}.payload IS DISTINCT FROM EXCLUDED.payload OR {table}.position != EXCLUDED.position",
                    rows,
                )
