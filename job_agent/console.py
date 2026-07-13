"""Console configuration shared by command-line entry points."""

import sys


def configure_utf8_output():
    """Use UTF-8 for job titles when the active streams support it."""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
