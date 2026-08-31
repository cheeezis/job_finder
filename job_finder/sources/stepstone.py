"""StepStone source adapter.

Search pages provide detail links in HTML. Detail pages expose structured
schema.org JobPosting JSON-LD, which is more stable than scraping visible text.
"""

import json
import re
import time
from html import unescape
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import quote, urlencode, urljoin, urlsplit, urlunsplit

from job_finder.config import (
    STEPSTONE_SEARCH_LOCATIONS,
    STEPSTONE_SEARCH_RADIUS_KM,
    STEPSTONE_SEARCH_TERMS,
)
from job_finder.console import print_progress, progress_checkpoint
from job_finder.http import fetch_text
from job_finder.models import Job, JobSource
from job_finder.paths import STEPSTONE_CACHE_FILE
from job_finder.remote import classify_remote, detect_remote
from job_finder.search_plan import append_unique, iter_search_queries
from job_finder.sources.common import (
    detail_cache_job_dict,
    detail_is_fresh,
    detail_within_age,
    extract_annual_salary_eur,
    extract_schema_locations,
    mark_content_change,
    normalize_employment_type,
    parse_published_date,
    source_job_id,
    utc_now,
)
from job_finder.structured_data import extract_json_ld_job_posting
from job_finder.text import html_to_text

SOURCE_NAME = "stepstone"
SEARCH_BASE_URL = "https://www.stepstone.de/jobs"
CACHE_FILE = STEPSTONE_CACHE_FILE
CACHE_VERSION = 2
REQUEST_DELAY_SECONDS = 1.5
BLOCKING_STATUS_CODES = {403, 429}
CAREER_LEVEL_LABELS = {
    "Berufseinstieg/Trainee",
    "Berufserfahrene",
    "Führungskraft",
    "Studentische Aushilfe",
}


class StepStoneBlockedError(RuntimeError):
    """Signal that StepStone asked the importer to stop sending requests."""

    def __init__(self, status_code, url):
        super().__init__(f"StepStone antwortet mit HTTP {status_code}: {url}")
        self.status_code = status_code
        self.url = url


class StepStoneHttpClient:
    """Pace StepStone requests and surface access blocks immediately."""

    def __init__(self, delay=REQUEST_DELAY_SECONDS, sleeper=time.sleep):
        self.delay = delay
        self.sleeper = sleeper
        self.has_requested = False

    def get(self, url):
        if self.has_requested:
            self.sleeper(self.delay)
        self.has_requested = True

        try:
            return fetch_text(url)
        except HTTPError as error:
            if error.code in BLOCKING_STATUS_CODES:
                raise StepStoneBlockedError(error.code, url) from error
            raise


