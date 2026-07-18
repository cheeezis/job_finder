"""Direct NETHINKS career-page source."""

import re

from job_agent.http import fetch_text
from job_agent.paths import NETHINKS_CACHE_FILE
from job_agent.sources.company_careers import extract_links, fetch_company_jobs

SOURCE_NAME = "nethinks"
COMPANY = "NETHINKS GmbH"
LIST_URL = "https://nethinks.com/nethinks_jobs/"
CACHE_FILE = NETHINKS_CACHE_FILE


def fetch_jobs(cache_path=CACHE_FILE, now=None):
    links = collect_links()
    return fetch_company_jobs(SOURCE_NAME, COMPANY, links, cache_path, now=now)


def collect_links():
    first_html = fetch_text(LIST_URL)
    pages = [int(value) for value in re.findall(r"/nethinks_jobs/page/(\d+)/", first_html)]
    last_page = max(pages, default=1)
    links = extract_job_links(first_html)
    seen = set(links)

    for page in range(2, last_page + 1):
        html = fetch_text(f"{LIST_URL}page/{page}/")
        for url in extract_job_links(html):
            if url not in seen:
                seen.add(url)
                links.append(url)
    return links


def extract_job_links(html):
    return extract_links(
        html,
        LIST_URL,
        r"nethinks\.com/nethinks_jobs/(?!page/|feed/?$)[^/]+/$",
    )
