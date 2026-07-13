"""get in IT source adapter.

Search uses get-in-IT's public JSON API. Detail pages usually have JobPosting
JSON-LD; for a few pages we fall back to the Next.js state because their JSON-LD
can contain malformed escaping.
"""

import json
import re
from html import unescape
from urllib.parse import urlencode, urljoin

from job_agent.config import GET_IN_IT_SEARCH_LOCATIONS, GET_IN_IT_SEARCH_TERMS
from job_agent.http import fetch_json, fetch_text
from job_agent.remote import detect_remote
from job_agent.search_plan import append_unique, iter_search_queries, unique_in_order
from job_agent.text import html_to_text

SOURCE_NAME = "get_in_it"
API_SEARCH_URL = "https://www.get-in-it.de/api/v2/open/job/search"
API_PAGE_SIZE = 39
HESSEN_STATE_ID = 5

THEMATIC_PRIORITIES = {
    36: "Anwendungsentwicklung",
    38: "Business Analysis",
    39: "Datenbankentwicklung/BI",
    44: "Forschung",
    35: "System Engineering / Admin",
    5: "Webentwicklung",
}

TERM_PRIORITY_RULES = [
    (["data", "analytics", "analyst", "bi"], [38, 39]),
    (["devops", "infrastructure", "system"], [35]),
    (["ai", "ki", "machine learning", "ml"], [36, 39, 44]),
    (["backend", "fullstack", "web"], [36, 5]),
    (["python", "developer", "softwareentwickler", "software"], [36, 5]),
]


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
    links = []
    seen = set()

    for search in build_api_searches():
        print(f"Suche get in IT: {search['label']} / {search['location']}")
        try:
            results = search_api(search["priority_id"], search["location"])
        except Exception as error:
            print(f"  FEHLER Suche: {error}")
            continue

        found_links = extract_detail_links_from_api(results)
        print(f"  {len(found_links)} Link(s)")
        for url in found_links:
            append_unique(url, links, seen)

    return links


def build_api_searches():
    """Map our shared search terms to get-in-IT's available category filters."""
    seen = set()

    for query in iter_search_queries(GET_IN_IT_SEARCH_TERMS, GET_IN_IT_SEARCH_LOCATIONS):
        for priority_id in priority_ids_for_term(query.term):
            key = (priority_id, query.location)
            if key in seen:
                continue

            seen.add(key)
            yield {
                "priority_id": priority_id,
                "location": query.location,
                "label": THEMATIC_PRIORITIES.get(priority_id, f"Thema {priority_id}"),
            }


def priority_ids_for_term(term):
    normalized = term.lower()
    priority_ids = []

    for keywords, ids in TERM_PRIORITY_RULES:
        if any(keyword in normalized for keyword in keywords):
            priority_ids.extend(ids)

    return unique_in_order(priority_ids)


def search_api(priority_id, location):
    results = []
    seen_ids = set()
    start = 0

    while True:
        params = {
            "start": start,
            "limit": API_PAGE_SIZE,
            "filter[thematic_priority]": priority_id,
        }

        if location.lower() == "remote":
            params["filter[homeOffice]"] = 1
        elif location.lower() == "fulda":
            # get-in-IT exposes state filters reliably; final Fulda/remote filtering
            # still happens in scoring, where exact locations and remote text exist.
            params["filter[state]"] = HESSEN_STATE_ID

        url = f"{API_SEARCH_URL}?{urlencode(params)}"
        data = fetch_json(
            url,
            headers={
                "Accept": "application/json",
                "X-Requested-With": "XMLHttpRequest",
            },
        )
        page_results = data.get("items", {}).get("results", [])
        new_results = [job for job in page_results if job.get("id") not in seen_ids]

        for job in new_results:
            seen_ids.add(job.get("id"))
            results.append(job)

        total = int(data.get("total", 0) or 0)
        print(f"  API {len(results)}/{total} Treffer geladen")
        if not page_results or not new_results or len(results) >= total:
            return results

        start += len(page_results)


def extract_detail_links_from_api(data):
    links = []

    for job in data:
        path = job.get("url")
        if path:
            links.append(urljoin("https://www.get-in-it.de", path))

    return links


def fetch_job(url):
    html = fetch_text(url)
    posting = extract_job_posting(html)
    description = html_to_text(posting.get("description", ""))
    location = format_location(posting.get("jobLocation"))
    title = posting.get("title", "")

    return {
        "title": title,
        "company": clean_company(posting.get("hiringOrganization", {}).get("name", "")),
        "location": location,
        "remote": detect_remote(title, html, description, location),
        "description": description,
        "url": url,
        "external_url": posting.get("url", ""),
        "source": SOURCE_NAME,
    }


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
