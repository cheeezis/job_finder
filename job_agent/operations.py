"""Small operational helpers for unattended local runs."""

import sys
import traceback as traceback_module
import zipfile
from contextlib import AbstractContextManager
from datetime import datetime
from pathlib import Path

from job_agent.paths import BACKUP_DIR, LOG_DIR


BACKUP_FILES_TO_KEEP = 7


class TeeStream:
    """Write console output to the original stream and one log file."""

    def __init__(self, original, log_file):
        self.original = original
        self.log_file = log_file

    def write(self, text):
        self.original.write(text)
        self.log_file.write(text)
        self.log_file.flush()
        return len(text)

    def flush(self):
        self.original.flush()
        self.log_file.flush()

    def __getattr__(self, name):
        return getattr(self.original, name)


class RunLog(AbstractContextManager):
    """Capture one complete run while preserving normal console output."""

    def __init__(self, log_dir=LOG_DIR, now=None):
        self.log_dir = Path(log_dir)
        self.started_at = now or datetime.now().astimezone()
        self.path = self.log_dir / f"run-{self.started_at:%Y%m%d-%H%M%S}.log"
        self.log_file = None
        self.original_stdout = None
        self.original_stderr = None

    def __enter__(self):
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.log_file = self.path.open("w", encoding="utf-8")
        self.original_stdout = sys.stdout
        self.original_stderr = sys.stderr
        sys.stdout = TeeStream(self.original_stdout, self.log_file)
        sys.stderr = TeeStream(self.original_stderr, self.log_file)
        print(f"Lauf gestartet: {self.started_at.isoformat(timespec='seconds')}")
        print(f"Logdatei: {self.path}")
        return self

    def __exit__(self, error_type, error, traceback):
        finished_at = datetime.now().astimezone()
        if error is None:
            print(f"Lauf erfolgreich beendet: {finished_at.isoformat(timespec='seconds')}")
        else:
            print(f"Lauf fehlgeschlagen: {type(error).__name__}: {error}")
            traceback_module.print_exception(error_type, error, traceback)
            print(f"Lauf beendet: {finished_at.isoformat(timespec='seconds')}")
        sys.stdout = self.original_stdout
        sys.stderr = self.original_stderr
        self.log_file.close()
        return False


def create_backup(files, backup_dir=BACKUP_DIR, keep=BACKUP_FILES_TO_KEEP, now=None):
    """Archive existing persistent state and retain only recent backups."""
    existing = [Path(path) for path in files if Path(path).is_file()]
    if not existing:
        return None

    timestamp = now or datetime.now().astimezone()
    directory = Path(backup_dir)
    directory.mkdir(parents=True, exist_ok=True)
    archive = directory / f"state-{timestamp:%Y%m%d-%H%M%S}.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        for path in existing:
            bundle.write(path, arcname=path.name)

    backups = sorted(directory.glob("state-*.zip"), reverse=True)
    for old_backup in backups[max(keep, 1):]:
        old_backup.unlink()
    return archive
