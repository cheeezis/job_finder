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


def _with_skipped_listings(current, previous, key, exclude_sources):
    """Carry the listings this run did not collect over from the previous entries.

    key names the listing list: "sources" for jobs, "source_links" for review
    cards. A job found again gains its listings from skipped sources; one not
    found again keeps just those listings, and its employer links. A manual
    import stays whole.
    """
    entries = {}
    for value in current:
        entries.setdefault(value["id"], value)
    kept = []
    for old in previous:
        listings = old.get(key, [])
        skipped = [listing for listing in listings if listing.get("source") in exclude_sources]
        entry = entries.get(old["id"])
        if entry is not None:
            known = {listing.get("url") for listing in entry.get(key, [])}
            added = [listing for listing in skipped if listing.get("url") not in known]
            if added:
                entry[key] = [*entry.get(key, []), *added]
                entry["locations"] = list(
                    dict.fromkeys([*entry.get("locations", []), *old.get("locations", [])])
                )
        elif any(listing.get("source") == "manual" for listing in listings):
            kept.append(old)
        elif skipped:
            remaining = [
                listing
                for listing in listings
                if listing in skipped or listing.get("source") == "original"
            ]
            kept.append({**old, key: remaining})
    return [*current, *kept]


def publish_results(jobs, results, *, jobs_path, writer, exclude_sources=frozenset()):
    """Publish both result views, retaining manual imports and skipped sources' listings.

    A split schedule (--exclude-sources) only recollects some sources per
    run. The listings of the skipped sources must survive the write, also
    when the other run found the same job, instead of being dropped as if
    they no longer existed.
    """
    values = [job.to_dict() for job in jobs]
    managed = dataset_name(jobs_path) is not None
    with transaction() if managed else nullcontext() as connection:
        if managed:
            lock(connection, "finder-publication")
            values = _with_skipped_listings(
                values, read_json(jobs_path, []), "sources", exclude_sources
            )
            previous = read_json(RECOMMENDATIONS_JSON, {}).get("recommendations", [])
        write_json_atomic(jobs_path, values)
        writer(results)
        if managed:
            updated = read_json(RECOMMENDATIONS_JSON, {"recommendations": []})
            updated["recommendations"] = _with_skipped_listings(
                updated["recommendations"], previous, "source_links", exclude_sources
            )
            write_json_atomic(RECOMMENDATIONS_JSON, updated)
