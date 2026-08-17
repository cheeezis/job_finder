"""Direct RhönEnergie group career-page source."""

from job_finder.http import fetch_text
from job_finder.paths import RHOENENERGIE_CACHE_FILE
from job_finder.sources.company_careers import extract_links, fetch_company_jobs

SOURCE_NAME = "rhoenenergie"
COMPANY = "RhönEnergie Fulda GmbH"
LIST_URL = "https://re-gruppe.de/karriere/"
CACHE_FILE = RHOENENERGIE_CACHE_FILE


def fetch_jobs(cache_path=CACHE_FILE, now=None):
    links = collect_links()
    return fetch_company_jobs(SOURCE_NAME, COMPANY, links, cache_path, now=now)


def collect_links():
    html = fetch_text(LIST_URL)
    return extract_links(
        html,
        LIST_URL,
        r"re-gruppe\.de/karriere/.+-de-j\d+\.html$",
    )
