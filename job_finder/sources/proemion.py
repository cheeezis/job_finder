"""Direct Proemion Personio career-page source."""

from job_finder.http import fetch_text
from job_finder.paths import PROEMION_CACHE_FILE
from job_finder.sources.company_careers import extract_links, fetch_company_jobs

SOURCE_NAME = "proemion"
COMPANY = "Proemion GmbH"
LIST_URL = "https://proemion.jobs.personio.de/?language=de"
CACHE_FILE = PROEMION_CACHE_FILE


def fetch_jobs(cache_path=CACHE_FILE, now=None):
    links = collect_links()
    return fetch_company_jobs(SOURCE_NAME, COMPANY, links, cache_path, now=now)


def collect_links():
    html = fetch_text(LIST_URL)
    return extract_links(html, LIST_URL, r"proemion\.jobs\.personio\.de/job/\d+$")
