"""Recovery failure tests against an explicitly isolated PostgreSQL database."""

import base64
import hashlib
import json
import os
import tempfile
import unittest
import zipfile
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from document_store_helpers import FakeVersionedContainer

from job_finder.persistence import document_store
from job_finder.persistence.application_documents import read_document, resolve_document_key, store_documents
from job_finder.persistence.database import transaction
from job_finder.persistence.postgres_backup import create_postgres_backup, restore_backup
from job_finder.workflow.memory import load_memory, save_memory


class LocalRestoreCleanupTests(unittest.TestCase):
    def test_cleanup_preserves_root_and_other_files(self):
        for other_file in (False, True):
            with (
                self.subTest(other_file=other_file),
                tempfile.TemporaryDirectory() as directory,
                patch.dict(os.environ, JOBFINDER_DOCUMENTS_BACKEND="local"),
            ):
                root = Path(directory)
                folder = root / "application" / "nested"
                folder.mkdir(parents=True)
                (folder / "restored.pdf").write_bytes(b"failed restore content")
                sentinel = folder / "keep.pdf"
                if other_file:
                    sentinel.write_bytes(b"other writer's content")
                document_store.delete("application/nested/restored.pdf", root, prune_empty=True)
                self.assertTrue(root.is_dir())
                self.assertFalse((folder / "restored.pdf").exists())
                if other_file:
                    self.assertEqual(sentinel.read_bytes(), b"other writer's content")
                else:
                    self.assertTrue(document_store.is_empty(root))


