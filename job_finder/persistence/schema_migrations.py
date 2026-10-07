"""Validate legacy PostgreSQL before adoption and run Alembic in one admin transaction."""

import hashlib
import re
from contextlib import contextmanager
from pathlib import Path

import psycopg
import sqlalchemy as sa
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from alembic.script.revision import ResolutionError
from alembic.util import CommandError
from sqlalchemy.pool import NullPool

from job_finder.persistence.database import admin_database_url
from job_finder.persistence.migrations.baseline import legacy_metadata, metadata
from job_finder.persistence.migrations.fact_sheet_state import REVISION as FACT_SHEET_REVISION, added_columns
from job_finder.persistence.migrations.job_listings import REVISION as LISTINGS_REVISION, define as define_listings
from job_finder.persistence.migrations.review_indexes import (
    INDEXES as REVIEW_INDEXES,
    REVISION as INDEX_REVISION,
    expected_definition,
)
from job_finder.persistence.migrations.runtime_boundaries import REVISION as BOUNDARIES_REVISION, rendered_predicate

BASELINE_REVISION = "0001_baseline"
# Revisions whose complete structure the check knows, oldest first; each one adds to the previous.
KNOWN_REVISIONS = (BASELINE_REVISION, BOUNDARIES_REVISION, FACT_SHEET_REVISION, INDEX_REVISION, LISTINGS_REVISION)


def migration_config(connection=None):
    """Keep credentials out of ConfigParser, logs and exception URLs."""
    config = Config()
    config.set_main_option("script_location", str(Path(__file__).with_name("migrations")).replace("%", "%%"))
    if connection is not None:
        config.attributes["connection"] = connection
    return config


