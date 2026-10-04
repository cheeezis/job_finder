"""PostgreSQL connections and versioned schema shared by worker and review."""

import hashlib
import os
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path

import psycopg
from dotenv import load_dotenv

from job_finder.paths import MEMORY_FILE, PROJECT_DIR

_connection: ContextVar[psycopg.Connection | None] = ContextVar("jobfinder_connection", default=None)


def database_url():
    """Environment wins over ignored local configuration; never log credentials."""
    return _required_env("JOBFINDER_DATABASE_URL")


def admin_database_url():
    """Only explicit maintenance operations request the DDL-capable connection."""
    return _required_env("JOBFINDER_ADMIN_DATABASE_URL")


def _required_env(name):
    """Read one connection URL from the environment or the ignored .env.postgres."""
    load_dotenv(PROJECT_DIR / ".env.postgres", override=False)
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"{name} fehlt. PostgreSQL einrichten; siehe docs/operations.md.")
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
    """Compatibility entry point: all schema setup now uses validated Alembic migrations."""
    from job_finder.persistence.schema_migrations import migrate

    return migrate()


def memory_scope(path):
    """Use one runtime scope and isolate explicit test namespaces."""
    if path is None or Path(path).resolve() == MEMORY_FILE.resolve():
        return "default"
    if os.environ.get("JOBFINDER_TEST_MODE") != "1":
        raise RuntimeError("Dateipfade als Datenbankziel sind nur in isolierten Tests erlaubt.")
    return "explicit:" + hashlib.sha256(str(Path(path).resolve()).encode()).hexdigest()


@contextmanager
def session_lock(name, *, busy_message):
    """Serialize external work without holding a database write transaction."""
    key = int.from_bytes(hashlib.sha256(name.encode()).digest()[:8], "big", signed=True)
    with psycopg.connect(database_url(), autocommit=True, connect_timeout=10) as connection:
        acquired = connection.execute("SELECT pg_try_advisory_lock(%s)", (key,)).fetchone()
        if not (acquired and acquired[0]):
            raise RuntimeError(busy_message)
        try:
            yield
        finally:
            connection.execute("SELECT pg_advisory_unlock(%s)", (key,))


def worker_lock():
    """Allow one worker run at a time; a crashed connection releases its lock."""
    return session_lock("jobfinder-worker", busy_message="Ein anderer Finder-Lauf ist bereits aktiv.")


def in_transaction():
    """Return whether a caller would reuse an uncommitted application transaction."""
    return _connection.get() is not None
