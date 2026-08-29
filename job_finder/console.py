"""Console configuration shared by command-line entry points."""

import sys
import time


PROGRESS_WIDTH = 24
_PROGRESS_STARTED = {}


def configure_utf8_output():
    """Use UTF-8 for job titles when the active streams support it."""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def progress_bar(current, total, width=PROGRESS_WIDTH):
    """Return one stable text bar suitable for consoles and log files."""
    total = max(int(total), 1)
    current = min(max(int(current), 0), total)
    filled = round(width * current / total)
    return f"[{'#' * filled}{'-' * (width - filled)}]"


def print_progress(label, current, total, detail=""):
    """Update a tqdm-style terminal line with timing and throughput."""
    current = int(current)
    total = max(int(total), 1)
    if current <= 0 or label not in _PROGRESS_STARTED:
        _PROGRESS_STARTED[label] = time.monotonic()
    elapsed = max(0.0, time.monotonic() - _PROGRESS_STARTED[label])
    line = progress_line(
        label,
        current,
        total,
        detail=detail,
        elapsed_seconds=elapsed,
    )
    writer = getattr(sys.stdout, "write_progress", None)
    if writer is not None:
        writer(line, complete=current >= total)
    else:
        print(line)
    if current >= total:
        _PROGRESS_STARTED.pop(label, None)


def progress_line(label, current, total, detail="", elapsed_seconds=0.0):
    """Format percentage, count, elapsed time, ETA and item rate."""
    total = max(int(total), 1)
    current = min(max(int(current), 0), total)
    elapsed = max(float(elapsed_seconds), 0.0)
    percent = round(current / total * 100)
    rate = current / elapsed if current and elapsed >= 0.05 else None
    remaining = total - current
    eta = remaining / rate if rate else None
    rate_text = f"{rate:.2f} it/s" if rate is not None else "? it/s"
    eta_text = format_clock(eta) if eta is not None else "?"
    suffix = f" · {detail}" if detail else ""
    return (
        f"  {label} {percent:3d}%|{progress_bar(current, total)[1:-1]}| "
        f"{current}/{total} "
        f"[{format_clock(elapsed)}<{eta_text}, {rate_text}]{suffix}"
    )


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
    print(f"\n{current}/{total} {label}")


def progress_checkpoint(current, total, interval=10):
    """Limit long detail loops to useful, readable progress snapshots."""
    return current == 1 or current == total or current % interval == 0
