"""Tests for unattended-run logging and state backups."""

import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from job_finder.operations import RunLog, create_backup


class OperationsTests(unittest.TestCase):
    def test_run_log_captures_output_and_success(self):
        with tempfile.TemporaryDirectory() as directory:
            started = datetime(2026, 7, 17, 9, 0, 0)
            with RunLog(directory, now=started) as run_log:
                print("Neue Jobs: 4")
            content = run_log.path.read_text(encoding="utf-8")

        self.assertIn("Lauf gestartet", content)
        self.assertIn("Neue Jobs: 4", content)
        self.assertIn("Lauf erfolgreich beendet", content)

    def test_postgres_backup_keeps_only_the_newest_archives(self):
        for keep, expected in ((2, ["20260903", "20260904"]), (0, ["20260904"])):
            with self.subTest(keep=keep), tempfile.TemporaryDirectory() as directory:
                backup_dir = Path(directory)
                for day in (1, 2, 3):
                    (backup_dir / f"postgres-2026090{day}-090000.zip").write_bytes(b"old")
                (backup_dir / "notes.txt").write_text("keep", encoding="utf-8")
                new_archive = backup_dir / "postgres-20260904-090000.zip"

                def fake_backup(target, archive=new_archive):
                    archive.write_bytes(b"new")
                    return archive

                # Patch both import styles so the test survives moving the import.
                with (
                    patch(
                        "job_finder.persistence.postgres_backup.create_postgres_backup",
                        side_effect=fake_backup,
                    ),
                    patch(
                        "job_finder.operations.create_postgres_backup",
                        side_effect=fake_backup,
                        create=True,
                    ),
                ):
                    archive = create_backup(backup_dir=backup_dir, keep=keep)
                archives = sorted(path.name[9:17] for path in backup_dir.glob("postgres-*.zip"))

                self.assertEqual(archive, new_archive)
                self.assertEqual(archives, expected)
                self.assertTrue((backup_dir / "notes.txt").exists())

    def test_run_log_captures_unhandled_error_details(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(RuntimeError, "kaputt"):
                with RunLog(directory) as run_log:
                    raise RuntimeError("kaputt")
            content = run_log.path.read_text(encoding="utf-8")

        self.assertIn("Lauf fehlgeschlagen: RuntimeError: kaputt", content)
        self.assertIn("Traceback", content)


if __name__ == "__main__":
    unittest.main()
