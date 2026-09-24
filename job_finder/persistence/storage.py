"""PostgreSQL runtime datasets and explicit JSON import/export helpers."""

import json
from contextlib import nullcontext
from pathlib import Path

from job_finder.paths import DATA_DIR, INTERNAL_DIR, OUTPUT_DIR, RECOMMENDATIONS_JSON
from job_finder.persistence.database import lock, transaction
from job_finder.persistence.postgres_store import read_dataset, write_dataset


def dataset_name(path):
    """Default runtime locations identify database datasets, not live JSON files."""
    target = Path(path).resolve()
    if target.parent in {INTERNAL_DIR.resolve(), OUTPUT_DIR.resolve()} and target.suffix == ".json":
        return target.relative_to(DATA_DIR.resolve()).as_posix()
    return None


def read_json(path, default=None):
    """Read a runtime dataset or an explicitly selected JSON import file."""
    name = dataset_name(path)
    if name is not None:
        return read_dataset(name, default)
    target = Path(path)
    if not target.exists():
        return default
    return json.loads(target.read_text(encoding="utf-8"))


def write_json_atomic(path, value):
    """Commit runtime data to PostgreSQL, or atomically export an explicit file."""
    name = dataset_name(path)
    if name is not None:
        write_dataset(name, value)
        return
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(f"{destination.suffix}.tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding="utf-8")
    temporary.replace(destination)


def read_versioned(path, version):
    """Return a stored cache document of this format version, else {}."""
    try:
        document = read_json(path, {})
    except (json.JSONDecodeError, OSError):
        return {}
    return document if document.get("version") == version else {}


def write_versioned(path, version, **fields):
    """Atomically store one cache document with its format version."""
    write_json_atomic(path, {"version": version, **fields})


def _kept_from_previous(value_sources, exclude_sources, *, ignore=frozenset()):
    """Keep an entry a run didn't recollect: manual, or entirely excluded sources."""
    names = {source.get("source") for source in value_sources} - ignore
    return "manual" in names or (bool(names) and names <= set(exclude_sources))


def publish_results(jobs, results, *, jobs_path, writer, exclude_sources=frozenset()):
    """Publish both result views, retaining manual imports and excluded sources.

    A split schedule (--exclude-sources) only recollects some sources per run;
    entries whose sources were all skipped this run must survive the write
    instead of being dropped as if they no longer existed.
    """
    values = [job.to_dict() for job in jobs]
    managed = dataset_name(jobs_path) is not None
    with transaction() if managed else nullcontext() as connection:
        if managed:
            lock(connection, "finder-publication")
            ids = {value["id"] for value in values}
            values.extend(
                value
                for value in read_json(jobs_path, [])
                if value["id"] not in ids
                and _kept_from_previous(value.get("sources", []), exclude_sources)
            )
            previous = read_json(RECOMMENDATIONS_JSON, {}).get("recommendations", [])
        write_json_atomic(jobs_path, values)
        writer(results)
        if managed:
            updated = read_json(RECOMMENDATIONS_JSON, {"recommendations": []})
            ids = {value["id"] for value in updated["recommendations"]}
            updated["recommendations"].extend(
                value
                for value in previous
                if value["id"] not in ids
                and _kept_from_previous(
                    value.get("source_links", []), exclude_sources, ignore={"original"}
                )
            )
            write_json_atomic(RECOMMENDATIONS_JSON, updated)
