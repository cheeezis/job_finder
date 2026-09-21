"""PostgreSQL runtime datasets and explicit JSON import/export helpers."""

import json
from contextlib import nullcontext
from pathlib import Path

from job_finder.paths import DATA_DIR, INTERNAL_DIR, OUTPUT_DIR


def dataset_name(path):
    """Default runtime locations identify database datasets, not live JSON files."""
    target = Path(path).resolve()
    if (
        target.parent in {INTERNAL_DIR.resolve(), OUTPUT_DIR.resolve()}
        and target.suffix == ".json"
    ):
        return target.relative_to(DATA_DIR.resolve()).as_posix()
    return None


def read_json(path, default=None):
    """Read a runtime dataset or an explicitly selected JSON import file."""
    name = dataset_name(path)
    if name is not None:
        from job_finder.postgres_store import read_dataset

        return read_dataset(name, default)
    target = Path(path)
    if not target.exists():
        return default
    return json.loads(target.read_text(encoding="utf-8"))


def write_json_atomic(path, value, *, indent=2):
    """Commit runtime data to PostgreSQL, or atomically export an explicit file."""
    name = dataset_name(path)
    if name is not None:
        from job_finder.postgres_store import write_dataset

        write_dataset(name, value)
        return
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(f"{destination.suffix}.tmp")
    temporary.write_text(
        json.dumps(value, indent=indent, ensure_ascii=False),
        encoding="utf-8",
    )
    temporary.replace(destination)


def publish_results(jobs, results, *, jobs_path, writer):
    """Publish both result views together, retaining concurrent manual imports."""
    from job_finder.database import lock, transaction
    from job_finder.paths import RECOMMENDATIONS_JSON

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
                and any(
                    source.get("source") == "manual"
                    for source in value.get("sources", [])
                )
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
                and any(
                    source.get("source") == "manual"
                    for source in value.get("source_links", [])
                )
            )
            write_json_atomic(RECOMMENDATIONS_JSON, updated)