def fetch_jobs(
    cache_path=CACHE_FILE,
    client=None,
    now=None,
    _coverage=None,
):
    """Search StepStone and return imported job details."""
    cache_file = Path(cache_path)
    cache = load_cache(cache_file)
    client = client or StepStoneHttpClient()

    try:
        links = search_links(client, coverage=_coverage)
    except StepStoneBlockedError as error:
        if _coverage is not None:
            _coverage["failed_segments"] = max(
                1, _coverage.get("failed_segments", 0)
            )
        print(
            f"WARNUNG StepStone: HTTP {error.status_code}; "
            "nutze letzten Cache-Stand"
        )
        return cached_jobs(cache.get("last_links", []), cache, now)

    if not links:
        return []

    cache["last_links"] = links
    save_cache(cache_file, cache)

    jobs = []
    detail_errors = 0
    stale_fallbacks = 0
    for index, url in enumerate(links):
        cache_key = normalize_detail_url(url)
        cached_job = cache["jobs"].get(cache_key)
        if detail_is_fresh(cached_job, now):
            cached_job.content_changed = False
            cached_job.cache_stale = False
            jobs.append(cached_job)
            if progress_checkpoint(index + 1, len(links)):
                print_progress(
                    "StepStone Details",
                    index + 1,
                    len(links),
                    f"{len(jobs)} übernommen",
                )
            continue

        try:
            job = fetch_job(url, client)
            job.cache_stale = False
            mark_content_change(job, cached_job)
            jobs.append(job)
            cache["jobs"][cache_key] = job
            save_cache(cache_file, cache)
        except StepStoneBlockedError as error:
            if _coverage is not None:
                _coverage["failed_segments"] = max(
                    1, _coverage.get("failed_segments", 0)
                )
            print(
                f"WARNUNG StepStone: HTTP {error.status_code}; "
                "keine weiteren Detailanfragen"
            )
            if cached_job and detail_within_age(cached_job, now):
                cached_job.content_changed = False
                cached_job.cache_stale = True
                jobs.append(cached_job)
            jobs.extend(cached_jobs(links[index + 1 :], cache, now))
            break
        except Exception:
            detail_errors += 1
            if _coverage is not None:
                _coverage["failed_segments"] = (
                    _coverage.get("failed_segments", 0) + 1
                )
            if cached_job and detail_within_age(cached_job, now):
                cached_job.content_changed = False
                cached_job.cache_stale = True
                jobs.append(cached_job)
                stale_fallbacks += 1
        if progress_checkpoint(index + 1, len(links)):
            print_progress(
                "StepStone Details",
                index + 1,
                len(links),
                f"{len(jobs)} übernommen",
            )
    if detail_errors:
        print(
            f"WARNUNG StepStone: {detail_errors} Detailseite(n) "
            f"nicht erreichbar, {stale_fallbacks} aus altem Cache übernommen"
        )
    return jobs


def fetch_jobs_with_report(cache_path=CACHE_FILE, client=None, now=None):
    """Return jobs and coverage metadata for safe inactivity tracking."""
    coverage = {"failed_segments": 0, "total_segments": 0}
    jobs = fetch_jobs(cache_path, client, now, _coverage=coverage)
    failed = coverage["failed_segments"]
    return {
        "jobs": jobs,
        "status": "partial" if failed else ("success" if jobs else "empty"),
        "details": coverage,
    }


def search_links(client=None, *, coverage=None):
    """Collect unique detail links from all configured search pages."""
    client = client or StepStoneHttpClient()
    links = []
    seen = set()
    search_errors = 0
    processed_queries = 0
    requested_pages = 0
    planned_queries = len(STEPSTONE_SEARCH_TERMS) * len(
        STEPSTONE_SEARCH_LOCATIONS
    )
    if coverage is not None:
        coverage["total_segments"] = planned_queries

    for query in iter_search_queries(
        STEPSTONE_SEARCH_TERMS,
        STEPSTONE_SEARCH_LOCATIONS,
    ):
        processed_queries += 1
        page = 1
        query_seen = set()

        while True:
            search_url = build_search_url(query.term, query.location, page)
            try:
                html = client.get(search_url)
                requested_pages += 1
            except StepStoneBlockedError:
                raise
            except Exception:
                search_errors += 1
                break

            found_links = extract_detail_links(html)
            page_links = [url for url in found_links if url not in query_seen]
            for url in page_links:
                query_seen.add(url)

            for url in page_links:
                append_unique(url, links, seen)

            if not found_links:
                break
            if not page_links:
                break

            page += 1
        print_progress(
            "StepStone Suche",
            processed_queries,
            planned_queries,
            f"{requested_pages} Seiten · {len(links)} Anzeigen",
        )

    if search_errors:
        print(f"WARNUNG StepStone: {search_errors} Suchseite(n) nicht erreichbar")
    if coverage is not None:
        coverage["failed_segments"] += search_errors
    return links


def build_search_url(term, location, page=1):
    base_url = f"{SEARCH_BASE_URL}/{quote(term.replace(' ', '-'))}/in-{quote(location)}"
    query = {"page": page}
    if location.lower() != "remote":
        query["radius"] = STEPSTONE_SEARCH_RADIUS_KM
    return f"{base_url}?{urlencode(query)}"


