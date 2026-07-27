"""Arbeitnow source adapter using its free public job-board API."""

import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode

from job_agent.http import fetch_json
from job_agent.models import Job, JobSource, WorkMode
from job_agent.paths import ARBEITNOW_CACHE_FILE
from job_agent.sources.common import (
    canonical_detail_url,
    detail_is_fresh,
    load_detail_cache,
    mark_content_change,
    normalize_employment_type,
    save_detail_cache,
    source_job_id,
    utc_now,
)
from job_agent.text import html_to_text

SOURCE_NAME = "arbeitnow"
API_URL = "https://www.arbeitnow.com/api/job-board-api"
CACHE_FILE = ARBEITNOW_CACHE_FILE
MAX_PAGES = 50
REQUEST_PAUSE_SECONDS = 6


def fetch_jobs(cache_path=CACHE_FILE):
    """Load all currently exposed jobs and track meaningful changes."""
    cache_file = Path(cache_path)
    cache = load_detail_cache(cache_file)
    jobs = []

    try:
        records = collect_records()
    except HTTPError as error:
        if error.code != 429:
            raise
        jobs = fresh_cached_jobs(cache)
        if not jobs:
            raise
        print(
            "Arbeitnow begrenzt Anfragen; nutze "
            f"{len(jobs)} aktuelle Stellen aus dem lokalen Cache"
        )
        return jobs

    for record in records:
        job = job_from_record(record)
        cache_key = canonical_detail_url(job.primary_url)
        previous = cache.get(cache_key)
        mark_content_change(job, previous)
        cache[cache_key] = job
        jobs.append(job)

    if jobs:
        save_detail_cache(cache_file, cache)
    return jobs


def collect_records():
    """Fetch each API page once and discard repeated slugs."""
    records = []
    seen = set()

    for page in range(1, MAX_PAGES + 1):
        print(f"Arbeitnow Seite {page}")
        payload = fetch_json(
            f"{API_URL}?{urlencode({'page': page})}",
            headers={"Accept": "application/json"},
        )
        page_records = payload.get("data") or []
        for record in page_records:
            slug = str(record.get("slug") or "").strip()
            if not slug or slug in seen:
                continue
            seen.add(slug)
            records.append(record)

        if not page_records or not (payload.get("links") or {}).get("next"):
            break
        time.sleep(REQUEST_PAUSE_SECONDS)

    return records


def fresh_cached_jobs(cache):
    """Reuse only recently fetched listings after an API rate limit response."""
    return [job for job in cache.values() if detail_is_fresh(job)]


def job_from_record(record):
    """Convert one API record into the shared Job model."""
    slug = str(record.get("slug") or "").strip()
    url = str(record.get("url") or "").strip()
    if not slug or not url:
        raise ValueError("Arbeitnow-Eintrag ohne slug oder URL")

    raw_description = str(record.get("description") or "")
    remote = bool(record.get("remote"))
    location = str(record.get("location") or "").strip() or "unbekannt"

    return Job(
        id=source_job_id(SOURCE_NAME, slug, url),
        title=str(record.get("title") or "").strip(),
        company=str(record.get("company_name") or "").strip(),
        locations=[location],
        sources=[JobSource(source=SOURCE_NAME, source_id=slug, url=url)],
        description_raw=raw_description,
        description_clean=html_to_text(raw_description),
        work_mode=WorkMode.REMOTE if remote else WorkMode.UNKNOWN,
        remote_percentage=100 if remote else None,
        employment_type=normalize_employment_type(record.get("job_types")),
        published_at=parse_created_at(record.get("created_at")),
        fetched_at=utc_now(),
    )


def parse_created_at(value):
    """Parse Arbeitnow's Unix timestamp into a UTC calendar date."""
    try:
        return datetime.fromtimestamp(float(value), tz=timezone.utc).date()
    except (TypeError, ValueError, OSError):
        return None
