"""Arbeitsagentur source adapter.

The site renders search and detail data into Angular's server-side ng-state
JSON, so we can read structured data without browser automation.
"""

import json
import re
from html import unescape
from pathlib import Path
from urllib.parse import urlencode

from job_finder.config import (
    COMMUTER_SEARCH_LOCATIONS,
    COMMUTER_SEARCH_RADIUS_KM,
    COMMUTER_SEARCH_TERMS,
    LOCAL_SEARCH_LOCATION,
    LOCAL_SEARCH_RADIUS_KM,
    SEARCH_TERMS,
)
from job_finder.http import fetch_text
from job_finder.models import Job, JobSource
from job_finder.paths import ARBEITSAGENTUR_CACHE_FILE
from job_finder.remote import classify_remote, detect_remote
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
    utc_now,
)
from job_finder.text import html_to_text

SOURCE_NAME = "arbeitsagentur"
SEARCH_BASE_URL = "https://www.arbeitsagentur.de/jobsuche/suche"
DETAIL_BASE_URL = "https://www.arbeitsagentur.de/jobsuche/jobdetail"
CACHE_FILE = ARBEITSAGENTUR_CACHE_FILE


def fetch_jobs(cache_path=CACHE_FILE, now=None):
    """Search Arbeitsagentur and return imported job details."""
    links = collect_links()
    cache_file = Path(cache_path)
    cache = load_detail_cache(cache_file)
    jobs = []
    unsaved_details = 0
    detail_errors = 0
    stale_fallbacks = 0

    for url in links:
        cache_key = canonical_detail_url(url)
        cached_job = cache.get(cache_key)
        if detail_is_fresh(cached_job, now):
            cached_job.content_changed = False
            jobs.append(cached_job)
            continue

        try:
            job = fetch_job(url)
            mark_content_change(job, cached_job)
            jobs.append(job)
            cache[cache_key] = job
            unsaved_details += 1
            if unsaved_details >= DETAIL_CACHE_SAVE_INTERVAL:
                save_detail_cache(cache_file, cache)
                unsaved_details = 0
        except Exception:
            detail_errors += 1
            if cached_job:
                cached_job.content_changed = False
                jobs.append(cached_job)
                stale_fallbacks += 1

    if unsaved_details:
        save_detail_cache(cache_file, cache)
    if detail_errors:
        print(
            f"WARNUNG Arbeitsagentur: {detail_errors} Detailseite(n) "
            f"nicht erreichbar, {stale_fallbacks} aus altem Cache übernommen"
        )
    return jobs


def collect_links():
    """Collect unique detail URLs from all configured Arbeitsagentur searches."""
    seen = set()
    links = []

    searches = [
        (term, LOCAL_SEARCH_LOCATION, LOCAL_SEARCH_RADIUS_KM)
        for term in SEARCH_TERMS
    ]
    searches.extend(
        (term, location, COMMUTER_SEARCH_RADIUS_KM)
        for location in COMMUTER_SEARCH_LOCATIONS
        for term in COMMUTER_SEARCH_TERMS
    )

    for term, location, radius in searches:
        results = search(term, location=location, radius=radius)

        for result in results:
            reference = result.get("referenznummer")
            if not reference:
                continue
            if "/" in reference:
                continue

            url = f"{DETAIL_BASE_URL}/{reference}"
            if url in seen:
                continue

            seen.add(url)
            links.append(url)

    return links


def search(term, location=LOCAL_SEARCH_LOCATION, radius=LOCAL_SEARCH_RADIUS_KM):
    """Load every available result page for one search term."""
    results = []
    seen_references = set()
    page = 1

    while True:
        html = fetch_text(build_search_url(term, page, location, radius))
        search_result = extract_ng_state(html).get("suchergebnis", {})
        page_results = (
            search_result.get("ergebnisliste")
            or search_result.get("stellenangebote")
            or []
        )
        new_results = [
            result
            for result in page_results
            if result.get("referenznummer") not in seen_references
        ]

        for result in new_results:
            seen_references.add(result.get("referenznummer"))
            results.append(result)

        total = int(search_result.get("maxErgebnisse", 0) or 0)
        if not page_results or not new_results or len(results) >= total:
            return results

        page += 1