class BackupRecoveryTests(unittest.TestCase):
    def setUp(self):
        if os.environ.get("JOBFINDER_TEST_MODE") != "1":
            self.skipTest("Use scripts/test_postgres.py with an isolated test database")
        self.clear_database()
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    def clear_database(self):
        with transaction() as connection:
            self.assertTrue(connection.info.dbname.endswith("_test"))
            connection.execute("TRUNCATE job_state,datasets,agent_usage,agent_fact_sheets CASCADE")

    def backup(self):
        documents = store_documents(
            "synthetic:recovery",
            [
                {"kind": kind, "name": f"{kind}.pdf", "content": base64.b64encode(kind.encode()).decode()}
                for kind in ("cover_letter", "resume")
            ],
            self.root / "documents",
        )
        save_memory({"synthetic:recovery": {"workflow_status": "applied", "application_documents": documents}})
        archive = create_postgres_backup(self.root / "backups", self.root / "documents")
        self.clear_database()
        return archive

    def rewrite(self, archive, *, corrupt=False, omit=False):
        target = self.root / "changed.zip"
        with zipfile.ZipFile(archive) as source, zipfile.ZipFile(target, "w") as result:
            manifest = json.loads(source.read("manifest.json"))
            document = next(name for name in manifest["hashes"] if name.startswith("documents/"))
            if omit:
                del manifest["hashes"][document]
            for name in source.namelist():
                if name == "manifest.json":
                    content = json.dumps(manifest).encode()
                elif name == document:
                    if omit:
                        continue
                    content = b"changed" if corrupt else source.read(name)
                else:
                    content = source.read(name)
                result.writestr(name, content)
        return target

    def test_changed_document_is_rejected_without_database_or_file_writes(self):
        archive = self.rewrite(self.backup(), corrupt=True)
        target = self.root / "restored"
        with self.assertRaisesRegex(ValueError, "Prüfsumme"):
            restore_backup(archive, target)
        self.assertEqual(load_memory(), {})
        self.assertFalse(target.exists())

    def test_missing_referenced_document_rolls_back_database_and_other_documents(self):
        original = self.backup()
        archive = self.rewrite(original, omit=True)
        target = self.root / "restored"
        with self.assertRaises(FileNotFoundError):
            restore_backup(archive, target)
        self.assertEqual(load_memory(), {})
        self.assertEqual(list(target.rglob("*.pdf")), [])
        self.assertTrue(document_store.is_empty(target))
        self.assertTrue(restore_backup(original, target)["verified"])

    def test_existing_document_target_is_preserved_without_database_writes(self):
        archive = self.backup()
        target = self.root / "restored"
        target.mkdir()
        sentinel = target / "existing.pdf"
        sentinel.write_bytes(b"keep existing content")
        with self.assertRaisesRegex(ValueError, "Dokumentziel muss leer"):
            restore_backup(archive, target)
        self.assertEqual(sentinel.read_bytes(), b"keep existing content")
        self.assertEqual(load_memory(), {})

    def blob_backup(self, container, *, second_content=None):
        content = b"synthetic earlier document"
        metadata = store_documents(
            "synthetic:recovery",
            [{"kind": "resume", "name": "resume.pdf", "content": base64.b64encode(content).decode()}],
        )[0]
        key = resolve_document_key("synthetic:recovery", metadata)
        memory = {"synthetic:recovery": {"workflow_status": "applied", "application_documents": [metadata]}}
        if second_content is not None:
            other = deepcopy(metadata)
            other["id"] = "synthetic-second-reference"
            other["blob_version_id"] = container.get_blob_client(key).upload_blob(second_content, overwrite=True)[
                "version_id"
            ]
            other["sha256"] = hashlib.sha256(second_content).hexdigest()
            memory["synthetic:other"] = {"workflow_status": "rejected", "application_documents": [other]}
        save_memory(memory)
        return key, memory

    def test_blob_backup_uses_pinned_bytes_and_local_restore_discards_source_version(self):
        source = FakeVersionedContainer(version_prefix="source")
        with (
            patch.dict(os.environ, JOBFINDER_DOCUMENTS_BACKEND="blob"),
            patch.object(document_store, "_blob_container", return_value=source),
        ):
            key, memory = self.blob_backup(source)
            source.upload_blob(key, b"synthetic later document", overwrite=True)
            archive = create_postgres_backup(self.root / "backups")
        self.clear_database()
        restore_backup(archive, self.root / "restored")
        restored = load_memory()
        expected = deepcopy(memory)
        expected["synthetic:recovery"]["application_documents"][0].pop("blob_version_id")
        self.assertEqual(restored, expected)
        metadata = restored["synthetic:recovery"]["application_documents"][0]
        self.assertEqual(
            read_document("synthetic:recovery", metadata, self.root / "restored"), b"synthetic earlier document"
        )

    def test_shared_identical_document_versions_restore_all_references_to_target_version(self):
        source = FakeVersionedContainer(version_prefix="source")
        with (
            patch.dict(os.environ, JOBFINDER_DOCUMENTS_BACKEND="blob"),
            patch.object(document_store, "_blob_container", return_value=source),
        ):
            key, _ = self.blob_backup(source, second_content=b"synthetic earlier document")
            archive = create_postgres_backup(self.root / "backups")
            self.assertEqual(len(source.downloads), 2)
        with zipfile.ZipFile(archive) as saved:
            self.assertEqual(len([name for name in saved.namelist() if name.startswith("documents/")]), 1)
        self.clear_database()
        target = FakeVersionedContainer(version_prefix="target")
        with (
            patch.dict(os.environ, JOBFINDER_DOCUMENTS_BACKEND="blob"),
            patch.object(document_store, "_blob_container", return_value=target),
        ):
            restore_backup(archive, self.root / "restored")
            restored = load_memory()
            self.assertEqual(len(target.uploads), 1)
            for job_id, entry in restored.items():
                metadata = entry["application_documents"][0]
                self.assertEqual(metadata["blob_version_id"], target.current[key])
                self.assertTrue(metadata["blob_version_id"].startswith("target-"))
                self.assertEqual(read_document(job_id, metadata), b"synthetic earlier document")

    def test_conflicting_versions_at_one_key_abort_backup_without_an_archive(self):
        source = FakeVersionedContainer()
        with (
            patch.dict(os.environ, JOBFINDER_DOCUMENTS_BACKEND="blob"),
            patch.object(document_store, "_blob_container", return_value=source),
        ):
            self.blob_backup(source, second_content=b"synthetic different content")
            with self.assertRaisesRegex(ValueError, "unterschiedliche Dokumentversionen"):
                create_postgres_backup(self.root / "backups")
        self.assertEqual(list((self.root / "backups").iterdir()), [])

    def test_restore_does_not_overwrite_a_concurrent_document_writer(self):
        archive = self.backup()
        target = self.root / "restored"
        original_write = document_store.write
        concurrent = []

        def concurrent_write(key, content, root, **kwargs):
            path = Path(root) / key
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"concurrent writer's content")
            concurrent.append(path)
            return original_write(key, content, root, **kwargs)

        with patch.object(document_store, "write", side_effect=concurrent_write), self.assertRaises(FileExistsError):
            restore_backup(archive, target)
        self.assertEqual(load_memory(), {})
        self.assertEqual(len(concurrent), 1)
        self.assertEqual(concurrent[0].read_bytes(), b"concurrent writer's content")
