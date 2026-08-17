"""Jobicy source adapter using its free public remote-jobs API."""

import time
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


SOURCE_NAME = "jobicy"
API_URL = "https://jobicy.com/api/v2/remote-jobs"
SEARCH_SCOPES = (
    {"geo": "germany"},
    {"industry": "engineering"},
    {"industry": "cybersecurity"},
    {"industry": "data-science"},
    {"industry": "qa-testing"},
    {"industry": "technical-support"},
)
RESULT_LIMIT = 100
REQUEST_PAUSE_SECONDS = 0.25


def fetch_jobs():
    """Return recent Germany-focused and international remote IT jobs."""
    return [
        job_from_record(record)
        for record in collect_records()
        if remote_region_allows_germany(record.get("jobGeo"))
    ]


def collect_records(scopes=SEARCH_SCOPES):
    """Fetch a bounded set of official feeds and remove their overlaps."""
    records = []
    seen = set()

    for index, scope in enumerate(scopes):
        if index:
            time.sleep(REQUEST_PAUSE_SECONDS)
        payload = fetch_json(build_search_url(scope))
        for record in payload.get("jobs") or []:
            identifier = record_identifier(record)
            if not identifier or identifier in seen:
                continue
            seen.add(identifier)
            records.append(record)

    return records


def build_search_url(scope):
    """Build one documented Jobicy API query."""
    return f"{API_URL}?{urlencode({**scope, 'count': RESULT_LIMIT})}"


def record_identifier(record):
    """Return the stable public identifier exposed by Jobicy."""
    return str(record.get("id") or record.get("url") or "").strip()


def job_from_record(record):
    """Convert one Jobicy API record into the shared Job model."""
    url = str(record.get("url") or "").strip()
    title = str(record.get("jobTitle") or "").strip()
    company = str(record.get("companyName") or "").strip()
    if not url or not title or not company:
        raise ValueError("Jobicy-Eintrag ohne URL, Titel oder Unternehmen")

    raw_description = str(record.get("jobDescription") or "")
    salary_minimum, salary_maximum = annual_salary_eur(record)

    return Job(
        id=source_job_id(SOURCE_NAME, record.get("id"), url),
        title=title,
        company=company,
        locations=location_names(record.get("jobGeo")),
        sources=[
            JobSource(
                source=SOURCE_NAME,
                source_id=record_identifier(record),
                url=url,
            )
        ],
        description_raw=raw_description,
        description_clean=html_to_text(raw_description),
        work_mode=WorkMode.REMOTE,
        remote_percentage=100,
        employment_type=normalize_employment_type(record.get("jobType")),
        career_levels=text_values(record.get("jobLevel")),
        salary_min_eur=salary_minimum,
        salary_max_eur=salary_maximum,
        published_at=parse_published_date(record.get("pubDate")),
        fetched_at=utc_now(),
    )


def location_names(value):
    """Return the advertised remote region in a filter-friendly form."""
    text = str(value or "").strip()
    if not text or text.casefold() == "anywhere":
        return ["weltweit"]
    return [text]


def text_values(value):
    """Split Jobicy's comma-separated seniority labels."""
    if isinstance(value, list):
        values = value
    else:
        values = str(value or "").split(",")
    return [str(item).strip() for item in values if str(item or "").strip()]


def annual_salary_eur(record):
    """Return only annual salary values already expressed in euros."""
    if str(record.get("salaryCurrency") or "").upper() != "EUR":
        return None, None
    period = str(record.get("salaryPeriod") or "").casefold()
    if period not in {"annual", "year", "yearly"}:
        return None, None
    return numeric_value(record.get("salaryMin")), numeric_value(record.get("salaryMax"))


def numeric_value(value):
    """Return a whole-number salary when possible."""
    try:
        return int(float(value)) if value is not None else None
    except (TypeError, ValueError):
        return None
