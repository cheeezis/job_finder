"""Arbeitsagentur source adapter.

The site renders search and detail data into Angular's server-side ng-state
JSON, so we can read structured data without browser automation.
"""

import json
import re
from html import unescape
from urllib.parse import urlencode

from job_agent.config import LOCAL_SEARCH_LOCATION, LOCAL_SEARCH_RADIUS_KM, SEARCH_TERMS
from job_agent.http import fetch_text
from job_agent.remote import detect_remote

SOURCE_NAME = "arbeitsagentur"
SEARCH_BASE_URL = "https://www.arbeitsagentur.de/jobsuche/suche"
DETAIL_BASE_URL = "https://www.arbeitsagentur.de/jobsuche/jobdetail"


def fetch_jobs():
    """Search Arbeitsagentur and return imported job details."""
    links = collect_links()
    jobs = []

    for url in links:
        try:
            job = fetch_job(url)
            job["source"] = SOURCE_NAME
            jobs.append(job)
            print(f"OK: {url}")
        except Exception as error:
            print(f"FEHLER: {url}")
            print(f"       {error}")

    return jobs


def collect_links():
    """Collect unique detail URLs from all configured Arbeitsagentur searches."""
    seen = set()
    links = []

    for term in SEARCH_TERMS:
        print(f"Suche: {term}")
        results = search(term)
        print(f"  {len(results)} Treffer")

        for result in results:
            reference = result.get("referenznummer")
            if not reference:
                continue
            if "/" in reference:
                print(f"  Ueberspringe Sonder-Referenz: {reference}")
                continue

            url = f"{DETAIL_BASE_URL}/{reference}"
            if url in seen:
                continue

            seen.add(url)
            links.append(url)

    return links


def search(term):
    """Load every available result page for one search term."""
    results = []
    seen_references = set()
    page = 1

    while True:
        html = fetch_text(build_search_url(term, page))
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
        print(f"  Seite {page}: {len(new_results)} neu, {len(results)}/{total} geladen")
        if not page_results or not new_results or len(results) >= total:
            return results

        page += 1


def build_search_url(term, page=1):
    # The local radius is centered on the configured home location.
    query = {
        "angebotsart": "1",
        "was": term,
        "wo": LOCAL_SEARCH_LOCATION,
        "umkreis": str(LOCAL_SEARCH_RADIUS_KM),
        "page": str(page),
    }
    return f"{SEARCH_BASE_URL}?{urlencode(query)}"


def fetch_job(url):
    html = fetch_text(url)
    detail = extract_jobdetail(html)
    title = detail.get("stellenangebotsTitel", "")
    location = format_locations(detail)
    description = detail.get("stellenangebotsBeschreibung", "")
    structured_remote = format_remote(detail)

    return {
        "title": title,
        "company": detail.get("firma", ""),
        "location": location,
        "remote": detect_remote(title, description, location, structured_remote=structured_remote),
        "description": description,
        "url": url,
        "external_url": detail.get("externeURL", ""),
        "source": SOURCE_NAME,
    }


def extract_ng_state(html):
    # Arbeitsagentur liefert Such- und Detaildaten als Angular-SSR-State aus.
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

    return ", ".join(locations) or "unbekannt"


def format_remote(detail):
    if not detail.get("homeofficemoeglich"):
        return "0%"

    remote_type = detail.get("homeofficetyp", "")
    if remote_type == "AUSSCHLIESSLICH":
        return "100%"
    if remote_type == "NACH_VEREINBARUNG":
        return "homeoffice"
    return "homeoffice"
