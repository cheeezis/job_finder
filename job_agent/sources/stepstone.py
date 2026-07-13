"""StepStone source adapter.

Search pages provide detail links in HTML. Detail pages expose structured
schema.org JobPosting JSON-LD, which is more stable than scraping visible text.
"""

import re
from html import unescape
from urllib.parse import quote, urljoin, urlsplit, urlunsplit

from job_agent.config import SEARCH_LOCATIONS, STEPSTONE_SEARCH_TERMS
from job_agent.http import fetch_text
from job_agent.remote import detect_remote
from job_agent.search_plan import append_unique, iter_search_queries
from job_agent.structured_data import extract_json_ld_job_posting
from job_agent.text import html_to_text

SOURCE_NAME = "stepstone"
SEARCH_BASE_URL = "https://www.stepstone.de/jobs"


def fetch_jobs():
    """Search StepStone and return imported job details."""
    links = search_links()
    if not links:
        print("Keine StepStone-Links gefunden")
        return []

    jobs = []
    for url in links:
        try:
            jobs.append(fetch_job(url))
            print(f"OK: {url}")
        except Exception as error:
            print(f"FEHLER: {url}")
            print(f"       {error}")
    return jobs


def search_links():
    """Collect unique detail links from all configured search pages."""
    links = []
    seen = set()

    for query in iter_search_queries(STEPSTONE_SEARCH_TERMS, SEARCH_LOCATIONS):
        print(f"Suche StepStone: {query.term} / {query.location}")
        page = 1
        query_seen = set()

        while True:
            search_url = build_search_url(query.term, query.location, page)
            try:
                html = fetch_text(search_url)
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
    return f"{base_url}?page={page}"


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


def fetch_job(url):
    """Import one StepStone detail page from its structured data."""
    html = fetch_text(url)
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
