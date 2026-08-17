"""Startup Jobs source adapter using its official read-only API."""

import os
from urllib.parse import urlencode

from job_finder.http import fetch_json
from job_finder.models import Job, JobSource, WorkMode
from job_finder.sources.common import (
    normalize_employment_type,
    parse_published_date,
    remote_region_allows_germany,
    source_job_id,
    utc_now,
)
from job_finder.text import html_to_text


SOURCE_NAME = "startup_jobs"
API_URL = "https://api.startup.jobs/v1/jobs"
API_KEY_ENV = "STARTUP_JOBS_API_KEY"
SEARCH_SCOPES = (
    {"role": "engineering", "country": "DE"},
    {"role": "engineering", "workplace_type": "remote"},
)
PAGE_SIZE = 50
MAX_PAGES_PER_SCOPE = 5


def is_configured(environ=None):
    """Return whether the optional API credential is available."""
    environment = environ if environ is not None else os.environ
    return bool(str(environment.get(API_KEY_ENV) or "").strip())


def fetch_jobs(api_key=None):
    """Return recent engineering jobs in Germany or remotely worldwide."""
    key = str(api_key or os.getenv(API_KEY_ENV) or "").strip()
    if not key:
        raise ValueError(f"{API_KEY_ENV} fehlt")
    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {key}",
    }
    return [
        job_from_record(record)
        for record in collect_records(headers)
        if available_from_germany(record)
    ]


def collect_records(headers, scopes=SEARCH_SCOPES):
    """Fetch bounded cursor pages and merge overlaps between both scopes."""
    records = []
    seen = set()

    for scope in scopes:
        cursor = None
        for _page in range(MAX_PAGES_PER_SCOPE):
            payload = fetch_json(build_search_url(scope, cursor), headers=headers)
            for record in payload.get("data") or []:
                identifier = str(record.get("id") or record.get("url") or "").strip()
                if not identifier or identifier in seen:
                    continue
                seen.add(identifier)
                records.append(record)

            cursor = payload.get("next_cursor")
            if not payload.get("has_more") or cursor is None:
                break

    return records


def build_search_url(scope, cursor=None):
    """Build one recent-jobs query from a documented search scope."""
    parameters = {**scope, "limit": PAGE_SIZE}
    if cursor is not None:
        parameters["starting_after"] = cursor
    return f"{API_URL}?{urlencode(parameters)}"


def available_from_germany(record):
    """Reject explicit foreign-only locations from the global remote scope."""
    location = record.get("location") or {}
    return remote_region_allows_germany(
        location.get("country"),
        country_code=location.get("country_code"),
    )


def job_from_record(record):
    """Convert one Startup Jobs API record into the shared Job model."""
    url = str(record.get("url") or "").strip()
    title = str(record.get("title") or "").strip()
    company = str((record.get("company") or {}).get("name") or "").strip()
    if not url or not title or not company:
        raise ValueError("Startup-Jobs-Eintrag ohne URL, Titel oder Unternehmen")

    raw_description = str(record.get("description_html") or "")
    work_mode, remote_percentage = work_arrangement(record.get("workplace_type"))
    salary_minimum, salary_maximum = annual_salary_eur(record.get("salary_data"))

    return Job(
        id=source_job_id(SOURCE_NAME, record.get("id"), url),
        title=title,
        company=company,
        locations=location_names(record.get("location")),
        sources=[
            JobSource(
                source=SOURCE_NAME,
                source_id=str(record.get("id") or "") or None,
                url=url,
            )
        ],
        description_raw=raw_description,
        description_clean=html_to_text(raw_description),
        work_mode=work_mode,
        remote_percentage=remote_percentage,
        employment_type=normalize_employment_type(record.get("employment_type")),
        salary_min_eur=salary_minimum,
        salary_max_eur=salary_maximum,
        published_at=parse_published_date(record.get("published_at")),
        fetched_at=utc_now(),
    )


def location_names(location):
    """Return the most specific advertised location without duplicates."""
    values = []
    for field in ("city", "state", "country"):
        text = str((location or {}).get(field) or "").strip()
        if text and text not in values:
            values.append(text)
    return [", ".join(values)] if values else ["Remote"]


def work_arrangement(value):
    """Map the API workplace enum to the shared remote model."""
    normalized = str(value or "").casefold()
    if normalized == "remote":
        return WorkMode.REMOTE, 100
    if normalized == "hybrid":
        return WorkMode.HYBRID, None
    if normalized == "on-site":
        return WorkMode.ONSITE, 0
    return WorkMode.UNKNOWN, None


def annual_salary_eur(salary):
    """Return only annual structured salary values in euros."""
    if not isinstance(salary, dict):
        return None, None
    if str(salary.get("currency") or "").upper() != "EUR":
        return None, None
    if str(salary.get("interval") or "").casefold() != "year":
        return None, None
    return numeric_value(salary.get("min")), numeric_value(salary.get("max"))


def numeric_value(value):
    """Return a whole-number salary when possible."""
    try:
        return int(float(value)) if value is not None else None
    except (TypeError, ValueError):
        return None
