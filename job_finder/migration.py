"""One-way, verified migration from the legacy SQLite/JSON data directory."""

import hashlib
import json
import shutil
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from psycopg.types.json import Jsonb

from job_finder.application_documents import document_path
from job_finder.database import initialize, lock, transaction, worker_lock
from job_finder.paths import DATA_DIR
from job_finder.postgres_store import (
    read_dataset,
    read_memory,
    write_dataset,
    write_memory,
)
from job_finder.state_compat import decode_legacy_memory


def digest(value):
    """Compare logical values independently of JSON key order."""
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, ensure_ascii=False, separators=(",", ":")
        ).encode()
    ).hexdigest()


def read_legacy_memory(path):
    """SQLite is only used here, read-only, never as an application backend."""
    path = Path(path)
    if path.suffix == ".json":
        return decode_legacy_memory(json.loads(path.read_text(encoding="utf-8")))
    with closing(
        sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    ) as connection:
        version = connection.execute(
            "SELECT value FROM metadata WHERE key='schema_version'"
        ).fetchone()
        if version != ("1",):
            raise ValueError("Nicht unterstützte SQLite-Schemaversion")
        return {
            key: json.loads(value)
            for key, value in connection.execute(
                "SELECT job_id,payload_json FROM job_state"
            )
        }


def document_manifest(memory, root):
    """Require every referenced document to exist and record its byte hash."""
    manifest = {}
    for job_id, entry in memory.items():
        for metadata in entry.get("application_documents", []):
            path = document_path(job_id, metadata, root)
            manifest[path.relative_to(root).as_posix()] = hashlib.sha256(
                path.read_bytes()
            ).hexdigest()
    return manifest


def snapshot_legacy(source):
    """Copy JSON/documents and take a consistent SQLite backup; keep originals."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    target = source / "backups" / ("pre-postgres-" + stamp)
    target.mkdir(parents=True)
    for directory in ("internal", "output"):
        if (source / directory).exists():
            shutil.copytree(source / directory, target / directory)
    sqlite_path = source / "internal" / "job_finder.sqlite3"
    if sqlite_path.exists():
        with closing(
            sqlite3.connect(sqlite_path.resolve().as_uri() + "?mode=ro", uri=True)
        ) as origin:
            with closing(
                sqlite3.connect(target / "internal" / "job_finder.sqlite3")
            ) as backup:
                origin.backup(backup)
    return target


def migrate(source=DATA_DIR):
    """Import only into an empty target and verify every dataset before commit."""
    source = Path(source).resolve()
    initialize()
    with worker_lock():
        snapshot = snapshot_legacy(source)
        sqlite_path = snapshot / "internal" / "job_finder.sqlite3"
        memory = read_legacy_memory(
            sqlite_path
            if sqlite_path.exists()
            else snapshot / "internal" / "seen_jobs.json"
        )
        documents = document_manifest(
            memory, snapshot / "internal" / "application_documents"
        )
        original_documents = document_manifest(
            memory, source / "internal" / "application_documents"
        )
        if documents != original_documents:
            raise RuntimeError(
                "Dokumente änderten sich während der Sicherung; Migration abgebrochen."
            )
        datasets = {}
        for directory in ("internal", "output"):
            for path in sorted((snapshot / directory).glob("*.json")):
                if path.name == "seen_jobs.json":
                    continue  # Superseded by SQLite; retained in the source backup.
                datasets[f"{directory}/{path.name}"] = json.loads(
                    path.read_text(encoding="utf-8")
                )
        hashes = {name: digest(value) for name, value in datasets.items()}
        hashes["memory"] = digest(memory)
        hashes["documents"] = digest(documents)
        fingerprint = digest(hashes)
        with transaction() as connection:
            lock(connection, "finder-publication")
            lock(connection, "memory:default")
            completed = connection.execute(
                "SELECT summary FROM migration_runs WHERE source_fingerprint=%s",
                (fingerprint,),
            ).fetchone()
            if completed:
                return {"already_migrated": True, **completed[0]}
            if (
                connection.execute("SELECT 1 FROM job_state LIMIT 1").fetchone()
                or connection.execute("SELECT 1 FROM datasets LIMIT 1").fetchone()
            ):
                raise RuntimeError(
                    "Zieldatenbank enthält bereits Daten; kein Überschreiben erlaubt."
                )
            write_memory(connection, "default", {}, memory)
            for name, value in datasets.items():
                write_dataset(name, value)
            if digest(read_memory(connection, "default")) != hashes["memory"]:
                raise RuntimeError(
                    "Die übernommenen Zustandsdaten stimmen nicht überein."
                )
            for name, value in datasets.items():
                # JSON numeric equality intentionally accepts 82 and 82.0.
                if read_dataset(name) != value:
                    raise RuntimeError(f"Datenvergleich fehlgeschlagen: {name}")
            summary = {
                "jobs_remembered": len(memory),
                "history_events": sum(
                    len(e.get("workflow_history", [])) for e in memory.values()
                ),
                "documents": len(documents),
                "datasets": len(datasets),
                "source_backup": str(snapshot),
                "fingerprint": fingerprint,
                "verified": True,
            }
            connection.execute(
                "INSERT INTO migration_runs(source_fingerprint,summary) VALUES (%s,%s)",
                (fingerprint, Jsonb(summary)),
            )
        (snapshot / "migration-report.json").write_text(
            json.dumps(
                {**summary, "hashes": hashes, "documents_manifest": documents}, indent=2
            ),
            encoding="utf-8",
        )
        return summary