@contextmanager
def admin_connection(*, readonly=False):
    """Preserve libpq options through the creator, including Azure TLS and encoded passwords."""
    url = admin_database_url()
    engine = sa.create_engine(
        "postgresql+psycopg://",
        creator=lambda: psycopg.connect(url, connect_timeout=10),
        poolclass=NullPool,
        hide_parameters=True,
    )
    try:
        with engine.begin() as connection:
            if readonly:
                connection.exec_driver_sql("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            connection.exec_driver_sql("SET LOCAL lock_timeout = '30s'")
            connection.exec_driver_sql("SET LOCAL search_path = public")
            yield connection
    finally:
        engine.dispose()


def _sql_shape(value):
    """Normalize harmless rendering differences without evaluating a column default."""
    if value is None:
        return None
    parts = re.split(r"('(?:''|[^'])*')", str(value))
    return "".join(part if index % 2 else re.sub(r"\s+", "", part) for index, part in enumerate(parts)).removesuffix(
        "::integer"
    )


def _different_default(context, inspected, expected, inspected_default, expected_default, rendered):
    return _sql_shape(inspected_default) != _sql_shape(rendered)


def _application_object(obj, name, kind, reflected, compared):
    # Alembic cannot compare expression indexes; validate_baseline checks these two itself.
    return (kind != "table" or name != "alembic_version") and (kind != "index" or name not in REVIEW_INDEXES)


def validate_baseline(
    connection, *, runtime_boundaries=False, fact_sheet_state=False, review_indexes=False, job_listings=False
):
    """Check types, nullability, defaults, keys, indexes and checks without altering rows.

    fact_sheet_state expects the columns revision 0003 added as well,
    review_indexes the two GIN indexes of revision 0004 with their exact definition,
    job_listings the tables and job_state columns of revision 0005.
    """
    inspector = sa.inspect(connection)
    expected = sa.MetaData()
    for table in metadata.sorted_tables:
        table.to_metadata(expected)
    if fact_sheet_state:
        for column in added_columns():
            expected.tables["agent_fact_sheets"].append_column(column)
    if job_listings:
        for column in define_listings(expected):
            expected.tables["job_state"].append_column(column)
    for table in legacy_metadata.sorted_tables:
        if inspector.has_table(table.name, schema="public"):
            table.to_metadata(expected)
    context = MigrationContext.configure(
        connection,
        opts={
            "compare_type": True,
            "compare_server_default": _different_default,
            "version_table_schema": "public",
            "include_object": _application_object,
        },
    )
    differences = compare_metadata(context, expected)
    # Alembic autogeneration does not compare primary keys or CHECK constraints.
    for table in expected.sorted_tables:
        if not inspector.has_table(table.name, schema="public"):
            continue
        primary_key = inspector.get_pk_constraint(table.name, schema="public")["constrained_columns"]
        if primary_key != [column.name for column in table.primary_key.columns]:
            differences.append(("primary_key", table.name))
        expected_checks = sorted(
            _sql_shape(check.sqltext) or "" for check in table.constraints if isinstance(check, sa.CheckConstraint)
        )
        actual_checks = sorted(
            _sql_shape(check["sqltext"]) or "" for check in inspector.get_check_constraints(table.name, schema="public")
        )
        if expected_checks != actual_checks:
            differences.append(("check_constraint", table.name))
        # Alembic's PostgreSQL comparator omits predicates and several index options.
        for index in inspector.get_indexes(table.name, schema="public"):
            if review_indexes and index["name"] in REVIEW_INDEXES:
                continue
            options = index.get("dialect_options", {})
            if (
                options.get("postgresql_where") is not None
                or options.get("postgresql_include")
                or options.get("postgresql_ops")
                or options.get("postgresql_using", "btree") != "btree"
                or index.get("column_sorting")
            ):
                differences.append(("index_options", table.name))
    if review_indexes:
        definitions = dict(
            connection.exec_driver_sql(
                "SELECT indexname, indexdef FROM pg_indexes WHERE schemaname = 'public' AND indexname = ANY(%s)",
                (list(REVIEW_INDEXES),),
            ).fetchall()
        )
        for name, key in REVIEW_INDEXES.items():
            if _sql_shape(definitions.get(name)) != _sql_shape(expected_definition(name, key)):
                differences.append(("review_index", name))
    if inspector.get_view_names(schema="public") or inspector.get_materialized_view_names(schema="public"):
        differences.append(("unexpected_view",))
    custom_behavior = connection.exec_driver_sql(
        "SELECT EXISTS (SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
        "WHERE n.nspname='public' AND ((c.relrowsecurity AND c.relname <> %s) OR c.relforcerowsecurity OR c.relkind='p' "
        "OR EXISTS (SELECT 1 FROM pg_trigger t WHERE t.tgrelid=c.oid AND NOT t.tgisinternal) "
        "OR EXISTS (SELECT 1 FROM pg_constraint con WHERE con.conrelid=c.oid AND NOT con.convalidated) "
        "OR EXISTS (SELECT 1 FROM pg_index i WHERE i.indrelid=c.oid AND (NOT i.indisvalid OR NOT i.indisready))))",
        ("datasets" if runtime_boundaries else "",),
    ).scalar()
    if custom_behavior:
        differences.append(("unexpected_table_behavior",))
    if differences:
        # Never stringify reflected objects or defaults: they may contain private data.
        raise RuntimeError("PostgreSQL-Struktur weicht von der Baseline ab; keine Migration oder Übernahme ausgeführt.")
    if runtime_boundaries:
        policies = connection.exec_driver_sql(
            "SELECT c.relrowsecurity,p.polname,p.polpermissive,p.polcmd,p.polroles,"
            "pg_get_expr(p.polqual,p.polrelid),pg_get_expr(p.polwithcheck,p.polrelid) "
            "FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
            "LEFT JOIN pg_policy p ON p.polrelid=c.oid WHERE n.nspname='public' AND c.relname='datasets'"
        ).fetchall()
        expected = _sql_shape(rendered_predicate())
        if (
            len(policies) != 1
            or tuple(policies[0][:5]) != (True, "runtime_datasets", True, "*", [0])
            or any(_sql_shape(value) != expected for value in policies[0][5:])
        ):
            raise RuntimeError(
                "PostgreSQL-Struktur: F09-Datensatzgrenze fehlt oder weicht ab; keine automatische Reparatur."
            )
    version = connection.exec_driver_sql("SELECT version FROM public.schema_version").fetchall()
    if version != [(2,)]:
        raise RuntimeError("Nicht unterstützte PostgreSQL-Schemaversion; keine Übernahme ausgeführt.")


def _status(connection):
    script = ScriptDirectory.from_config(migration_config())
    head = script.get_current_head()
    inspector = sa.inspect(connection)
    if inspector.has_table("alembic_version", schema="public"):
        columns = inspector.get_columns("alembic_version", schema="public")
        primary = inspector.get_pk_constraint("alembic_version", schema="public")["constrained_columns"]
        if (
            len(columns) != 1
            or columns[0]["name"] != "version_num"
            or columns[0]["nullable"]
            or not isinstance(columns[0]["type"], sa.String)
            or columns[0]["type"].length != 32
            or columns[0]["default"] is not None
            or primary != ["version_num"]
        ):
            raise RuntimeError("Ungültige PostgreSQL-Migrationsmetadaten; keine Übernahme ausgeführt.")
    revisions = MigrationContext.configure(connection, opts={"version_table_schema": "public"}).get_current_heads()
    if len(revisions) > 1:
        raise RuntimeError("Mehrere PostgreSQL-Migrationsstände; manuelle Prüfung erforderlich.")
    tables = set(inspector.get_table_names(schema="public")) - {"alembic_version"}
    if revisions:
        revision = revisions[0]
        try:
            script.get_revision(revision)
        except (ResolutionError, CommandError):
            raise RuntimeError("Unbekannter PostgreSQL-Migrationsstand; passendes Release erforderlich.") from None
        if revision in KNOWN_REVISIONS:
            level = KNOWN_REVISIONS.index(revision)
            validate_baseline(
                connection,
                runtime_boundaries=level >= 1,
                fact_sheet_state=level >= 2,
                review_indexes=level >= 3,
                job_listings=level >= 4,
            )
        return {"state": "current" if revision == head else "outdated", "revision": revision, "head": head}
    if (
        not tables
        and not inspector.get_view_names(schema="public")
        and not inspector.get_materialized_view_names(schema="public")
    ):
        return {"state": "empty", "revision": None, "head": head}
    validate_baseline(connection)
    return {"state": "legacy", "revision": None, "head": head}


def schema_status():
    """Inspect the target through a technically read-only admin connection."""
    with admin_connection(readonly=True) as connection:
        return _status(connection)


def _protect_version_tables(connection):
    """Override inherited default grants: migration markers belong only to their owner."""
    quote = connection.dialect.identifier_preparer.quote_identifier
    for table in ("schema_version", "alembic_version"):
        roles = connection.exec_driver_sql(
            "SELECT DISTINCT pg_get_userbyid(acl.grantee) FROM pg_class c "
            "JOIN pg_namespace n ON n.oid = c.relnamespace "
            "CROSS JOIN LATERAL aclexplode(c.relacl) acl "
            "WHERE n.nspname = 'public' AND c.relname = %s AND acl.grantee NOT IN (0, c.relowner)",
            (table,),
        ).scalars()
        for role in ["PUBLIC", *roles]:
            recipient = "PUBLIC" if role == "PUBLIC" else quote(role)
            connection.exec_driver_sql(f"REVOKE ALL ON TABLE public.{quote(table)} FROM {recipient}")


def migrate():
    """Atomically create, adopt or upgrade; failed validation never repairs legacy drift."""
    with admin_connection() as connection:
        key = int.from_bytes(hashlib.sha256(b"jobfinder-schema").digest()[:8], "big", signed=True)
        connection.exec_driver_sql("SELECT pg_advisory_xact_lock(%s)", (key,))
        # Keep legacy validation and stamping safe from concurrent DDL, while allowing DML.
        tables = set(sa.inspect(connection).get_table_names(schema="public")) & (
            metadata.tables.keys() | legacy_metadata.tables.keys()
        )
        if tables:
            quote = connection.dialect.identifier_preparer.quote_identifier
            names = ", ".join(f"public.{quote(table)}" for table in sorted(tables))
            connection.exec_driver_sql(f"LOCK TABLE {names} IN SHARE UPDATE EXCLUSIVE MODE")
        before = _status(connection)
        config = migration_config(connection)
        if before["state"] == "legacy":
            command.stamp(config, BASELINE_REVISION)
        command.upgrade(config, "head")
        _protect_version_tables(connection)
        result = _status(connection)
        return {
            **result,
            "action": {"empty": "created", "legacy": "adopted", "current": "unchanged", "outdated": "upgraded"}[
                before["state"]
            ],
        }
