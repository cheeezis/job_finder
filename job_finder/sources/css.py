"""Direct CSS/eGECKO career-page source for Künzell."""

from job_finder.http import fetch_text
from job_finder.paths import CSS_CACHE_FILE
from job_finder.sources.company_careers import extract_links, fetch_company_jobs

SOURCE_NAME = "css"
COMPANY = "CSS AG"
LIST_URL = "https://jobs.css.de/public/jobs/?standort=1"
CACHE_FILE = CSS_CACHE_FILE


def fetch_jobs(cache_path=CACHE_FILE, now=None):
    links = collect_links()
    return fetch_company_jobs(SOURCE_NAME, COMPANY, links, cache_path, now=now)


def collect_links():
    html = fetch_text(LIST_URL)
    return extract_links(html, LIST_URL, r"jobs\.css\.de/job-.+\.html$")
