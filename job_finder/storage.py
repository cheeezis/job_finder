"""Small atomic file helpers for replaceable JSON reports and caches."""

import json
from pathlib import Path


def write_json_atomic(path, value, *, indent=2):
    """Write UTF-8 JSON beside its destination and replace it atomically."""
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(f"{destination.suffix}.tmp")
    temporary.write_text(
        json.dumps(value, indent=indent, ensure_ascii=False),
        encoding="utf-8",
    )
    temporary.replace(destination)
