"""Direct JUMO career-page source."""

import http.cookiejar
import json
import re
from html import unescape
from urllib.parse import urlencode
from urllib.request import HTTPCookieProcessor, Request, build_opener

from job_finder.paths import cache_file
from job_finder.sources.company_careers import fetch_company_jobs

SOURCE_NAME = "jumo"
COMPANY = "JUMO GmbH & Co. KG"
BASE_URL = "https://jobs.jumo.de/engage/jobexchange/"
SEARCH_URL = f"{BASE_URL}showJobOffers.do?j=jobexchange"
LIST_URL = f"{BASE_URL}showJobOfferList.do"
CACHE_FILE = cache_file("jumo")
MAX_RESULT_BATCHES = 20


def fetch_jobs(cache_path=CACHE_FILE, now=None):
    """Import JUMO search results through the shared company detail cache."""
    links = collect_links()
    return fetch_company_jobs(SOURCE_NAME, COMPANY, links, cache_path, now=now)


def collect_links():
    """Use JUMO's public search session to collect every current detail ID."""
    opener = build_opener(HTTPCookieProcessor(http.cookiejar.CookieJar()))
    page = _session_text(opener, SEARCH_URL)
    csrf_match = re.search(r'name="_csrf"[^>]*value="([^"]+)"', page)
    if not csrf_match:
        raise ValueError("JUMO-CSRF-Kennung nicht gefunden")
    csrf = unescape(csrf_match.group(1))

    _session_text(opener, f"{LIST_URL}?search=true", {"j": "jobexchange", "_csrf": csrf})
    identifiers = {}

    for _batch in range(MAX_RESULT_BATCHES):
        html = _session_text(
            opener, LIST_URL, {"showNextJobOffers": "true", "j": "jobexchange", "_csrf": csrf}
        )
        identifiers.update(dict.fromkeys(extract_job_ids(html)))

        has_next = _session_text(opener, LIST_URL, {"hasNextJobOffers": "true", "_csrf": csrf})
        if not json.loads(has_next.lower()):
            break

    return [
        f"{BASE_URL}showJobOfferDetail.do?"
        f"{urlencode({'jobOfferId': identifier, 'j': 'jobexchange', 'organizationUnitId': ''})}"
        for identifier in identifiers
    ]


def extract_job_ids(html):
    """Return unique hexadecimal offer IDs in their first-seen order."""
    return list(dict.fromkeys(re.findall(r"jobOfferId=([a-f0-9]+)", html, re.IGNORECASE)))


def _session_text(opener, url, form=None):
    """GET, or POST form fields, through the JUMO session and return UTF-8 text."""
    headers = {"User-Agent": "job-finder/0.1"}
    data = None
    if form is not None:
        headers["Content-Type"] = "application/x-www-form-urlencoded"
        data = urlencode(form).encode("utf-8")
    with opener.open(Request(url, data=data, headers=headers), timeout=20) as response:
        return response.read().decode("utf-8")
