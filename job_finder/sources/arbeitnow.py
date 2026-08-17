"""Arbeitnow source adapter using its free public job-board API."""

import time
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlencode, urlsplit

from job_finder.http import fetch_json, fetch_text_with_final_url
from job_finder.models import Job, JobSource, WorkMode
from job_finder.paths import ARBEITNOW_CACHE_FILE
from job_finder.sources.common import (
    canonical_detail_url,
    detail_is_fresh,
    load_detail_cache,
    mark_content_change,
    normalize_employment_type,
    save_detail_cache,
    source_job_id,
    utc_now,
)
from job_finder.structured_data import extract_json_ld_job_posting
from job_finder.text import html_to_text

SOURCE_NAME = "arbeitnow"
API_URL = "https://www.arbeitnow.com/api/job-board-api"
CACHE_FILE = ARBEITNOW_CACHE_FILE
MAX_PAGES = 50
REQUEST_PAUSE_SECONDS = 6
PLACEHOLDER_DESCRIPTION = "find jobs in germany on arbeitnow"
MIN_EXTERNAL_DESCRIPTION_LENGTH = 200


class _DescriptionMetaParser(HTMLParser):
    """Collect portable description metadata regardless of attribute order."""

    def __init__(self):
        super().__init__()
        self.descriptions = []

    def handle_starttag(self, tag, attrs):
        if tag.casefold() != "meta":
            return
        values = {str(name).casefold(): value for name, value in attrs}
        description_type = str(
            values.get("property") or values.get("name") or ""
        ).casefold()
        content = values.get("content")
        if description_type in {"og:description", "description"} and content:
            self.descriptions.append(content)


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
            "WARNUNG Arbeitnow: API-Limit erreicht; nutze "
            f"{len(jobs)} aktuelle Stellen aus dem lokalen Cache"
        )
        return jobs

    current_cache = {}
    for record in records:
        job = job_from_record(record)
        cache_key = canonical_detail_url(job.primary_url)
        previous = cache.get(cache_key)
        reuse_cached_enrichment(job, previous)
        mark_content_change(job, previous)
        current_cache[cache_key] = job
        jobs.append(job)

    if jobs:
        save_detail_cache(cache_file, current_cache)
    return jobs


def collect_records():
    """Fetch each API page once and discard repeated slugs."""
    records = []
    seen = set()

    for page in range(1, MAX_PAGES + 1):
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


def reuse_cached_enrichment(job, previous):
    """Keep a confirmed original link and bridge later placeholder responses."""
    if (
        previous is None
        or is_placeholder_description(previous.description_clean)
    ):
        return False

    previous_source = next(
        (item for item in previous.sources if item.source == SOURCE_NAME),
        None,
    )
    current_source = next(
        (item for item in job.sources if item.source == SOURCE_NAME),
        None,
    )
    if (
        previous_source is None
        or current_source is None
        or not previous_source.application_url
        or application_page_is_missing(previous_source.application_url)
    ):
        return False

    current_source.application_url = previous_source.application_url
    if is_placeholder_description(job.description_clean):
        job.description_raw = previous.description_raw
        job.description_clean = previous.description_clean
    return True


def enrich_candidate_jobs(jobs, candidate_ids, cache_path=CACHE_FILE):
    """Replace placeholder portal text with the original ad for eligible jobs."""
    cache_file = Path(cache_path)
    cache = load_detail_cache(cache_file)
    enriched = 0
    enrichment_errors = 0
    for job in jobs:
        if job.id not in candidate_ids or not is_placeholder_description(job.description_clean):
            continue
        source = next((item for item in job.sources if item.source == SOURCE_NAME), None)
        if source is None:
            continue
        try:
            target_url, html = fetch_text_with_final_url(f"{source.url.rstrip('/')}/apply")
            if application_page_is_missing(target_url):
                continue
            description = external_description(html)
            if len(description) < MIN_EXTERNAL_DESCRIPTION_LENGTH:
                continue
            previous = cache.get(canonical_detail_url(source.url))
            source.application_url = target_url
            job.description_raw = html
            job.description_clean = description
            mark_content_change(job, previous)
            cache[canonical_detail_url(source.url)] = job
            enriched += 1
        except Exception:
            enrichment_errors += 1
    if enriched:
        save_detail_cache(cache_file, cache)
    if enrichment_errors:
        print(
            f"WARNUNG Arbeitnow: {enrichment_errors} Originalanzeige(n) "
            "nicht erreichbar"
        )
    return enriched


def is_placeholder_description(description):
    return PLACEHOLDER_DESCRIPTION in str(description or "").lower()


def external_description(html):
    """Prefer structured job text and fall back to description metadata."""
    posting = extract_json_ld_job_posting(html)
    if posting:
        structured = html_to_text(posting.get("description"))
        if len(structured) >= MIN_EXTERNAL_DESCRIPTION_LENGTH:
            return structured

    parser = _DescriptionMetaParser()
    parser.feed(str(html or ""))
    return max(
        (html_to_text(value) for value in parser.descriptions),
        key=len,
        default="",
    )


def application_page_is_missing(url):
    """Reject redirects that explicitly identify a missing application page."""
    values = parse_qs(urlsplit(url or "").query)
    return any(
        value.casefold() in {"1", "true", "yes"}
        for value in values.get("not_found", [])
    )


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
