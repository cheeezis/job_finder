"""get in IT source adapter.

The search page is a Next.js page with job cards in __NEXT_DATA__. Detail pages
usually have JobPosting JSON-LD; for a few pages we fall back to the Next.js
state because their JSON-LD can contain malformed escaping.
"""

import json
import re
from html import unescape
from html.parser import HTMLParser
from urllib.parse import urljoin
from urllib.request import Request, urlopen

from job_agent.config import GET_IN_IT_MAX_LINKS

SOURCE_NAME = "get_in_it"
SEARCH_URL = "https://www.get-in-it.de/jobsuche"


class TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []

    def handle_data(self, data):
        text = data.strip()
        if text:
            self.parts.append(text)

    def text(self):
        return " ".join(self.parts)


def fetch_jobs():
    links = collect_links()
    if not links:
        print("Keine get-in-IT-Links gefunden")
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
    print("Suche get in IT: Jobsuche")
    html = fetch_html(SEARCH_URL)
    links = extract_detail_links(html)[:GET_IN_IT_MAX_LINKS]
    print(f"  {len(links)} Link(s)")
    return links


def extract_detail_links(html):
    """Read job links from the search page's embedded Next.js state."""
    data = extract_next_data(html)
    jobs = data.get("props", {}).get("initialState", {}).get("jobSearchJobs", {}).get("jobs", [])
    links = []
    seen = set()

    for job in jobs:
        path = job.get("url")
        if not path:
            continue

        url = urljoin("https://www.get-in-it.de", path)
        if url in seen:
            continue

        seen.add(url)
        links.append(url)

    return links


def fetch_job(url):
    html = fetch_html(url)
    posting = extract_job_posting(html)
    description = html_to_text(posting.get("description", ""))
    location = format_location(posting.get("jobLocation"))

    return {
        "title": posting.get("title", ""),
        "company": clean_company(posting.get("hiringOrganization", {}).get("name", "")),
        "location": location,
        "remote": detect_remote(html, description, location),
        "description": description,
        "url": url,
        "external_url": posting.get("url", ""),
        "source": SOURCE_NAME,
    }


def fetch_html(url):
    request = Request(url, headers={"User-Agent": "job-agent/0.1"})
    with urlopen(request, timeout=20) as response:
        return response.read().decode("utf-8")


def extract_next_data(html):
    match = re.search(
        r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>',
        html,
        re.DOTALL,
    )
    if not match:
        raise ValueError("__NEXT_DATA__ JSON nicht gefunden")
    return json.loads(unescape(match.group(1)))


def extract_job_posting(html):
    """Prefer JSON-LD, then fall back to get-in-IT's embedded state."""
    scripts = re.findall(
        r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        html,
        re.DOTALL | re.IGNORECASE,
    )

    for script in scripts:
        try:
            data = json.loads(unescape(script.strip()))
        except json.JSONDecodeError:
            continue

        posting = find_job_posting(data)
        if posting:
            return posting

    posting = extract_job_posting_from_next_data(html)
    if posting:
        return posting

    raise ValueError("JobPosting JSON-LD nicht gefunden")


def extract_job_posting_from_next_data(html):
    """Build a JobPosting-like dict from Next.js state when JSON-LD fails."""
    job = extract_next_data(html).get("props", {}).get("initialState", {}).get("jobJob", {}).get("job")
    if not job:
        return None

    for item in job.get("metaData", []):
        if item.get("name") != "schema_org:job_posting":
            continue

        try:
            return json.loads(item.get("content", ""))
        except json.JSONDecodeError:
            return None

    return {
        "@type": "JobPosting",
        "title": job.get("header", {}).get("title", ""),
        "hiringOrganization": {
            "name": job.get("header", {}).get("companyName", ""),
        },
        "jobLocation": build_locations(job.get("header", {}).get("locations", [])),
        "description": job.get("content", ""),
        "url": f"https://www.get-in-it.de/jobsuche/p{job.get('id')}",
    }


def build_locations(locations):
    return [
        {
            "@type": "Place",
            "address": {
                "@type": "PostalAddress",
                "addressLocality": location,
                "addressCountry": "DE",
            },
        }
        for location in locations
    ]


def find_job_posting(data):
    if isinstance(data, dict):
        if data.get("@type") == "JobPosting":
            return data
        for value in data.values():
            posting = find_job_posting(value)
            if posting:
                return posting

    if isinstance(data, list):
        for item in data:
            posting = find_job_posting(item)
            if posting:
                return posting

    return None


def html_to_text(html):
    parser = TextExtractor()
    parser.feed(html)
    return parser.text()


def clean_company(company):
    return re.sub(r"\s+", " ", company).strip()


def format_location(job_location):
    locations = job_location if isinstance(job_location, list) else [job_location]
    cities = []

    for location in locations:
        if not location:
            continue

        addresses = location.get("address", [])
        if isinstance(addresses, dict):
            addresses = [addresses]

        for address in addresses:
            city = address.get("addressLocality")
            if city and city not in cities:
                cities.append(city)

    return ", ".join(cities) or "unbekannt"


def detect_remote(html, description, location):
    text = f"{html} {description} {location}".lower()
    if '"homeoffice":true' in text:
        return "homeoffice"
    if contains_any(text, ["100% remote", "fully remote"]):
        return "100%"
    if contains_any(text, ["homeoffice", "home office", "remote", "mobiles arbeiten", "hybrid"]):
        return "homeoffice"
    return "0%"


def contains_any(text, words):
    return any(word in text for word in words)