def build_search_url(
    term,
    page=1,
    location=LOCAL_SEARCH_LOCATION,
    radius=LOCAL_SEARCH_RADIUS_KM,
):
    query = {
        "angebotsart": "1",
        "was": term,
        "wo": location,
        "umkreis": str(radius),
        "page": str(page),
    }
    return f"{SEARCH_BASE_URL}?{urlencode(query)}"


def fetch_job(url):
    """Import one Arbeitsagentur detail page from its Angular state."""
    html = fetch_text(url)
    detail = extract_jobdetail(html)
    title = detail.get("stellenangebotsTitel", "")
    locations = format_locations(detail)
    location_text = ", ".join(locations)
    raw_description = detail.get("stellenangebotsBeschreibung", "")
    description = html_to_text(raw_description)
    structured_remote = format_remote(detail)
    detected_remote = detect_remote(
        title,
        description,
        location_text,
        structured_remote=structured_remote,
    )
    work_mode, remote_percentage = classify_remote(detected_remote)
    reference = url.rstrip("/").rsplit("/", 1)[-1]

    return Job(
        id=source_job_id(SOURCE_NAME, reference, url),
        title=title,
        company=detail.get("firma", ""),
        locations=locations,
        sources=[
            JobSource(
                source=SOURCE_NAME,
                source_id=reference,
                url=url,
                application_url=detail.get("externeURL") or None,
            )
        ],
        description_raw=raw_description,
        description_clean=description,
        work_mode=work_mode,
        remote_percentage=remote_percentage,
        employment_type=format_employment_type(detail),
        published_at=parse_published_date(
            detail.get("datumErsteVeroeffentlichung"),
            (detail.get("veroeffentlichungszeitraum") or {}).get("von"),
            detail.get("aktuelleVeroeffentlichungsdatum"),
            detail.get("veroeffentlichungsdatum"),
        ),
        fetched_at=utc_now(),
    )


def extract_ng_state(html):
    """Extract Arbeitsagentur's Angular server-side rendering state."""
    match = re.search(
        r'<script id="ng-state" type="application/json">(.*?)</script>',
        html,
        re.DOTALL,
    )
    if not match:
        raise ValueError("ng-state JSON nicht gefunden")
    return json.loads(unescape(match.group(1)))


def extract_jobdetail(html):
    detail = extract_ng_state(html).get("jobdetail")
    if not detail:
        raise ValueError("jobdetail im ng-state JSON nicht gefunden")
    return detail


def format_locations(detail):
    locations = []
    for location in detail.get("stellenlokationen", []):
        address = location.get("adresse", {})
        city = address.get("ort")
        if city and city not in locations:
            locations.append(city)

    return locations or ["unbekannt"]


def format_remote(detail):
    if not detail.get("homeofficemoeglich"):
        return "0%"

    remote_type = detail.get("homeofficetyp", "")
    if remote_type == "AUSSCHLIESSLICH":
        return "100%"
    return "homeoffice"


def format_employment_type(detail):
    """Return Arbeitsagentur's employment flags as compact model values."""
    employment_types = []
    if detail.get("arbeitszeitVollzeit"):
        employment_types.append("FULL_TIME")
    if any(
        detail.get(field)
        for field in [
            "arbeitszeitTeilzeitAbend",
            "arbeitszeitTeilzeitNachmittag",
            "arbeitszeitTeilzeitVormittag",
            "arbeitszeitTeilzeitFlexibel",
        ]
    ):
        employment_types.append("PART_TIME")
    return normalize_employment_type(employment_types)
