"""Small operational helpers for unattended local runs."""

import sys
import time
import traceback as traceback_module
import zipfile
from contextlib import AbstractContextManager, contextmanager
from datetime import datetime
from pathlib import Path

from job_finder.console import format_clock, log_event, new_run_id
from job_finder.paths import BACKUP_DIR, LOG_DIR, MEMORY_FILE

BACKUP_FILES_TO_KEEP = 7


class TeeStream:
    """Write console output to the original stream and one log file."""

    def __init__(self, original, log_file):
        self.original = original
        self.log_file = log_file
        self.progress_active = False
        self.progress_width = 0
        self.line_start = True

    def write(self, text):
        """Write text to both streams, timestamping logs and nonterminal output."""
        if self.progress_active and text:
            self.original.write("\r" + (" " * self.progress_width) + "\r")
            self.progress_active = False
            self.progress_width = 0
        terminal = bool(getattr(self.original, "isatty", lambda: False)())
        for part in text.splitlines(keepends=True):
            prefix = (
                datetime.now().astimezone().isoformat(timespec="seconds") + " "
                if self.line_start and part.strip()
                else ""
            )
            self.original.write(part if terminal else prefix + part)
            self.log_file.write(prefix + part)
            self.line_start = part.endswith("\n")
        self.original.flush()
        self.log_file.flush()
        return len(text)

    def write_progress(self, text, complete=False):
        """Update one terminal line while keeping logs free of redraws."""
        is_terminal = bool(getattr(self.original, "isatty", lambda: False)())
        if not is_terminal:
            self.write(f"{text}\n")
            return

        width = max(self.progress_width, len(text))
        self.original.write("\r" + text.ljust(width))
        self.original.flush()
        self.progress_width = width
        self.progress_active = not complete
        if complete:
            self.original.write("\n")
            self.original.flush()
            self.log_file.write(
                f"{datetime.now().astimezone().isoformat(timespec='seconds')} {text}\n"
            )
            self.log_file.flush()
            self.progress_width = 0

    def flush(self):
        """Flush the original stream and log file together."""
        self.original.flush()
        self.log_file.flush()

    def __getattr__(self, name):
        return getattr(self.original, name)


class RunLog(AbstractContextManager):
    """Capture one complete run while preserving normal console output."""

    def __init__(self, log_dir=LOG_DIR, now=None, run_id=None):
        self.log_dir = Path(log_dir)
        self.started_at = now or datetime.now().astimezone()
        self.path = self.log_dir / f"run-{self.started_at:%Y%m%d-%H%M%S}.log"
        self.run_id = run_id or new_run_id()
        self.log_file = None
        self.started_monotonic = None
        self.original_stdout = None
        self.original_stderr = None

    def __enter__(self):
        self.started_monotonic = time.monotonic()
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.log_file = self.path.open("w", encoding="utf-8")
        self.original_stdout = sys.stdout
        self.original_stderr = sys.stderr
        sys.stdout = TeeStream(self.original_stdout, self.log_file)
        sys.stderr = TeeStream(self.original_stderr, self.log_file)
        print(f"Lauf gestartet: {self.started_at.isoformat(timespec='seconds')}")
        print(f"Logdatei: {self.path}")
        log_event("run_started", run_id=self.run_id, log_path=str(self.path))
        return self

    def __exit__(self, error_type, error, traceback):
        finished_at = datetime.now().astimezone()
        duration_seconds = round(time.monotonic() - self.started_monotonic, 1)
        if error is None:
            print(
                f"Lauf erfolgreich beendet · Gesamtdauer {format_clock(time.monotonic() - self.started_monotonic)}"
            )
            log_event(
                "run_finished",
                run_id=self.run_id,
                duration_seconds=duration_seconds,
            )
        else:
            print(f"Lauf fehlgeschlagen: {type(error).__name__}: {error}")
            traceback_module.print_exception(error_type, error, traceback)
            print(f"Lauf beendet: {finished_at.isoformat(timespec='seconds')}")
            log_event(
                "run_failed",
                run_id=self.run_id,
                level="error",
                duration_seconds=duration_seconds,
                error_type=type(error).__name__,
                error=str(error),
            )
        sys.stdout = self.original_stdout
        sys.stderr = self.original_stderr
        self.log_file.close()
        return False


def create_backup(files, backup_dir=BACKUP_DIR, keep=BACKUP_FILES_TO_KEEP, now=None):
    """Archive existing persistent state and retain only recent backups."""
    if any(Path(path).resolve() == MEMORY_FILE.resolve() for path in files):
        from job_finder.persistence.postgres_backup import create_postgres_backup

        archive = create_postgres_backup(backup_dir)
        backups = sorted(Path(backup_dir).glob("postgres-*.zip"), reverse=True)
        for old_backup in backups[max(keep, 1) :]:
            old_backup.unlink()
        return archive
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
    for old_backup in backups[max(keep, 1) :]:
        old_backup.unlink()
    return archive


@contextmanager
def timed_step(label):
    """Log elapsed wall time even when a step fails or is interrupted."""
    started = time.monotonic()
    writer = getattr(sys.stdout, "write_progress", None)
    if writer is not None and getattr(sys.stdout, "isatty", lambda: False)():
        writer(f"  {label} …")
    else:
        print(f"  {label}: gestartet", flush=True)
    completed = False
    try:
        yield
        completed = True
    finally:
        outcome = "fertig" if completed else "abgebrochen/fehlgeschlagen"
        print(
            f"  {label}: {format_clock(time.monotonic() - started)} · {outcome}",
            flush=True,
        )
