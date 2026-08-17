"""Direct JUMO career-page source."""

import http.cookiejar
import json
import re
from html import unescape
from urllib.parse import urlencode
from urllib.request import HTTPCookieProcessor, Request, build_opener

from job_finder.paths import JUMO_CACHE_FILE
from job_finder.sources.company_careers import fetch_company_jobs

SOURCE_NAME = "jumo"
COMPANY = "JUMO GmbH & Co. KG"
BASE_URL = "https://jobs.jumo.de/engage/jobexchange/"
SEARCH_URL = f"{BASE_URL}showJobOffers.do?j=jobexchange"
LIST_URL = f"{BASE_URL}showJobOfferList.do"
CACHE_FILE = JUMO_CACHE_FILE
MAX_RESULT_BATCHES = 20


def fetch_jobs(cache_path=CACHE_FILE, now=None):
    links = collect_links()
    return fetch_company_jobs(SOURCE_NAME, COMPANY, links, cache_path, now=now)


def collect_links():
    """Use JUMO's public search session to collect every current detail ID."""
    opener = build_opener(HTTPCookieProcessor(http.cookiejar.CookieJar()))
    page = open_text(opener, SEARCH_URL)
    csrf_match = re.search(r'name="_csrf"[^>]*value="([^"]+)"', page)
    if not csrf_match:
        raise ValueError("JUMO-CSRF-Kennung nicht gefunden")
    csrf = unescape(csrf_match.group(1))

    post_text(
        opener,
        f"{LIST_URL}?search=true",
        {"j": "jobexchange", "_csrf": csrf},
    )
    identifiers = []
    seen = set()

    for _batch in range(MAX_RESULT_BATCHES):
        html = post_text(
            opener,
            LIST_URL,
            {"showNextJobOffers": "true", "j": "jobexchange", "_csrf": csrf},
        )
        for identifier in extract_job_ids(html):
            if identifier not in seen:
                seen.add(identifier)
                identifiers.append(identifier)

        has_next = post_text(
            opener,
            LIST_URL,
            {"hasNextJobOffers": "true", "_csrf": csrf},
        )
        if not json.loads(has_next.lower()):
            break

    return [
        f"{BASE_URL}showJobOfferDetail.do?"
        f"{urlencode({'jobOfferId': identifier, 'j': 'jobexchange', 'organizationUnitId': ''})}"
        for identifier in identifiers
    ]


def extract_job_ids(html):
    return list(dict.fromkeys(re.findall(r"jobOfferId=([a-f0-9]+)", html, re.IGNORECASE)))


def open_text(opener, url):
    request = Request(url, headers={"User-Agent": "job-finder/0.1"})
    with opener.open(request, timeout=20) as response:
        return response.read().decode("utf-8")


def post_text(opener, url, values):
    request = Request(
        url,
        data=urlencode(values).encode("utf-8"),
        headers={
            "User-Agent": "job-finder/0.1",
            "Content-Type": "application/x-www-form-urlencoded",
        },
    )
    with opener.open(request, timeout=20) as response:
        return response.read().decode("utf-8")
