"""Exercise the application ZIP restore with synthetic data on local PostgreSQL.

Creates and removes two fresh databases; never reads an existing application DB.
This is not an Azure point-in-time restore or a proof of historical blob recovery.
"""

import argparse
import base64
import json
import os
import sys
import tempfile
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter

import psycopg
from dotenv import dotenv_values
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from job_finder.persistence import document_store  # noqa: E402
from job_finder.persistence.application_documents import (  # noqa: E402
    find_document,
    resolve_document_key,
    store_documents,
)
from job_finder.persistence.database import initialize  # noqa: E402
from job_finder.persistence.postgres_backup import create_postgres_backup, restore_backup  # noqa: E402
from job_finder.persistence.postgres_store import read_dataset, write_dataset  # noqa: E402
from job_finder.workflow.memory import load_memory, save_memory  # noqa: E402
from job_finder.workflow.review_data import load_review_jobs  # noqa: E402

PROJECT = Path(__file__).resolve().parents[1]
JOB_ID = "synthetic:restore-drill"
ORIGINAL_DOCUMENT = b"%PDF-1.4 synthetic original application document"


def local_parameters(env_file):
    """Validate the explicit maintenance file and pin connections to loopback."""
    values = dotenv_values(env_file, interpolate=False)
    url = values.get("JOBFINDER_ADMIN_DATABASE_URL") or values.get("JOBFINDER_DATABASE_URL")
    if not url:
        raise ValueError("Lokaler PostgreSQL-Zugang fehlt in der angegebenen Datei.")
    parameters = conninfo_to_dict(url)
    if (
        parameters.get("host") not in {"localhost", "127.0.0.1"}
        or parameters.get("hostaddr", "127.0.0.1") != "127.0.0.1"
        or parameters.get("service")
    ):
        raise ValueError("Die Probe erlaubt ausschließlich einen einzelnen lokalen PostgreSQL-Host.")
    return {**parameters, "hostaddr": "127.0.0.1", "dbname": "postgres", "connect_timeout": "10"}


@contextmanager
def isolated_environment():
    """Keep inherited cloud and notification settings out of the synthetic drill."""
    previous = dict(os.environ)
    try:
        for key in list(os.environ):
            if key.startswith(("JOBFINDER_", "PG")) or key == "DISCORD_WEBHOOK_URL":
                del os.environ[key]
        os.environ.update(
            JOBFINDER_DATABASE_AUTH="password",
            JOBFINDER_DOCUMENTS_BACKEND="local",
            JOBFINDER_TEST_MODE="1",
            # dotenv must not reload a real webhook from a local file.
            DISCORD_WEBHOOK_URL="",
            JOBFINDER_OPENAI_ENDPOINT="",
        )
        yield
    finally:
        os.environ.clear()
        os.environ.update(previous)


def select_database(parameters, name):
    """Use only a fresh database created by this invocation."""
    url = make_conninfo(**{**parameters, "dbname": name})
    os.environ.update(JOBFINDER_DATABASE_URL=url, JOBFINDER_ADMIN_DATABASE_URL=url)


def seed_source(root):
    """Represent one application, its review card, history and sent notification."""
    initialize()
    documents = store_documents(
        JOB_ID,
        [{"kind": "resume", "name": "resume.pdf", "content": base64.b64encode(ORIGINAL_DOCUMENT).decode()}],
        root / "source-documents",
        company="Example",
        title="Synthetic restore exercise",
    )
    memory = {
        JOB_ID: {
            "title": "Synthetic restore exercise",
            "company": "Example",
            "workflow_status": "applied",
            "workflow_history": [{"from": "new", "to": "applied", "at": "2026-01-01T12:00:00+00:00"}],
            "application_documents": documents,
        }
    }
    save_memory(memory)
    write_dataset(
        "output/recommendations.json",
        {"recommendations": [{"id": JOB_ID, "title": "Synthetic restore exercise", "company": "Example"}]},
    )
    notifications = {"version": 3, "sent": {JOB_ID: {"job_id": JOB_ID}}, "pending": {}}
    write_dataset("internal/notifications.json", notifications)
    return memory, notifications


