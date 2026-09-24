"""Targeted processing for one job URL explicitly supplied by the user."""

from contextlib import nullcontext

from job_finder.matching.deduplication import deduplicate_jobs, merge_jobs
from job_finder.paths import JOBS_FILE, MEMORY_FILE, RECOMMENDATIONS_JSON
from job_finder.persistence.database import lock, transaction
from job_finder.persistence.storage import dataset_name, read_json, write_json_atomic
from job_finder.sources import manual
from job_finder.sources.common import canonical_detail_url
from job_finder.workflow.main import load_jobs, score_for_pipeline
from job_finder.workflow.memory import edit_memory, update_memory
from job_finder.workflow.reporting import recommendation_for_job


def import_manual_url(
    url,
    *,
    cache_path=manual.MANUAL_CACHE_FILE,
    jobs_path=JOBS_FILE,
    memory_path=MEMORY_FILE,
    recommendations_path=RECOMMENDATIONS_JSON,
):
    """Fetch, remember and score one user-supplied public listing URL.

    Write the manual source, update PostgreSQL state, and replace the job's
    entries in the job snapshot and recommendations. Unrelated review
    results are retained. Default runtime writes share one transaction.

    Return job_id, title, company, match_percent and prefilter_warning.
    Manual submissions remain reviewable even when the filter rejects
    them; prefilter_warning explains that conflict.

    Invalid URLs or unrecognized pages raise ValueError. Network,
    filesystem and database failures propagate to the caller.
    """
    # Download before taking a database lock. Persist the manual source and
    # both review datasets in the same transaction as the remembered job.
    if dataset_name(cache_path):
        requested_url = manual.validate_public_url(url)
        final_url, html = manual.fetch_text_with_final_url(
            requested_url, url_validator=manual.validate_public_url
        )
        imported = manual.job_from_page(final_url, html)
    else:
        imported = manual.add_url(url, cache_path=cache_path)
    with transaction() if dataset_name(jobs_path) else nullcontext() as connection:
        if connection is not None:
            lock(connection, "finder-publication")
            cache = manual.load_detail_cache(cache_path)
            cache[canonical_detail_url(imported.primary_url)] = imported
            manual.save_detail_cache(cache_path, cache)
        return _persist_import(imported, jobs_path, memory_path, recommendations_path)


def _persist_import(imported, jobs_path, memory_path, recommendations_path):
    jobs = load_jobs(jobs_path)
    target = replace_or_add_job(jobs, imported)

    with edit_memory(memory_path) as memory:
        update_memory([target], memory, successful_sources=None)
    save_jobs(jobs, jobs_path)

    score = score_for_pipeline(target)
    warning = score.get("prefilter_warning")

    row = {**target.to_dict(), "is_new": target.is_new, **score}
    save_recommendation(row, recommendations_path)
    return {
        "job_id": row["id"],
        "title": row["title"],
        "company": row["company"],
        "match_percent": row.get("match_percent"),
        "prefilter_warning": warning,
    }


def replace_or_add_job(jobs, imported):
    """Update an existing exact URL or cross-source duplicate in-place."""
    imported_url = canonical_detail_url(imported.primary_url)
    for index, existing in enumerate(jobs):
        existing_urls = {canonical_detail_url(source.url) for source in existing.sources}
        if imported_url in existing_urls:
            jobs[index] = merge_jobs(existing, imported)
            return jobs[index]

    merged = deduplicate_jobs([*jobs, imported])
    jobs[:] = merged
    for job in jobs:
        if any(
            source.source == manual.SOURCE_NAME and canonical_detail_url(source.url) == imported_url
            for source in job.sources
        ):
            return job
    raise RuntimeError("Die manuell importierte Stelle konnte nicht zugeordnet werden")


def save_jobs(jobs, path):
    """Atomically replace the JSON snapshot with serialized Job objects."""
    write_json_atomic(path, [job.to_dict() for job in jobs])


def save_recommendation(job, path):
    """Replace only the imported card and preserve all other review results."""
    document = read_json(path, {"recommendations": []})
    recommendation = recommendation_for_job(job)
    urls = {link["url"] for link in recommendation.get("source_links", []) if link.get("url")}
    retained = [
        item
        for item in document.get("recommendations", [])
        if item.get("id") != recommendation["id"]
        and not urls.intersection(
            link.get("url") for link in item.get("source_links", []) if link.get("url")
        )
    ]
    retained.append(recommendation)
    retained.sort(
        key=lambda item: (
            -(item.get("match_percent") if item.get("match_percent") is not None else -1),
            item.get("title", "").casefold(),
        )
    )
    write_json_atomic(path, {"recommendations": retained})
