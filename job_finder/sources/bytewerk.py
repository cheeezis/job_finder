"""Direct bytewerk Personio career-page source."""

from job_finder.http import fetch_text
from job_finder.paths import BYTEWERK_CACHE_FILE
from job_finder.sources.company_careers import extract_links, fetch_company_jobs

SOURCE_NAME = "bytewerk"
COMPANY = "bytewerk GmbH"
LIST_URL = "https://bytewerk-gmbh.jobs.personio.de/?language=de"
CACHE_FILE = BYTEWERK_CACHE_FILE


def fetch_jobs(cache_path=CACHE_FILE, now=None):
    """Import bytewerk listings through the shared company detail cache."""
    links = collect_links()
    return fetch_company_jobs(SOURCE_NAME, COMPANY, links, cache_path, now=now)


def collect_links():
    """Collect bytewerk Personio detail links from its public board."""
    html = fetch_text(LIST_URL)
    return extract_links(html, LIST_URL, r"bytewerk-gmbh\.jobs\.personio\.de/job/\d+$")
