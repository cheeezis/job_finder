"""Shared detail importing for selected company career pages."""

import re
from html import unescape
from urllib.parse import urljoin, urlsplit

from job_finder.http import fetch_text
from job_finder.paths import cache_file
from job_finder.sources.common import (
    canonical_detail_url,
    fetch_cached_details,
    job_from_schema_posting,
    source_job_id,
)
from job_finder.structured_data import extract_json_ld_job_posting


class CareerPage:
    """A company career page whose detail links all match one URL pattern.

    The upper-case attributes mirror an adapter module, so run_finder treats a
    registry entry like any other source.
    """

    def __init__(self, source_name, company, list_url, link_pattern):
        self.SOURCE_NAME = source_name
        self.CACHE_FILE = cache_file(source_name)
        self.company = company
        self.list_url = list_url
        self.link_pattern = link_pattern

    def fetch_jobs(self, cache_path=None, now=None):
        """Import the listings through the shared company detail cache."""
        links = self.collect_links()
        cache_path = self.CACHE_FILE if cache_path is None else cache_path
        return fetch_company_jobs(self.SOURCE_NAME, self.company, links, cache_path, now=now)

    def collect_links(self):
        """Extract the detail links from the public career page."""
        return extract_links(fetch_text(self.list_url), self.list_url, self.link_pattern)


class PaginatedCareerPage(CareerPage):
    """A career page that spreads its openings over numbered .../page/N/ pages."""

    def collect_links(self):
        """Collect unique detail links across all advertised pages."""
        first_html = fetch_text(self.list_url)
        page_pattern = re.escape(urlsplit(self.list_url).path) + r"page/(\d+)/"
        last_page = max((int(value) for value in re.findall(page_pattern, first_html)), default=1)
        links = dict.fromkeys(extract_links(first_html, self.list_url, self.link_pattern))
        for page in range(2, last_page + 1):
            html = fetch_text(f"{self.list_url}page/{page}/")
            links.update(dict.fromkeys(extract_links(html, self.list_url, self.link_pattern)))
        return list(links)


def fetch_company_jobs(source_name, company, links, cache_path, now=None, parser=None):
    """Import company details with the shared weekly cache and stale fallback."""
    parser = parser or job_from_json_ld

    def fetch_detail(url):
        job = parser(source_name, company, url, fetch_text(url))
        ensure_url_identity(job, source_name, url)
        return job

    return fetch_cached_details(
        links,
        cache_path,
        fetch_detail,
        source_name,
        now=now,
        normalize_cached=lambda job, url: ensure_url_identity(job, source_name, url),
    )


def job_from_json_ld(source_name, fallback_company, url, html):
    """Create a shared Job from a company's schema.org JobPosting."""
    posting = extract_json_ld_job_posting(html)
    if not posting:
        raise ValueError("JobPosting JSON-LD nicht gefunden")
    return job_from_posting(source_name, fallback_company, url, posting)


def job_from_posting(source_name, fallback_company, url, posting):
    """Convert an already extracted schema.org JobPosting to a shared Job.

    Company pages may escape plain descriptions and titles and name no employer.
    """
    description = posting.get("description", "")
    if not re.search(r"<[a-z][^>]*>", description, re.IGNORECASE):
        description = unescape(description)
    title = unescape(str(posting.get("title") or "")).strip()
    organization = posting.get("hiringOrganization") or {}
    company = organization.get("name", "") if isinstance(organization, dict) else ""
    telecommute = str(posting.get("jobLocationType") or "").upper() == "TELECOMMUTE"
    return job_from_schema_posting(
        source_name,
        url,
        {**posting, "description": description, "title": title},
        identifier=identifier_from_url(url),
        company=unescape(str(company or fallback_company)).strip(),
        structured_remote="100%" if telecommute else "",
    )


def extract_links(html, base_url, pattern):
    """Return canonical links whose absolute URLs match a regex, once each."""
    hrefs = re.findall(r'href=["\']([^"\']+)', html, re.IGNORECASE)
    urls = (canonical_detail_url(urljoin(base_url, unescape(href))) for href in hrefs)
    return list(dict.fromkeys(url for url in urls if re.search(pattern, url, re.IGNORECASE)))


def ensure_url_identity(job, source_name, url):
    """Use the unique career-page URL as the source ID.

    Some career sites publish one generic schema.org identifier for every
    opening.  Using it would merge unrelated postings in memory and caches.
    """
    identifier = identifier_from_url(url)
    job_id = source_job_id(source_name, identifier, url)
    changed = job.id != job_id
    job.id = job_id
    for source in job.sources:
        if source.source == source_name:
            changed = changed or source.source_id != identifier or source.url != url
            source.source_id = identifier
            source.url = url
            break
    return changed


def identifier_from_url(url):
    """Prefer a numeric or hexadecimal ID at the end of a career URL."""
    match = re.search(
        r"(?:jobOfferId=|/job/|[-/])([a-f0-9]{8,}|\d{3,})(?:\D*$|$)", url, re.IGNORECASE
    )
    return match.group(1) if match else urlsplit(url).path.rstrip("/").rsplit("/", 1)[-1]


CSS = CareerPage(
    "css", "CSS AG", "https://jobs.css.de/public/jobs/?standort=1", r"jobs\.css\.de/job-.+\.html$"
)
PROEMION = CareerPage(
    "proemion",
    "Proemion GmbH",
    "https://proemion.jobs.personio.de/?language=de",
    r"proemion\.jobs\.personio\.de/job/\d+$",
)
BYTEWERK = CareerPage(
    "bytewerk",
    "bytewerk GmbH",
    "https://bytewerk-gmbh.jobs.personio.de/?language=de",
    r"bytewerk-gmbh\.jobs\.personio\.de/job/\d+$",
)
RHOENENERGIE = CareerPage(
    "rhoenenergie",
    "RhönEnergie Fulda GmbH",
    "https://re-gruppe.de/karriere/",
    r"re-gruppe\.de/karriere/.+-de-j\d+\.html$",
)
NETHINKS = PaginatedCareerPage(
    "nethinks",
    "NETHINKS GmbH",
    "https://nethinks.com/nethinks_jobs/",
    r"nethinks\.com/nethinks_jobs/(?!page/|feed/?$)[^/]+/$",
)
