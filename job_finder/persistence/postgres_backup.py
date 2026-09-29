"""Consistent PostgreSQL application backups with separately stored documents."""

import hashlib
import json
import zipfile
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

from psycopg import sql

from job_finder.paths import APPLICATION_DOCUMENTS_DIR, BACKUP_DIR
from job_finder.persistence import document_store
from job_finder.persistence.application_documents import live_document_manifest
from job_finder.persistence.database import initialize, lock, snapshot, transaction
from job_finder.persistence.postgres_store import read_dataset, read_memory, write_dataset, write_memory

# The agent's cost ledger and fact sheets with their sort keys. PostgreSQL renders them as JSON and parses them
# back itself, so amounts and time stamps stay exact.
AGENT_TABLES = {"agent_usage": "id", "agent_fact_sheets": "scope, job_id"}


def create_postgres_backup(backup_dir=BACKUP_DIR, documents_dir=APPLICATION_DOCUMENTS_DIR):
    """Back up a consistent database snapshot and verify referenced file bytes."""
    directory = Path(backup_dir)
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    target = directory / f"postgres-{stamp}.zip"
    temporary = target.with_suffix(".zip.tmp")
    hashes = {}
    try:
        with snapshot() as connection, zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED) as archive:
            memory = read_memory(connection, "default")
            documents = live_document_manifest(memory, documents_dir)

            def add(name, content):
                archive.writestr(name, content)
                hashes[name] = hashlib.sha256(content).hexdigest()

            add("memory.json", json.dumps(memory, ensure_ascii=False).encode())
            names = [r[0] for r in connection.execute("SELECT name FROM datasets ORDER BY name")]
            for name in names:
                add("datasets/" + name, json.dumps(read_dataset(name), ensure_ascii=False).encode())
            for table, order in AGENT_TABLES.items():
                query = sql.SQL("SELECT coalesce(json_agg(t ORDER BY {}), '[]')::text FROM {} t")
                rows = connection.execute(query.format(sql.SQL(order), sql.Identifier(table))).fetchone()[0]
                add(f"agent/{table}.json", rows.encode())
            for name, expected in documents.items():
                content = document_store.read(name, documents_dir)
                if hashlib.sha256(content).hexdigest() != expected:
                    raise RuntimeError("Dokument während der Sicherung geändert.")
                add("documents/" + name, content)
            archive.writestr("manifest.json", json.dumps({"version": 1, "hashes": hashes}))
        temporary.replace(target)
        return target
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def restore_backup(archive_path, documents_dir):
    """Restore into an empty database and document store; verify before commit."""
    initialize()
    if not document_store.is_empty(documents_dir):
        raise ValueError("Dokumentziel muss leer sein; bestehende Dateien werden nicht überschrieben.")
    written = []
    with zipfile.ZipFile(archive_path) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        if manifest.get("version") != 1:
            raise ValueError("Unbekanntes Backup-Format")
        for name, expected in manifest["hashes"].items():
            relative = PurePosixPath(name)
            if relative.is_absolute() or ".." in relative.parts or "\\" in name or ":" in name:
                raise ValueError("Ungültiger Backup-Pfad")
            if hashlib.sha256(archive.read(name)).hexdigest() != expected:
                raise ValueError("Backup-Prüfsumme stimmt nicht überein")
        if "memory.json" not in manifest["hashes"]:
            raise ValueError("Gedächtnis fehlt im Backup")
        memory = json.loads(archive.read("memory.json"))
        try:
            with transaction() as connection:
                lock(connection, "finder-publication")
                lock(connection, "memory:default")
                tables = ("job_state", "datasets", *AGENT_TABLES)
                query = sql.SQL("SELECT 1 FROM {} LIMIT 1")
                if any(connection.execute(query.format(sql.Identifier(table))).fetchone() for table in tables):
                    raise ValueError("Wiederherstellung benötigt eine leere Datenbank")
                write_memory(connection, "default", {}, memory)
                for name in manifest["hashes"]:
                    if name.startswith("datasets/"):
                        dataset = name.removeprefix("datasets/")
                        value = json.loads(archive.read(name))
                        write_dataset(dataset, value)
                        if read_dataset(dataset) != value:
                            raise RuntimeError("Datenvergleich nach Wiederherstellung fehlgeschlagen")
                    elif name.startswith("documents/"):
                        key = name.removeprefix("documents/")
                        if document_store.exists(key, documents_dir):
                            raise ValueError("Dokument existiert bereits am Zielort")
                        document_store.write(key, archive.read(name), documents_dir)
                        written.append(key)
                    elif name.startswith("agent/"):
                        restore_agent_table(connection, name, archive.read(name).decode())
                if read_memory(connection, "default") != memory:
                    raise RuntimeError("Gedächtnis stimmt nach Wiederherstellung nicht überein")
                live_document_manifest(memory, documents_dir)
        except BaseException:
            for key in written:
                document_store.delete(key, documents_dir)
            raise
    return {"jobs_remembered": len(memory), "documents": len(written), "verified": True}


def restore_agent_table(connection, name, rows):
    """Insert one backed-up agent table into its empty table and check that every row arrived unchanged."""
    table = name.removeprefix("agent/").removesuffix(".json")
    if table not in AGENT_TABLES:
        raise ValueError("Unbekannte Tabelle im Backup")
    identifier = sql.Identifier(table)
    records = sql.SQL("SELECT * FROM json_populate_recordset(NULL::{}, %s::json)").format(identifier)
    connection.execute(sql.SQL("INSERT INTO {} ").format(identifier) + records, (rows,))
    check = sql.SQL(
        "SELECT (SELECT count(*) FROM {0}) = json_array_length(%s::json) AND NOT EXISTS ({1} EXCEPT ALL SELECT * FROM {0})"
    )
    if not connection.execute(check.format(identifier, records), (rows, rows)).fetchone()[0]:
        raise RuntimeError("Datenvergleich nach Wiederherstellung fehlgeschlagen")
