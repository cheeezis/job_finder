"""Direct Proemion Personio career-page source."""

from job_finder.http import fetch_text
from job_finder.paths import cache_file
from job_finder.sources.company_careers import extract_links, fetch_company_jobs

SOURCE_NAME = "proemion"
COMPANY = "Proemion GmbH"
LIST_URL = "https://proemion.jobs.personio.de/?language=de"
CACHE_FILE = cache_file("proemion")


def fetch_jobs(cache_path=CACHE_FILE, now=None):
    """Import Proemion listings through the shared company detail cache."""
    links = collect_links()
    return fetch_company_jobs(SOURCE_NAME, COMPANY, links, cache_path, now=now)


def collect_links():
    """Collect Proemion Personio detail links from its public board."""
    html = fetch_text(LIST_URL)
    return extract_links(html, LIST_URL, r"proemion\.jobs\.personio\.de/job/\d+$")
