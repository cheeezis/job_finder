"""Targeted processing for one job URL explicitly supplied by the user."""

import json
from pathlib import Path

from job_finder.deduplication import deduplicate_jobs, merge_jobs
from job_finder.main import load_jobs, score_for_pipeline
from job_finder.memory import load_memory, save_memory, update_memory
from job_finder.paths import JOBS_FILE, MEMORY_FILE, RECOMMENDATIONS_JSON
from job_finder.reporting import recommendation_for_job
from job_finder.sources import manual
from job_finder.sources.common import canonical_detail_url


def import_manual_url(
    url,
    *,
    cache_path=manual.MANUAL_CACHE_FILE,
    jobs_path=JOBS_FILE,
    memory_path=MEMORY_FILE,
    recommendations_path=RECOMMENDATIONS_JSON,
):
    """Import, remember, and score exactly one supplied listing."""
    imported = manual.add_url(url, cache_path=cache_path)
    jobs = load_current_jobs(jobs_path)
    target = replace_or_add_job(jobs, imported)

    memory = load_memory(memory_path)
    update_memory([target], memory, successful_sources=None)
    save_memory(memory, memory_path)
    save_jobs(jobs, jobs_path)

    score = score_for_pipeline(target)
    warning = score.get("prefilter_warning")

    row = {
        **target.to_dict(),
        "is_new": target.is_new,
        "content_changed": target.content_changed,
        **score,
    }
    if warning:
        row["prefilter_warning"] = warning
    save_recommendation(row, recommendations_path)
    return {
        "job_id": row["id"],
        "title": row["title"],
        "company": row["company"],
        "match_percent": row.get("match_percent"),
        "prefilter_warning": warning,
    }


def load_current_jobs(path):
    path = Path(path)
    return load_jobs(path) if path.exists() else []


def replace_or_add_job(jobs, imported):
    """Update an existing exact URL or cross-source duplicate in-place."""
    imported_url = canonical_detail_url(imported.primary_url)
    for index, existing in enumerate(jobs):
        existing_urls = {
            canonical_detail_url(source.url) for source in existing.sources
        }
        if imported_url in existing_urls:
            jobs[index] = merge_jobs(existing, imported)
            return jobs[index]

    merged = deduplicate_jobs([*jobs, imported])
    jobs[:] = merged
    for job in jobs:
        if any(
            source.source == manual.SOURCE_NAME
            and canonical_detail_url(source.url) == imported_url
            for source in job.sources
        ):
            return job
    raise RuntimeError("Die manuell importierte Stelle konnte nicht zugeordnet werden")


def save_jobs(jobs, path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            [job.to_dict() for job in jobs],
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def save_recommendation(job, path):
    """Replace only the imported card and preserve all other review results."""
    path = Path(path)
    if path.exists():
        document = json.loads(path.read_text(encoding="utf-8"))
    else:
        document = {"recommendations": []}
    recommendation = recommendation_for_job(job)
    urls = {
        link["url"]
        for link in recommendation.get("source_links", [])
        if link.get("url")
    }
    retained = [
        item
        for item in document.get("recommendations", [])
        if item.get("id") != recommendation["id"]
        and not urls.intersection(
            link.get("url")
            for link in item.get("source_links", [])
            if link.get("url")
        )
    ]
    retained.append(recommendation)
    retained.sort(
        key=lambda item: (
            -(item.get("match_percent") if item.get("match_percent") is not None else -1),
            item.get("title", "").casefold(),
        )
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {"recommendations": retained},
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