def exercise(parameters, source, target, root):
    """Recover the earlier state after changing both source data and document bytes."""
    select_database(parameters, source)
    original, notifications = seed_source(root)
    archive = create_postgres_backup(root / "backups", root / "source-documents")
    changed = load_memory()
    changed[JOB_ID]["workflow_status"] = "rejected"
    save_memory(changed)
    metadata = original[JOB_ID]["application_documents"][0]
    key = resolve_document_key(JOB_ID, metadata)
    document_store.write(key, b"synthetic newer document", root / "source-documents")

    try:
        restore_backup(archive, root / "blocked-documents")
    except ValueError as error:
        if "leere Datenbank" not in str(error):
            raise
    else:
        raise RuntimeError("Eine gefüllte Datenbank wurde nicht geschützt.")

    select_database(parameters, target)
    started = perf_counter()
    result = restore_backup(archive, root / "restored-documents")
    restored = load_memory()
    found = find_document(restored[JOB_ID], metadata["id"])
    checks = {
        "database_state_matches_backup": restored == original,
        "sent_notification_preserved": read_dataset("internal/notifications.json") == notifications,
        "review_shows_restored_application": any(
            card["id"] == JOB_ID and card["workflow_status"] == "applied" for card in load_review_jobs()
        ),
        "document_reference_resolves": resolve_document_key(JOB_ID, found) == key,
        "document_bytes_match_backup": document_store.read(key, root / "restored-documents") == ORIGINAL_DOCUMENT,
        "populated_database_restore_refused": True,
        "archive_checksums_verified": result["verified"],
    }
    duration = round(perf_counter() - started, 3)
    select_database(parameters, source)
    checks["source_changes_preserved"] = (
        load_memory() == changed and document_store.read(key, root / "source-documents") == b"synthetic newer document"
    )
    if not all(checks.values()):
        raise RuntimeError("Die lokale Wiederherstellungsprobe hat eine Abweichung gefunden.")
    return {"checks": checks, "local_restore_and_validation_seconds": duration}


def run_drill(env_file, report_path):
    """Delete only databases created successfully by this invocation, even on failure."""
    parameters = local_parameters(env_file)
    suffix = uuid.uuid4().hex[:16]
    names = [f"recovery_{kind}_{suffix}_test" for kind in ("source", "target")]
    created = []
    with isolated_environment(), psycopg.connect(**parameters, autocommit=True) as admin:
        if not admin.execute("SELECT rolsuper FROM pg_roles WHERE rolname=current_user").fetchone()[0]:
            raise ValueError("Die lokale Probe benötigt den lokalen PostgreSQL-Testadministrator.")
        try:
            for name in names:
                admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
                created.append(name)
            with tempfile.TemporaryDirectory(prefix="jobfinder-restore-") as directory:
                result = exercise(parameters, *names, Path(directory))
        finally:
            for name in reversed(created):
                admin.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))
        removed = not admin.execute("SELECT 1 FROM pg_database WHERE datname = ANY(%s)", (names,)).fetchone()
    report = {
        "verified_at_utc": datetime.now(UTC).isoformat(),
        "mode": "local_synthetic_application_zip",
        **result,
        "temporary_databases_removed": removed,
        "azure_pitr_and_historical_blobs_verified": False,
    }
    if not removed:
        raise RuntimeError("Temporäre Testdatenbanken wurden nicht vollständig entfernt.")
    path = Path(report_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=PROJECT / ".env.postgres")
    parser.add_argument("--report", type=Path, default=PROJECT / "tmp/recovery-drill.json")
    arguments = parser.parse_args()
    try:
        report = run_drill(arguments.env_file, arguments.report)
    except Exception as error:
        # Database and file errors may contain credentials, paths or DSNs.
        raise SystemExit(
            f"Lokale Restore-Probe abgebrochen ({type(error).__name__}); sensible Details zurückgehalten."
        ) from None
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
