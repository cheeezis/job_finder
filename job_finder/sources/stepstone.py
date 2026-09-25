"""StepStone source adapter.

Search pages provide detail links in HTML. Detail pages expose structured
schema.org JobPosting JSON-LD, which is more stable than scraping visible text.
"""

import re
import time
from html import unescape
from itertools import product
from urllib.error import HTTPError
from urllib.parse import quote, urlencode, urljoin, urlsplit

from job_finder.console import print_progress, progress_checkpoint
from job_finder.http import fetch_text
from job_finder.matching.config import (
    LOCAL_SEARCH_RADIUS_KM,
    STEPSTONE_SEARCH_LOCATIONS,
    STEPSTONE_SEARCH_TERMS,
)
from job_finder.models import Job
from job_finder.paths import cache_file
from job_finder.persistence.storage import read_versioned, write_versioned
from job_finder.sources.common import (
    detail_cache_job_dict,
    detail_is_fresh,
    detail_within_age,
    ensure_partial_failure,
    job_from_schema_posting,
    posting_source_id,
    record_partial_failure,
    record_total_segments,
)
from job_finder.structured_data import extract_json_ld_job_posting

SOURCE_NAME = "stepstone"
SEARCH_BASE_URL = "https://www.stepstone.de/jobs"
CACHE_FILE = cache_file("stepstone")
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
        """Fetch paced requests and raise StepStoneBlockedError for HTTP 403/429."""
        if self.has_requested:
            self.sleeper(self.delay)
        self.has_requested = True

        try:
            return fetch_text(url)
        except HTTPError as error:
            if error.code in BLOCKING_STATUS_CODES:
                raise StepStoneBlockedError(error.code, url) from error
            raise


def fetch_jobs(cache_path=CACHE_FILE, client=None, now=None):
    """Search StepStone and return imported job details."""
    cache = load_cache(cache_path)
    client = client or StepStoneHttpClient()

    try:
        links = search_links(client)
    except StepStoneBlockedError as error:
        ensure_partial_failure()
        print(f"WARNUNG StepStone: HTTP {error.status_code}; nutze letzten Cache-Stand")
        return cached_jobs(cache.get("last_links", []), cache, now)

    if not links:
        return []

    cache["last_links"] = links
    save_cache(cache_path, cache)

    jobs = []
    detail_errors = 0
    stale_fallbacks = 0
    for index, url in enumerate(links):
        cache_key = normalize_detail_url(url)
        cached_job = cache["jobs"].get(cache_key)
        if detail_is_fresh(cached_job, now):
            cached_job.cache_stale = False
            jobs.append(cached_job)
        else:
            try:
                job = fetch_job(url, client)
                job.cache_stale = False
                jobs.append(job)
                cache["jobs"][cache_key] = job
                save_cache(cache_path, cache)
            except StepStoneBlockedError as error:
                ensure_partial_failure()
                print(f"WARNUNG StepStone: HTTP {error.status_code}; keine weiteren Detailanfragen")
                if cached_job and detail_within_age(cached_job, now):
                    cached_job.cache_stale = True
                    jobs.append(cached_job)
                jobs.extend(cached_jobs(links[index + 1 :], cache, now))
                break
            except Exception:
                detail_errors += 1
                record_partial_failure()
                if cached_job and detail_within_age(cached_job, now):
                    cached_job.cache_stale = True
                    jobs.append(cached_job)
                    stale_fallbacks += 1
        if progress_checkpoint(index + 1, len(links)):
            print_progress("StepStone Details", index + 1, len(links), f"{len(jobs)} übernommen")
    if detail_errors:
        print(
            f"WARNUNG StepStone: {detail_errors} Detailseite(n) "
            f"nicht erreichbar, {stale_fallbacks} aus altem Cache übernommen"
        )
    return jobs


def search_links(client=None):
    """Collect unique detail links from all configured search pages."""
    client = client or StepStoneHttpClient()
    links = {}
    search_errors = 0
    requested_pages = 0
    planned_queries = len(STEPSTONE_SEARCH_TERMS) * len(STEPSTONE_SEARCH_LOCATIONS)
    record_total_segments(planned_queries)

    queries = product(STEPSTONE_SEARCH_TERMS, STEPSTONE_SEARCH_LOCATIONS)
    for processed_queries, (term, location) in enumerate(queries, start=1):
        page = 1
        query_seen = set()

        while True:
            search_url = build_search_url(term, location, page)
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
            query_seen.update(page_links)

            links.update(dict.fromkeys(page_links))

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
    record_partial_failure(search_errors)
    return list(links)


def build_search_url(term, location, page=1):
    """Build a paginated search URL with a radius for nonremote locations."""
    base_url = f"{SEARCH_BASE_URL}/{quote(term.replace(' ', '-'))}/in-{quote(location)}"
    query = {"page": page}
    if location.lower() != "remote":
        query["radius"] = LOCAL_SEARCH_RADIUS_KM
    return f"{base_url}?{urlencode(query)}"


def extract_detail_links(html):
    """Collect unique absolute detail URLs from absolute and relative links."""
    matches = re.findall(
        r'https://www\.stepstone\.de/stellenangebote--[^"\'<> ]+?\.html[^"\'<> ]*'
        r'|/stellenangebote--[^"\'<> ]+?\.html[^"\'<> ]*',
        html,
    )
    urls = (
        normalize_detail_url(urljoin("https://www.stepstone.de", unescape(match)))
        for match in matches
    )
    return list(dict.fromkeys(urls))


def normalize_detail_url(url):
    """Remove query and fragment from a StepStone detail URL."""
    return urlsplit(url)._replace(query="", fragment="").geturl()


def fetch_job(url, client=None):
    """Import one StepStone detail page from its structured data."""
    client = client or StepStoneHttpClient()
    html = client.get(url)
    posting = extract_json_ld_job_posting(html)
    if not posting:
        raise ValueError("JobPosting JSON-LD nicht gefunden")
    job = job_from_schema_posting(
        SOURCE_NAME,
        url,
        posting,
        identifier=posting_source_id(urlsplit(url).path, r"--(\d+)-inline\.html$", posting),
        company=clean_company(posting.get("hiringOrganization", {}).get("name", "")),
    )
    job.career_levels = extract_career_levels(html)
    return job


def load_cache(path):
    """Load cached jobs and the links from the last successful search."""
    cache = read_versioned(path, CACHE_VERSION) or {"version": CACHE_VERSION}
    cache.setdefault("last_links", [])
    cache["jobs"] = {url: Job.from_dict(values) for url, values in cache.get("jobs", {}).items()}
    return cache


def save_cache(path, cache):
    """Persist cache updates atomically so interrupted runs keep valid JSON."""
    jobs = {url: detail_cache_job_dict(job) for url, job in cache.get("jobs", {}).items()}
    write_versioned(path, CACHE_VERSION, last_links=cache.get("last_links", []), jobs=jobs)


def cached_jobs(links, cache, now=None):
    """Return cached jobs for links without making network requests."""
    jobs = []
    for url in links:
        job = cache.get("jobs", {}).get(normalize_detail_url(url))
        if job and detail_within_age(job, now):
            job.cache_stale = not detail_is_fresh(job, now)
            jobs.append(job)
    return jobs


def clean_company(company):
    """Remove StepStone's year-tagged suffix from an employer name."""
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
