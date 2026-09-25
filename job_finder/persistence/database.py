"""PostgreSQL connections and versioned schema shared by worker and review."""

import hashlib
import os
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path

import psycopg
from dotenv import load_dotenv

from job_finder.paths import MEMORY_FILE, PROJECT_DIR

SCHEMA_VERSION = 2
_connection = ContextVar("jobfinder_connection", default=None)

SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_version (version integer PRIMARY KEY);
CREATE TABLE IF NOT EXISTS job_state (
    scope text NOT NULL,
    job_id text NOT NULL,
    title text,
    company text,
    workflow_status text,
    active boolean,
    missed_runs integer,
    salary_expectation_eur bigint,
    personal_rating text,
    review_note text,
    present text[] NOT NULL,
    extra jsonb NOT NULL,
    PRIMARY KEY (scope, job_id)
);
CREATE INDEX IF NOT EXISTS job_state_status ON job_state(scope, workflow_status);
CREATE TABLE IF NOT EXISTS workflow_history (
    scope text NOT NULL,
    job_id text NOT NULL,
    position integer NOT NULL,
    status text,
    occurred_on date,
    scheduled_for timestamp,
    present text[] NOT NULL,
    extra jsonb NOT NULL,
    PRIMARY KEY (scope, job_id, position),
    FOREIGN KEY (scope, job_id) REFERENCES job_state ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS application_documents (
    scope text NOT NULL,
    job_id text NOT NULL,
    position integer NOT NULL,
    id text,
    name text,
    kind text,
    stored_name text,
    folder_name text,
    present text[] NOT NULL,
    extra jsonb NOT NULL,
    PRIMARY KEY (scope, job_id, position),
    FOREIGN KEY (scope, job_id) REFERENCES job_state ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS datasets (
    name text PRIMARY KEY,
    metadata jsonb NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS jobs (
    dataset text NOT NULL REFERENCES datasets(name) ON DELETE CASCADE,
    job_id text NOT NULL,
    position integer NOT NULL,
    title text,
    company text,
    present text[] NOT NULL,
    extra jsonb NOT NULL,
    PRIMARY KEY (dataset, position)
);
CREATE TABLE IF NOT EXISTS recommendations (
    dataset text NOT NULL REFERENCES datasets(name) ON DELETE CASCADE,
    job_id text NOT NULL,
    position integer NOT NULL,
    title text,
    company text,
    match_percent double precision,
    present text[] NOT NULL,
    extra jsonb NOT NULL,
    PRIMARY KEY (dataset, position)
);
CREATE TABLE IF NOT EXISTS notifications (
    dataset text NOT NULL REFERENCES datasets(name) ON DELETE CASCADE,
    notification_key text NOT NULL,
    delivery_state text NOT NULL CHECK (delivery_state IN ('sent', 'pending')),
    job_id text,
    sent_at text,
    attempts integer,
    present text[] NOT NULL,
    extra jsonb NOT NULL,
    PRIMARY KEY (dataset, notification_key, delivery_state)
);
CREATE TABLE IF NOT EXISTS source_cache (
    dataset text NOT NULL REFERENCES datasets(name) ON DELETE CASCADE,
    cache_key text NOT NULL,
    position integer NOT NULL,
    payload jsonb NOT NULL,
    stored_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (dataset, cache_key)
);
CREATE TABLE IF NOT EXISTS manual_sources (
    dataset text NOT NULL REFERENCES datasets(name) ON DELETE CASCADE,
    url text NOT NULL,
    position integer NOT NULL,
    payload jsonb NOT NULL,
    PRIMARY KEY (dataset, url)
);
CREATE TABLE IF NOT EXISTS migration_runs (
    source_fingerprint text PRIMARY KEY,
    completed_at timestamptz NOT NULL DEFAULT now(),
    summary jsonb NOT NULL
);
"""


def database_url():
    """Environment wins over ignored local configuration; never log credentials."""
    load_dotenv(PROJECT_DIR / ".env.postgres", override=False)
    value = os.getenv("JOBFINDER_DATABASE_URL")
    if not value:
        raise RuntimeError(
            "JOBFINDER_DATABASE_URL fehlt. PostgreSQL einrichten; siehe docs/postgresql.md."
        )
    return value


def admin_database_url():
    """DDL-capable connection; only initialize() may use this, never runtime reads/writes."""
    load_dotenv(PROJECT_DIR / ".env.postgres", override=False)
    value = os.getenv("JOBFINDER_ADMIN_DATABASE_URL")
    if not value:
        raise RuntimeError(
            "JOBFINDER_ADMIN_DATABASE_URL fehlt. PostgreSQL einrichten; siehe docs/postgresql.md."
        )
    return value


@contextmanager
def transaction(*, admin=False):
    """Reuse the current transaction so multi-repository writes commit together."""
    existing = _connection.get()
    if existing is not None:
        with existing.transaction():
            yield existing
        return
    url = admin_database_url() if admin else database_url()
    with psycopg.connect(url, connect_timeout=10) as connection:
        connection.execute("SET LOCAL lock_timeout = '30s'")
        token = _connection.set(connection)
        try:
            yield connection
        finally:
            _connection.reset(token)


def lock(connection, name, *, shared=False):
    """Serialize related writes across processes, including empty datasets."""
    key = int.from_bytes(hashlib.sha256(name.encode()).digest()[:8], "big", signed=True)
    function = "pg_advisory_xact_lock_shared" if shared else "pg_advisory_xact_lock"
    connection.execute(f"SELECT {function}(%s)", (key,))


@contextmanager
def snapshot():
    """Read a coherent multi-table view without blocking application writers."""
    nested = _connection.get() is not None
    with transaction() as connection:
        if not nested:
            connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        yield connection


def initialize():
    """Explicitly initialize the application schema, without touching user data."""
    with transaction(admin=True) as connection:
        lock(connection, "jobfinder-schema")
        connection.execute(SCHEMA)
        versions = connection.execute("SELECT version FROM schema_version").fetchall()
        if versions and versions != [(SCHEMA_VERSION,)]:
            raise RuntimeError("Nicht unterstützte PostgreSQL-Schemaversion")
        connection.execute(
            "INSERT INTO schema_version VALUES (%s) ON CONFLICT DO NOTHING", (SCHEMA_VERSION,)
        )


def memory_scope(path):
    """Use one runtime scope and isolate explicit test namespaces."""
    if path is None or Path(path).resolve() == MEMORY_FILE.resolve():
        return "default"
    if os.environ.get("JOBFINDER_TEST_MODE") != "1":
        raise RuntimeError("Dateipfade als Datenbankziel sind nur in isolierten Tests erlaubt.")
    return "explicit:" + hashlib.sha256(str(Path(path).resolve()).encode()).hexdigest()


@contextmanager
def worker_lock():
    """Allow one worker run at a time; a crashed connection releases its lock."""
    key = int.from_bytes(hashlib.sha256(b"jobfinder-worker").digest()[:8], "big", signed=True)
    with psycopg.connect(database_url(), autocommit=True, connect_timeout=10) as connection:
        acquired = connection.execute("SELECT pg_try_advisory_lock(%s)", (key,)).fetchone()[0]
        if not acquired:
            raise RuntimeError("Ein anderer Finder-Lauf ist bereits aktiv.")
        try:
            yield
        finally:
            connection.execute("SELECT pg_advisory_unlock(%s)", (key,))
