"""Tests for unattended-run logging and state backups."""

import tempfile
import unittest
import zipfile
from datetime import datetime
from pathlib import Path

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

    def test_backup_contains_state_and_removes_old_archives(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = root / "seen_jobs.json"
            state.write_text('{"version": 2}', encoding="utf-8")
            backup_dir = root / "backups"
            for index in range(3):
                (backup_dir / f"state-2026071{index}-090000.zip").parent.mkdir(
                    parents=True,
                    exist_ok=True,
                )
                (backup_dir / f"state-2026071{index}-090000.zip").write_bytes(b"old")

            archive = create_backup(
                [state, root / "missing.json"],
                backup_dir=backup_dir,
                keep=2,
                now=datetime(2026, 7, 17, 9, 0, 0),
            )

            backups = list(backup_dir.glob("state-*.zip"))
            with zipfile.ZipFile(archive) as bundle:
                names = bundle.namelist()

        self.assertEqual(len(backups), 2)
        self.assertEqual(names, ["seen_jobs.json"])

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
