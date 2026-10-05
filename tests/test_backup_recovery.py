"""Recovery failure tests against an explicitly isolated PostgreSQL database."""

import base64
import json
import os
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from job_finder.persistence import document_store
from job_finder.persistence.application_documents import store_documents
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