def extract_detail_links(html):
    matches = re.findall(
        r'https://www\.stepstone\.de/stellenangebote--[^"\'<> ]+?\.html[^"\'<> ]*'
        r'|/stellenangebote--[^"\'<> ]+?\.html[^"\'<> ]*',
        html,
    )
    links = []
    seen = set()

    for match in matches:
        url = normalize_detail_url(urljoin("https://www.stepstone.de", unescape(match)))
        append_unique(url, links, seen)

    return links


def normalize_detail_url(url):
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def fetch_job(url, client=None):
    """Import one StepStone detail page from its structured data."""
    client = client or StepStoneHttpClient()
    html = client.get(url)
    posting = extract_json_ld_job_posting(html)
    if not posting:
        raise ValueError("JobPosting JSON-LD nicht gefunden")
    raw_description = posting.get("description", "")
    description = html_to_text(raw_description)
    locations = extract_schema_locations(posting.get("jobLocation"))
    location_text = ", ".join(locations)
    title = posting.get("title", "")
    detected_remote = detect_remote(title, description, location_text)
    work_mode, remote_percentage = classify_remote(detected_remote)
    identifier = extract_source_id(url, posting)
    salary_min_eur, salary_max_eur = extract_annual_salary_eur(posting)

    return Job(
        id=source_job_id(SOURCE_NAME, identifier, url),
        title=title,
        company=clean_company(posting.get("hiringOrganization", {}).get("name", "")),
        locations=locations,
        sources=[
            JobSource(
                source=SOURCE_NAME,
                source_id=identifier,
                url=url,
            )
        ],
        description_raw=raw_description,
        description_clean=description,
        work_mode=work_mode,
        remote_percentage=remote_percentage,
        employment_type=normalize_employment_type(posting.get("employmentType")),
        career_levels=extract_career_levels(html),
        salary_min_eur=salary_min_eur,
        salary_max_eur=salary_max_eur,
        published_at=parse_published_date(posting.get("datePosted")),
        fetched_at=utc_now(),
    )


def load_cache(path):
    """Load cached jobs and the links from the last successful search."""
    empty_cache = {"version": CACHE_VERSION, "last_links": [], "jobs": {}}
    if not path.exists():
        return empty_cache

    try:
        cache = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return empty_cache

    if cache.get("version") != CACHE_VERSION:
        return empty_cache
    cache.setdefault("last_links", [])
    cache.setdefault("jobs", {})
    cache["jobs"] = {
        url: Job.from_dict(job_values)
        for url, job_values in cache["jobs"].items()
    }
    return cache


def save_cache(path, cache):
    """Persist cache updates atomically so interrupted runs keep valid JSON."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_suffix(f"{path.suffix}.tmp")
    serialized = {
        "version": CACHE_VERSION,
        "last_links": cache.get("last_links", []),
        "jobs": {
            url: detail_cache_job_dict(job)
            for url, job in cache.get("jobs", {}).items()
        },
    }
    temporary_path.write_text(
        json.dumps(serialized, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    temporary_path.replace(path)


def cached_jobs(links, cache, now=None):
    """Return cached jobs for links without making network requests."""
    jobs = []
    for url in links:
        job = cache.get("jobs", {}).get(normalize_detail_url(url))
        if job and detail_within_age(job, now):
            job.content_changed = False
            job.cache_stale = not detail_is_fresh(job, now)
            jobs.append(job)
    return jobs


def clean_company(company):
    return re.sub(r"_20\d{2}-.+$", "", company).strip()


def extract_career_levels(html):
    """Read StepStone's explicit career-level labels from page metadata."""
    labels = []
    for value in re.findall(r'"contractType"\s*:\s*"([^"]*)"', html):
        for label in unescape(value).split(","):
            label = label.strip()
            if label in CAREER_LEVEL_LABELS and label not in labels:
                labels.append(label)
    return labels


def extract_source_id(url, posting):
    """Extract StepStone's numeric posting ID when available."""
    match = re.search(r"--(\d+)-inline\.html$", urlsplit(url).path)
    if match:
        return match.group(1)

    identifier = posting.get("identifier")
    if isinstance(identifier, dict):
        return identifier.get("value")
    return identifier
