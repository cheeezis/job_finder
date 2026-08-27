"""StudySmarter source adapter using its public read-only jobs API."""

import time
from dataclasses import replace
from pathlib import Path
from urllib.parse import urlencode

from job_finder.config import (
    LOCAL_SEARCH_RADIUS_KM,
    STUDYSMARTER_LOCAL_SEARCH_LOCATION,
)
from job_finder.http import fetch_json, fetch_text
from job_finder.models import Job, JobSource, WorkMode
from job_finder.paths import STUDYSMARTER_CACHE_FILE
from job_finder.sources.common import (
    DETAIL_CACHE_SAVE_INTERVAL,
    canonical_detail_url,
    detail_is_fresh,
    load_detail_cache,
    mark_content_change,
    normalize_employment_type,
    parse_published_date,
    save_detail_cache,
    source_job_id,
)
from job_finder.sources.company_careers import job_from_json_ld


SOURCE_NAME = "studysmarter"
API_URL = "https://talents.studysmarter.de/wp-json/studysmarter/v1/jobs/"
CACHE_FILE = STUDYSMARTER_CACHE_FILE
IT_CATEGORIES = (
    "software-entwicklung",
    "it-beratung",
    "data-science",
    "it-sicherheit",
    "telekommunikation-netzwerktechnik",
    "sap",
)
REMOTE_ENTRY_TERMS = ("Junior", "Graduate", "Berufseinsteiger", "Einstieg")
MAX_PAGES_PER_SEARCH = 20
REQUEST_PAUSE_SECONDS = 0.2


def fetch_jobs(cache_path=CACHE_FILE, now=None):
    """Return cached details or lightweight records for the first prefilter."""
    records = collect_records()
    cache = load_detail_cache(Path(cache_path))
    jobs = []

    for record in records:
        url = canonical_detail_url(record.get("link", ""))
        if not url:
            continue
        summary = summary_job_from_record(record)
        cached_job = cache.get(url)
        if cached_job:
            jobs.append(with_current_summary(cached_job, summary))
        else:
            jobs.append(summary)
    return jobs


def with_current_summary(cached_job, summary):
    """Keep cached detail text but refresh fields exposed by the search API."""
    current = replace(
        cached_job,
        id=summary.id,
        title=summary.title or cached_job.title,
        company=summary.company or cached_job.company,
        locations=(
            summary.locations
            if summary.locations != ["unbekannt"]
            else cached_job.locations
        ),
        sources=summary.sources,
        work_mode=(
            summary.work_mode
            if summary.work_mode is not WorkMode.UNKNOWN
            else cached_job.work_mode
        ),
        remote_percentage=(
            summary.remote_percentage
            if summary.work_mode is not WorkMode.UNKNOWN
            else cached_job.remote_percentage
        ),
        employment_type=summary.employment_type or cached_job.employment_type,
        published_at=summary.published_at or cached_job.published_at,
    )
    return mark_content_change(current, cached_job)


def enrich_candidate_jobs(jobs, candidate_ids, cache_path=CACHE_FILE, now=None):
    """Load details only for prefiltered candidates without a fresh cache."""
    cache_file = Path(cache_path)
    cache = load_detail_cache(cache_file)
    enriched = 0
    unsaved = 0
    errors = 0

    for index, job in enumerate(jobs):
        if (
            job.id not in candidate_ids
            or not job.primary_source
            or job.primary_source.source != SOURCE_NAME
        ):
            continue
        url = canonical_detail_url(job.primary_url)
        cached_job = cache.get(url)
        if detail_is_fresh(cached_job, now):
            continue
        try:
            detailed = enrich_summary_job(job, fetch_text(url))
            mark_content_change(detailed, cached_job)
            detailed.first_seen_at = job.first_seen_at
            detailed.last_seen_at = job.last_seen_at
            detailed.workflow_status = job.workflow_status
            detailed.is_new = job.is_new
            jobs[index] = detailed
            cache[url] = detailed
            enriched += 1
            unsaved += 1
            if unsaved >= DETAIL_CACHE_SAVE_INTERVAL:
                save_detail_cache(cache_file, cache)
                unsaved = 0
        except Exception:
            errors += 1

    if unsaved:
        save_detail_cache(cache_file, cache)
    if errors:
        print(f"WARNUNG StudySmarter: {errors} Kandidat(en) nicht erreichbar")
    return enriched


