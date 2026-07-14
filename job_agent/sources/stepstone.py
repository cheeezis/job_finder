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

from job_agent.config import (
    STEPSTONE_SEARCH_LOCATIONS,
    STEPSTONE_SEARCH_RADIUS_KM,
    STEPSTONE_SEARCH_TERMS,
)
from job_agent.http import fetch_text
from job_agent.remote import detect_remote
from job_agent.search_plan import append_unique, iter_search_queries
from job_agent.structured_data import extract_json_ld_job_posting
from job_agent.text import html_to_text

SOURCE_NAME = "stepstone"
SEARCH_BASE_URL = "https://www.stepstone.de/jobs"
CACHE_FILE = "data/stepstone_cache.json"
IMPORTED_JOBS_FILE = "data/jobs_imported.json"
CACHE_VERSION = 1
REQUEST_DELAY_SECONDS = 1.5
BLOCKING_STATUS_CODES = {403, 429}


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
    imported_jobs_path=IMPORTED_JOBS_FILE,
    client=None,
):
    """Search StepStone and return imported job details."""
    cache_file = Path(cache_path)
    cache = load_cache(cache_file)
    imported_count = seed_cache_from_imported_jobs(
        cache,
        Path(imported_jobs_path),
    )
    if imported_count:
        save_cache(cache_file, cache)
        print(f"StepStone-Cache mit {imported_count} vorhandenem Job(s) gestartet")
    client = client or StepStoneHttpClient()

    try:
        links = search_links(client)
    except StepStoneBlockedError as error:
        print(f"ABBRUCH: {error}")
        print("StepStone wird spaeter erneut versucht; nutze letzten Cache-Stand")
        return cached_jobs(cache.get("last_links", []), cache)

    if not links:
        print("Keine StepStone-Links gefunden")
        return []

    cache["last_links"] = links
    save_cache(cache_file, cache)

    jobs = []
    for index, url in enumerate(links):
        cache_key = normalize_detail_url(url)
        cached_job = cache["jobs"].get(cache_key)
        if cached_job:
            jobs.append(cached_job)
            print(f"CACHE: {url}")
            continue

        try:
            job = fetch_job(url, client)
            jobs.append(job)
            cache["jobs"][cache_key] = job
            save_cache(cache_file, cache)
            print(f"OK: {url}")
        except StepStoneBlockedError as error:
            print(f"ABBRUCH: {error}")
            print("Keine weiteren StepStone-Requests in diesem Lauf")
            jobs.extend(cached_jobs(links[index + 1 :], cache))
            break
        except Exception as error:
            print(f"FEHLER: {url}")
            print(f"       {error}")
    return jobs


def search_links(client=None):
    """Collect unique detail links from all configured search pages."""
    client = client or StepStoneHttpClient()
    links = []
    seen = set()

    for query in iter_search_queries(
        STEPSTONE_SEARCH_TERMS,
        STEPSTONE_SEARCH_LOCATIONS,
    ):
        print(f"Suche StepStone: {query.term} / {query.location}")
        page = 1
        query_seen = set()

        while True:
            search_url = build_search_url(query.term, query.location, page)
            try:
                html = client.get(search_url)
            except StepStoneBlockedError:
                raise
            except Exception as error:
                print(f"  FEHLER Seite {page}: {error}")
                break

            found_links = extract_detail_links(html)
            page_links = [url for url in found_links if url not in query_seen]
            for url in page_links:
                query_seen.add(url)

            globally_new = 0
            for url in page_links:
                if append_unique(url, links, seen):
                    globally_new += 1

            print(
                f"  Seite {page}: {len(found_links)} Link(s), "
                f"{globally_new} quellenweit neu"
            )
            if not found_links or not page_links:
                break

            page += 1

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
    description = html_to_text(posting.get("description", ""))
    location = format_location(posting.get("jobLocation"))
    title = posting.get("title", "")

    return {
        "title": title,
        "company": clean_company(posting.get("hiringOrganization", {}).get("name", "")),
        "location": location,
        "remote": detect_remote(title, description, location),
        "description": description,
        "url": posting.get("url") or url,
        "external_url": url,
        "source": SOURCE_NAME,
    }


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
    return cache


def save_cache(path, cache):
    """Persist cache updates atomically so interrupted runs keep valid JSON."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_suffix(f"{path.suffix}.tmp")
    temporary_path.write_text(
        json.dumps(cache, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    temporary_path.replace(path)


def seed_cache_from_imported_jobs(cache, path):
    """Use StepStone jobs from an earlier full run as the initial cache."""
    if not path.exists():
        return 0

    try:
        imported_jobs = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return 0

    added = 0
    for job in imported_jobs:
        if job.get("source") != SOURCE_NAME:
            continue
        detail_url = job.get("external_url") or job.get("url")
        if not detail_url:
            continue
        cache_key = normalize_detail_url(detail_url)
        if cache_key in cache["jobs"]:
            continue
        cache["jobs"][cache_key] = job
        added += 1
    return added


def cached_jobs(links, cache):
    """Return cached jobs for links without making network requests."""
    jobs = []
    for url in links:
        job = cache.get("jobs", {}).get(normalize_detail_url(url))
        if job:
            jobs.append(job)
    return jobs


def clean_company(company):
    return re.sub(r"_20\d{2}-.+$", "", company).strip()


def format_location(job_location):
    locations = job_location if isinstance(job_location, list) else [job_location]
    cities = []

    for location in locations:
        if not location:
            continue
        address = location.get("address", {})
        city = address.get("addressLocality")
        if city and city not in cities:
            cities.append(city)

    return ", ".join(cities) or "unbekannt"
