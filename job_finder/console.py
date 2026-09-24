"""Console configuration shared by command-line entry points."""

import json
import sys
import time
import uuid
from datetime import datetime, timezone

_PROGRESS_STARTED = {}
_PROGRESS_REPORTED = {}
LOG_PROGRESS_INTERVAL = 30


def new_run_id():
    """Create one short identifier shared by every structured log line of a run."""
    return uuid.uuid4().hex[:12]


def log_event(event, *, run_id, level="info", **fields):
    """Print one structured JSON line for machine-readable log analysis.

    Alongside the human-readable progress output, so Azure Log Analytics
    receives a parseable JSON string in Log_s instead of only free text;
    query it with e.g. `Log_s | extend e = parse_json(Log_s)`.
    """
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "run_id": run_id,
        "event": event,
        "level": level,
        **fields,
    }
    print(json.dumps(entry, ensure_ascii=False))


def configure_utf8_output():
    """Use UTF-8 for job titles when the active streams support it."""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def print_progress(label, current, total, detail=""):
    """Update a tqdm-style terminal line with timing and throughput."""
    current = int(current)
    total = max(int(total), 1)
    if current <= 0 or label not in _PROGRESS_STARTED:
        _PROGRESS_STARTED[label] = time.monotonic()
    now = time.monotonic()
    elapsed = max(0.0, now - _PROGRESS_STARTED[label])
    terminal = bool(getattr(sys.stdout, "isatty", lambda: False)())
    if (
        not terminal
        and 0 < current < total
        and label in _PROGRESS_REPORTED
        and now - _PROGRESS_REPORTED[label] < LOG_PROGRESS_INTERVAL
    ):
        return
    _PROGRESS_REPORTED[label] = now
    line = progress_line(label, current, total, detail=detail, elapsed_seconds=elapsed)
    writer = getattr(sys.stdout, "write_progress", None)
    if writer is not None:
        writer(line, complete=current >= total)
    else:
        print(line)
    if current >= total:
        _PROGRESS_STARTED.pop(label, None)
        _PROGRESS_REPORTED.pop(label, None)


def progress_line(label, current, total, detail="", elapsed_seconds=0.0):
    """Format percentage, count, elapsed time, ETA and item rate."""
    total = max(int(total), 1)
    current = min(max(int(current), 0), total)
    elapsed = max(float(elapsed_seconds), 0.0)
    percent = round(current / total * 100)
    rate = current / elapsed if current and elapsed >= 0.05 else None
    remaining = total - current
    eta = remaining / rate if rate else None
    suffix = f" · {detail}" if detail else ""
    if total == 1:
        return f"  {label}: {detail or ('fertig' if current else 'wird geladen')} · {format_clock(elapsed)}"
    estimate = (
        f" · Rest ca. {format_clock(eta)}" if eta is not None and elapsed >= 5 and remaining else ""
    )
    return f"  {label}: {current}/{total} ({percent}%) · {format_clock(elapsed)}{estimate}{suffix}"


def format_clock(seconds):
    """Format a compact tqdm-like clock without fractional noise."""
    if seconds is None:
        return "?"
    rounded = max(0, round(seconds))
    hours, remainder = divmod(rounded, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
    return f"{minutes:02d}:{seconds:02d}"


def print_phase(current, total, label):
    """Show a plain heading for one coarse pipeline phase."""
    print(f"\n[{current}/{total}] {label}", flush=True)


def progress_checkpoint(current, total):
    """Limit long detail loops to useful, readable progress snapshots."""
    return current == 1 or current == total or current % 10 == 0