def collect_records(searches=None):
    """Collect bounded local and remote searches without duplicate listings."""
    records = []
    seen = set()
    search_errors = 0
    first_request = True

    for parameters in searches or build_searches():
        try:
            for page in range(1, MAX_PAGES_PER_SEARCH + 1):
                if not first_request:
                    time.sleep(REQUEST_PAUSE_SECONDS)
                first_request = False
                payload = fetch_json(build_search_url(parameters, page))
                page_records = payload.get("data") or []
                for record in page_records:
                    identifier = record_identifier(record)
                    if not identifier or identifier in seen:
                        continue
                    seen.add(identifier)
                    records.append(record)
                if page >= integer(payload.get("total_pages"), 0):
                    break
        except Exception:
            search_errors += 1

    if search_errors:
        print(f"WARNUNG StudySmarter: {search_errors} Suche(n) fehlgeschlagen")
    return records


def build_searches():
    """Build one local-radius search and focused Germany-wide remote searches."""
    categories = ",".join(IT_CATEGORIES)
    yield {
        "city": STUDYSMARTER_LOCAL_SEARCH_LOCATION,
        "radius": LOCAL_SEARCH_RADIUS_KM,
        "job_listing_category": categories,
    }
    for term in REMOTE_ENTRY_TERMS:
        yield {
            "keyword": term,
            "is_remote_position": "completely",
            "job_listing_category": categories,
        }


def build_search_url(parameters, page=1):
    """Build one public StudySmarter API URL."""
    return f"{API_URL}?{urlencode({**parameters, 'page': page})}"


def record_identifier(record):
    """Return the stable numeric identifier exposed by StudySmarter."""
    return str(record.get("id") or record.get("link") or "").strip()


def job_from_record(record, html):
    """Combine API metadata with a detail page's structured job data."""
    summary = summary_job_from_record(record)
    return enrich_summary_job(summary, html)


def summary_job_from_record(record):
    """Create a lightweight Job from one API search record."""
    url = canonical_detail_url(record.get("link", ""))
    identifier = record_identifier(record)
    remote = str(record.get("is_remote_positions") or "").casefold()
    if remote == "completely":
        work_mode, remote_percentage = WorkMode.REMOTE, 100
    elif remote == "partly":
        work_mode, remote_percentage = WorkMode.HYBRID, None
    elif remote == "no":
        work_mode, remote_percentage = WorkMode.ONSITE, 0
    else:
        work_mode, remote_percentage = WorkMode.UNKNOWN, None

    job_types = [
        item.get("name")
        for item in record.get("job_types") or []
        if isinstance(item, dict) and item.get("name")
    ]
    return Job(
        id=source_job_id(SOURCE_NAME, identifier, url),
        title=str(record.get("title") or "").strip(),
        company=str(record.get("company_name") or "").strip(),
        locations=[
            str(location).strip()
            for location in record.get("locations") or []
            if str(location).strip()
        ] or ["unbekannt"],
        sources=[JobSource(source=SOURCE_NAME, source_id=identifier, url=url)],
        description_raw="",
        description_clean="",
        work_mode=work_mode,
        remote_percentage=remote_percentage,
        employment_type=normalize_employment_type(job_types),
        published_at=parse_published_date(record.get("posted")),
    )


def enrich_summary_job(summary, html):
    """Replace one lightweight job with structured detail-page content."""
    job = job_from_json_ld(
        SOURCE_NAME,
        summary.company,
        summary.primary_url,
        html,
    )
    job.id = summary.id
    job.sources[0].source_id = summary.primary_source.source_id
    if summary.work_mode is WorkMode.REMOTE:
        job.work_mode = WorkMode.REMOTE
        job.remote_percentage = 100
    elif summary.work_mode is WorkMode.HYBRID and job.work_mode in {
        WorkMode.ONSITE,
        WorkMode.UNKNOWN,
    }:
        job.work_mode = WorkMode.HYBRID
    # StudySmarter marks some salary values as AI predictions in its API.
    # Without reliable provenance on the detail page, keep no salary value.
    job.salary_min_eur = None
    job.salary_max_eur = None
    return job


def integer(value, default):
    """Return an integer pagination value with a safe fallback."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
