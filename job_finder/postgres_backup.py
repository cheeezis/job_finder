"""Consistent PostgreSQL application backups with separately stored documents."""

import hashlib
import json
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from job_finder.database import initialize, lock, transaction
from job_finder.migration import document_manifest
from job_finder.paths import APPLICATION_DOCUMENTS_DIR, BACKUP_DIR
from job_finder.postgres_store import (
    read_dataset,
    read_memory,
    write_dataset,
    write_memory,
)


def create_postgres_backup(
    backup_dir=BACKUP_DIR, documents_dir=APPLICATION_DOCUMENTS_DIR
):
    """Back up a consistent database snapshot and verify referenced file bytes."""
    directory = Path(backup_dir)
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    target = directory / f"postgres-{stamp}.zip"
    temporary = target.with_suffix(".zip.tmp")
    hashes = {}
    try:
        with (
            transaction() as connection,
            zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED) as archive,
        ):
            connection.execute(
                "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"
            )
            memory = read_memory(connection, "default")
            documents = document_manifest(memory, Path(documents_dir))

            def add(name, content):
                archive.writestr(name, content)
                hashes[name] = hashlib.sha256(content).hexdigest()

            add("memory.json", json.dumps(memory, ensure_ascii=False).encode())
            names = [
                r[0]
                for r in connection.execute("SELECT name FROM datasets ORDER BY name")
            ]
            for name in names:
                add(
                    "datasets/" + name,
                    json.dumps(read_dataset(name), ensure_ascii=False).encode(),
                )
            for name, expected in documents.items():
                content = (Path(documents_dir) / name).read_bytes()
                if hashlib.sha256(content).hexdigest() != expected:
                    raise RuntimeError("Dokument während der Sicherung geändert.")
                add("documents/" + name, content)
            archive.writestr(
                "manifest.json", json.dumps({"version": 1, "hashes": hashes})
            )
        temporary.replace(target)
        return target
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def restore_backup(archive_path, documents_dir):
    """Restore into an empty database and document directory; verify before commit."""
    initialize()
    root = Path(documents_dir).resolve()
    if root.exists() and any(root.iterdir()):
        raise ValueError(
            "Dokumentziel muss leer sein; bestehende Dateien werden nicht überschrieben."
        )
    written = []
    with zipfile.ZipFile(archive_path) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        if manifest.get("version") != 1:
            raise ValueError("Unbekanntes Backup-Format")
        for name, expected in manifest["hashes"].items():
            relative = PurePosixPath(name)
            if (
                relative.is_absolute()
                or ".." in relative.parts
                or "\\" in name
                or ":" in name
            ):
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
                if (
                    connection.execute("SELECT 1 FROM job_state LIMIT 1").fetchone()
                    or connection.execute("SELECT 1 FROM datasets LIMIT 1").fetchone()
                ):
                    raise ValueError("Wiederherstellung benötigt eine leere Datenbank")
                write_memory(connection, "default", {}, memory)
                for name in manifest["hashes"]:
                    if name.startswith("datasets/"):
                        dataset = name.removeprefix("datasets/")
                        value = json.loads(archive.read(name))
                        write_dataset(dataset, value)
                        if read_dataset(dataset) != value:
                            raise RuntimeError(
                                "Datenvergleich nach Wiederherstellung fehlgeschlagen"
                            )
                    elif name.startswith("documents/"):
                        target = root / name.removeprefix("documents/")
                        target.parent.mkdir(parents=True, exist_ok=True)
                        with target.open("xb") as output:
                            output.write(archive.read(name))
                        written.append(target)
                if read_memory(connection, "default") != memory:
                    raise RuntimeError(
                        "Gedächtnis stimmt nach Wiederherstellung nicht überein"
                    )
                document_manifest(memory, root)
        except BaseException:
            for path in written:
                path.unlink(missing_ok=True)
            raise
    return {"jobs_remembered": len(memory), "documents": len(written), "verified": True}
