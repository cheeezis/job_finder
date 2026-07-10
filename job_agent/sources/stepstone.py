"""StepStone source adapter.

Search pages provide detail links in HTML. Detail pages expose structured
schema.org JobPosting JSON-LD, which is more stable than scraping visible text.
"""

import json
import re
from html import unescape
from urllib.parse import quote, urljoin, urlsplit, urlunsplit

from job_agent.config import SEARCH_LOCATIONS, STEPSTONE_MAX_LINKS_PER_SEARCH
from job_agent.config import STEPSTONE_MAX_TOTAL_LINKS, STEPSTONE_SEARCH_TERMS
from job_agent.http import fetch_text
from job_agent.remote import detect_remote
from job_agent.search_plan import append_unique, iter_search_queries
from job_agent.text import html_to_text

SOURCE_NAME = "stepstone"
SEARCH_BASE_URL = "https://www.stepstone.de/jobs"


def fetch_jobs():
    links = collect_links()
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


def collect_links():
    links = []
    seen = set()

    for url in search_links():
        append_unique(url, links, seen)

    return links


def search_links():
    links = []
    seen = set()

    for query in iter_search_queries(STEPSTONE_SEARCH_TERMS, SEARCH_LOCATIONS):
        search_url = build_search_url(query.term, query.location)
        print(f"Suche StepStone: {query.term} / {query.location}")
        try:
            html = fetch_text(search_url)
        except Exception as error:
            print(f"  FEHLER Suche: {error}")
            continue

        found_links = extract_detail_links(html)[:STEPSTONE_MAX_LINKS_PER_SEARCH]
        print(f"  {len(found_links)} Link(s)")
        for url in found_links:
            append_unique(url, links, seen)
            if len(links) >= STEPSTONE_MAX_TOTAL_LINKS:
                return links

    return links


def build_search_url(term, location):
    return f"{SEARCH_BASE_URL}/{quote(term.replace(' ', '-'))}/in-{quote(location)}"


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
    html = fetch_text(url)
    posting = extract_job_posting(html)
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


def extract_job_posting(html):
    """Find the JobPosting JSON-LD block on a StepStone detail page."""
    scripts = re.findall(
        r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        html,
        re.DOTALL | re.IGNORECASE,
    )

    for script in scripts:
        data = json.loads(unescape(script.strip()))
        postings = data if isinstance(data, list) else [data]
        for item in postings:
            if item.get("@type") == "JobPosting":
                return item

    raise ValueError("JobPosting JSON-LD nicht gefunden")


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
