"""get in IT source adapter.

Search uses get-in-IT's public JSON API. Detail pages usually have JobPosting
JSON-LD; for a few pages we fall back to the Next.js state because their JSON-LD
can contain malformed escaping.
"""

import json
import re
from html import unescape
from urllib.parse import urlencode, urljoin

from job_finder.config import (
    COMMUTER_SEARCH_LOCATIONS,
    COMMUTER_SEARCH_TERMS,
    GET_IN_IT_SEARCH_LOCATIONS,
    GET_IN_IT_SEARCH_TERMS,
)
from job_finder.http import fetch_json, fetch_text
from job_finder.models import Job, JobSource
from job_finder.paths import GET_IN_IT_CACHE_FILE
from job_finder.remote import classify_remote, detect_remote
from job_finder.search_plan import append_unique, iter_search_queries, unique_in_order
from job_finder.sources.common import (
    extract_annual_salary_eur,
    extract_schema_locations,
    fetch_cached_details,
    normalize_employment_type,
    parse_published_date,
    source_job_id,
    utc_now,
)
from job_finder.structured_data import extract_json_ld_job_posting
from job_finder.text import html_to_text

SOURCE_NAME = "get_in_it"
API_SEARCH_URL = "https://www.get-in-it.de/api/v2/open/job/search"
API_PAGE_SIZE = 39
HESSEN_STATE_ID = 5
CACHE_FILE = GET_IN_IT_CACHE_FILE

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
    (["devops", "infrastructure", "system", "cloud", "security", "network"], [35]),
    (["consultant", "berater"], [36, 38]),
    (["ai", "ki", "machine learning", "ml"], [36, 39, 44]),
    (["backend", "fullstack", "web"], [36, 5]),
    (["python", "developer", "softwareentwickler", "software"], [36, 5]),
]


def fetch_jobs(cache_path=CACHE_FILE, now=None):
    """Search get in IT and return imported job details."""
    links = collect_links()
    if not links:
        return []
    return fetch_cached_details(
        links,
        cache_path,
        fetch_job,
        "get-in-IT",
        now=now,
    )


def fetch_jobs_with_report(cache_path=CACHE_FILE, now=None):
    """Return jobs plus coverage so partial searches never age out old jobs."""
    links, failed, total = collect_links(return_report=True)
    jobs = fetch_cached_details(
        links, cache_path, fetch_job, "get-in-IT", now=now
    ) if links else []
    return {
        "jobs": jobs,
        "status": "partial" if failed else ("success" if jobs else "empty"),
        "details": {"failed_segments": failed, "total_segments": total},
    }


def collect_links(*, return_report=False):
    """Collect unique detail links from all generated API searches."""
    links = []
    seen = set()
    search_errors = 0

    searches = list(build_api_searches())
    for search in searches:
        try:
            results = search_api(search["priority_id"], search["location"])
        except Exception:
            search_errors += 1
            continue

        found_links = extract_detail_links_from_api(results)
        for url in found_links:
            append_unique(url, links, seen)

    if search_errors:
        print(f"WARNUNG get-in-IT: {search_errors} Suche(n) fehlgeschlagen")
    return (links, search_errors, len(searches)) if return_report else links


def build_api_searches():
    """Map our shared search terms to get-in-IT's available category filters."""
    seen = set()

    search_plans = [
        (GET_IN_IT_SEARCH_TERMS, GET_IN_IT_SEARCH_LOCATIONS),
        (COMMUTER_SEARCH_TERMS, COMMUTER_SEARCH_LOCATIONS),
    ]
    for terms, locations in search_plans:
        for query in iter_search_queries(terms, locations):
            for priority_id in priority_ids_for_term(query.term):
                key = (priority_id, query.location)
                if key in seen:
                    continue

                seen.add(key)
                yield {
                    "priority_id": priority_id,
                    "location": query.location,
                    "label": THEMATIC_PRIORITIES.get(
                        priority_id,
                        f"Thema {priority_id}",
                    ),
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
        else:
            # get-in-IT only exposes a reliable state filter. The exact local
            # radius is enforced later from each job's actual location.
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
        if not page_results or not new_results or len(results) >= total:
            return results

        start += len(page_results)


def extract_detail_links_from_api(results):
    links = []

    for job in results:
        path = job.get("url")
        if path:
            links.append(urljoin("https://www.get-in-it.de", path))

    return links


def fetch_job(url):
    """Import one get-in-IT detail page from its embedded job data."""
    html = fetch_text(url)
    posting = extract_job_posting(html)
    raw_description = posting.get("description", "")
    description = html_to_text(raw_description)
    locations = extract_schema_locations(posting.get("jobLocation"))
    location_text = ", ".join(locations)
    title = posting.get("title", "")
    detected_remote = detect_remote(
        title,
        description,
        location_text,
        structured_remote=format_schema_remote(posting),
    )
    work_mode, remote_percentage = classify_remote(detected_remote)
    identifier = extract_source_id(url, posting)
    salary_min_eur, salary_max_eur = extract_annual_salary_eur(posting)

    return Job(
        id=source_job_id(SOURCE_NAME, identifier, url),
        title=title,
        company=clean_company(posting.get("hiringOrganization", {}).get("name", "")),
        locations=locations,
        sources=[
            JobSource(
                source=SOURCE_NAME,
                source_id=identifier,
                url=url,
            )
        ],
        description_raw=raw_description,
        description_clean=description,
        work_mode=work_mode,
        remote_percentage=remote_percentage,
        employment_type=normalize_employment_type(posting.get("employmentType")),
        career_levels=extract_career_levels(description),
        salary_min_eur=salary_min_eur,
        salary_max_eur=salary_max_eur,
        published_at=parse_published_date(posting.get("datePosted")),
        fetched_at=utc_now(),
    )


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
    posting = extract_json_ld_job_posting(html)
    if posting:
        return posting

    posting = extract_job_posting_from_next_data(html)
    if posting:
        return posting

    raise ValueError("JobPosting JSON-LD nicht gefunden")


def extract_job_posting_from_next_data(html):
    """Build a JobPosting-like dict from Next.js state when JSON-LD fails."""
    next_data = extract_next_data(html)
    job = (
        next_data.get("props", {})
        .get("initialState", {})
        .get("jobJob", {})
        .get("job")
    )
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

def clean_company(company):
    return re.sub(r"\s+", " ", company).strip()


def extract_source_id(url, posting):
    """Extract get-in-IT's numeric posting ID when available."""
    match = re.search(r"/p(\d+)", url)
    if match:
        return match.group(1)

    identifier = posting.get("identifier")
    if isinstance(identifier, dict):
        return identifier.get("value")
    return identifier


def format_schema_remote(posting):
    """Return a conservative remote hint from schema.org JobPosting data."""
    location_type = str(posting.get("jobLocationType", "")).lower()
    if "telecommute" in location_type or "remote" in location_type:
        return "homeoffice"
    return ""


def extract_career_levels(description):
    """Read the explicitly labelled get-in-IT career level from job facts."""
    match = re.search(
        r"Karrierestufe:\s*(.+?)(?=\s*(?:Beschaeftigungsgrad|"
        r"Beschäftigungsgrad|Dauer der Beschaeftigung|Dauer der Beschäftigung|"
        r"Verguetung|Vergütung|Arbeitsverhaeltnis|Arbeitsverhältnis):|$)",
        description,
        re.IGNORECASE,
    )
    if not match:
        return []
    return [
        value.strip()
        for value in match.group(1).split(";")
        if value.strip()
    ]
