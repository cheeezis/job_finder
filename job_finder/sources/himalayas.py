"""Himalayas source adapter using its free public remote-jobs API."""

import time
from datetime import datetime, timezone
from urllib.parse import urlencode

from job_finder.http import fetch_json
from job_finder.models import Job, JobSource, WorkMode
from job_finder.sources.common import (
    normalize_employment_type,
    parse_published_date,
    source_job_id,
    utc_now,
)
from job_finder.text import html_to_text


SOURCE_NAME = "himalayas"
API_URL = "https://himalayas.app/jobs/api/search"
SEARCH_TERMS = (
    "software",
    "developer",
    "information technology",
    "data",
    "cloud",
    "devops",
    "security",
    "quality assurance",
    "technical support",
    "system administrator",
    "IT consultant",
    "business analyst",
)
MAX_PAGES_PER_SEARCH = 5
REQUEST_PAUSE_SECONDS = 0.25


def fetch_jobs():
    """Return current entry-level remote jobs available from Germany."""
    return [job_from_record(record) for record in collect_records()]


def collect_records(search_terms=SEARCH_TERMS):
    """Fetch bounded filtered searches and remove cross-query duplicates."""
    records = []
    seen = set()
    first_request = True

    for search_term in search_terms:
        for page in range(1, MAX_PAGES_PER_SEARCH + 1):
            if not first_request:
                time.sleep(REQUEST_PAUSE_SECONDS)
            first_request = False
            payload = fetch_json(build_search_url(search_term, page))
            page_records = payload.get("jobs") or []
            for record in page_records:
                identifier = record_identifier(record)
                if not identifier or identifier in seen:
                    continue
                seen.add(identifier)
                records.append(record)

            if page_is_complete(payload, page_records):
                break

    return records


def build_search_url(search_term, page=1):
    """Build one Germany-compatible, entry-level search request."""
    parameters = {
        "q": search_term,
        "country": "DE",
        "seniority": "Entry-level",
        "sort": "recent",
        "page": page,
    }
    return f"{API_URL}?{urlencode(parameters)}"


def page_is_complete(payload, page_records):
    """Return whether the current search has no further result page."""
    if not page_records:
        return True
    offset = integer(payload.get("offset"), 0)
    limit = integer(payload.get("limit"), len(page_records))
    total = integer(payload.get("totalCount"), len(page_records))
    return offset + limit >= total


def record_identifier(record):
    """Return the stable public identifier exposed by Himalayas."""
    return str(record.get("guid") or record.get("applicationLink") or "").strip()


def job_from_record(record):
    """Convert one Himalayas API record into the shared Job model."""
    url = str(record.get("applicationLink") or record.get("guid") or "").strip()
    title = str(record.get("title") or "").strip()
    company = str(record.get("companyName") or "").strip()
    if not url or not title or not company:
        raise ValueError("Himalayas-Eintrag ohne URL, Titel oder Unternehmen")

    raw_description = str(record.get("description") or "")
    locations = location_names(record.get("locationRestrictions"))
    salary_minimum, salary_maximum = annual_salary_eur(record)

    return Job(
        id=source_job_id(SOURCE_NAME, None, url),
        title=title,
        company=company,
        locations=locations,
        sources=[JobSource(source=SOURCE_NAME, source_id=record_identifier(record), url=url)],
        description_raw=raw_description,
        description_clean=html_to_text(raw_description),
        work_mode=WorkMode.REMOTE,
        remote_percentage=100,
        employment_type=normalize_employment_type(record.get("employmentType")),
        career_levels=text_values(record.get("seniority")),
        salary_min_eur=salary_minimum,
        salary_max_eur=salary_maximum,
        published_at=parse_api_date(record.get("pubDate")),
        fetched_at=utc_now(),
    )


def location_names(restrictions):
    """Return readable eligible countries or a worldwide marker."""
    names = []
    for restriction in restrictions or []:
        if isinstance(restriction, dict):
            value = restriction.get("name") or restriction.get("alpha2")
        else:
            value = restriction
        text = str(value or "").strip()
        if text and text not in names:
            names.append(text)
    return names or ["weltweit"]


def annual_salary_eur(record):
    """Return only annual salary values already expressed in euros."""
    if str(record.get("currency") or "").upper() != "EUR":
        return None, None
    if str(record.get("salaryPeriod") or "annual").casefold() != "annual":
        return None, None
    return numeric_value(record.get("minSalary")), numeric_value(record.get("maxSalary"))


def parse_api_date(value):
    """Parse Unix seconds/milliseconds or an ISO date into a calendar date."""
    try:
        timestamp = float(value)
    except (TypeError, ValueError):
        return parse_published_date(value)
    if timestamp > 10_000_000_000:
        timestamp /= 1000
    try:
        return datetime.fromtimestamp(timestamp, tz=timezone.utc).date()
    except (OSError, OverflowError, ValueError):
        return None


def text_values(value):
    """Normalize a scalar or list into non-empty strings."""
    values = value if isinstance(value, list) else [value]
    return [str(item).strip() for item in values if str(item or "").strip()]


def numeric_value(value):
    """Return a whole-number salary when possible."""
    try:
        return int(float(value)) if value is not None else None
    except (TypeError, ValueError):
        return None


def integer(value, default):
    """Return an integer pagination value with a safe fallback."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
