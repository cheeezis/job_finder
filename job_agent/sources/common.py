"""Shared normalization helpers for source adapters."""

import hashlib
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from job_agent.models import Job


DETAIL_CACHE_VERSION = 1
DETAIL_REFRESH_AGE = timedelta(days=7)
DETAIL_CACHE_SAVE_INTERVAL = 25
RELEVANT_CONTENT_FIELDS = (
    "title",
    "company",
    "locations",
    "description_clean",
    "work_mode",
    "remote_percentage",
    "employment_type",
    "career_levels",
    "salary_min_eur",
    "salary_max_eur",
)
DETAIL_CACHE_FIELDS = (
    "id",
    "title",
    "company",
    "locations",
    "sources",
    "description_raw",
    "description_clean",
    "work_mode",
    "remote_percentage",
    "employment_type",
    "career_levels",
    "salary_min_eur",
    "salary_max_eur",
    "published_at",
    "fetched_at",
)


def utc_now():
    """Return a timezone-aware timestamp for source fetches."""
    return datetime.now(timezone.utc)


def canonical_detail_url(url):
    """Remove tracking parameters while preserving a source's stable job ID."""
    parts = urlsplit(url or "")
    stable_parameters = [
        (name, value)
        for name, value in parse_qsl(parts.query, keep_blank_values=True)
        if name == "jobOfferId"
    ]
    return urlunsplit(
        (
            parts.scheme,
            parts.netloc,
            parts.path,
            urlencode(stable_parameters),
            "",
        )
    )


def load_detail_cache(path):
    """Load one source's current URL-to-job detail cache."""
    cache_path = Path(path)
    if not cache_path.exists():
        return {}
    try:
        document = json.loads(cache_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    if document.get("version") != DETAIL_CACHE_VERSION:
        return {}
    return {
        url: Job.from_dict(values)
        for url, values in document.get("jobs", {}).items()
    }


def save_detail_cache(path, jobs):
    """Persist one source's detail cache via an atomic replacement."""
    cache_path = Path(path)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = cache_path.with_suffix(f"{cache_path.suffix}.tmp")
    temporary_path.write_text(
        json.dumps(
            {
                "version": DETAIL_CACHE_VERSION,
                "jobs": {
                    url: detail_cache_job_dict(job)
                    for url, job in jobs.items()
                },
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    temporary_path.replace(cache_path)


def detail_cache_job_dict(job):
    """Serialize only source data needed to reuse one detail page."""
    values = job.to_dict()
    return {field: values[field] for field in DETAIL_CACHE_FIELDS}


def detail_is_fresh(job, now=None):
    """Return whether a cached detail page is younger than seven days."""
    if job is None or job.fetched_at is None:
        return False
    current_time = now or utc_now()
    fetched_at = job.fetched_at
    if fetched_at.tzinfo is None:
        fetched_at = fetched_at.replace(tzinfo=timezone.utc)
    return current_time - fetched_at < DETAIL_REFRESH_AGE


def mark_content_change(job, previous_job):
    """Mark a refreshed job when fields relevant to filtering or review changed."""
    job.content_changed = previous_job is not None and any(
        getattr(job, field) != getattr(previous_job, field)
        for field in RELEVANT_CONTENT_FIELDS
    )
    return job


def source_job_id(source, external_id, url):
    """Build a stable source-scoped ID, falling back to the listing URL."""
    identifier = str(external_id or "").strip()
    if not identifier:
        identifier = hashlib.sha256(url.encode("utf-8")).hexdigest()[:20]
    return f"{source}:{identifier}"


def parse_published_date(*values):
    """Return the first recognized ISO or German calendar date."""
    for value in values:
        if not value:
            continue
        text = str(value).strip()
        for candidate in [text, text[:10]]:
            try:
                return date.fromisoformat(candidate)
            except ValueError:
                pass
        try:
            return datetime.strptime(text[:10], "%d.%m.%Y").date()
        except ValueError:
            continue
    return None


def normalize_employment_type(value):
    """Keep source employment labels as compact text when available."""
    if isinstance(value, list):
        values = [str(item).strip() for item in value if item]
        return ", ".join(values) or None
    text = str(value or "").strip()
    return text or None


def extract_annual_salary_eur(posting):
    """Extract an annual EUR salary range from schema.org JobPosting data."""
    salary = posting.get("baseSalary") or posting.get("estimatedSalary")
    if not isinstance(salary, dict):
        return None, None
    if str(salary.get("currency", "EUR")).upper() != "EUR":
        return None, None

    value = salary.get("value", salary)
    if not isinstance(value, dict):
        return numeric_salary(value), numeric_salary(value)

    unit = str(value.get("unitText", "YEAR")).upper()
    if unit not in {"YEAR", "YEARLY", "JAHR"}:
        return None, None

    minimum = numeric_salary(value.get("minValue"))
    maximum = numeric_salary(value.get("maxValue"))
    single_value = numeric_salary(value.get("value"))
    if minimum is None and maximum is None and single_value is not None:
        return single_value, single_value
    return minimum, maximum


def numeric_salary(value):
    """Return a whole-euro salary or None for unusable values."""
    try:
        return int(float(value)) if value is not None else None
    except (TypeError, ValueError):
        return None


def extract_schema_locations(job_location):
    """Extract unique city names from schema.org JobPosting locations."""
    locations = job_location if isinstance(job_location, list) else [job_location]
    cities = []

    for location in locations:
        if not isinstance(location, dict):
            continue
        addresses = location.get("address", [])
        if isinstance(addresses, dict):
            addresses = [addresses]
        for address in addresses:
            city = address.get("addressLocality")
            if city and city not in cities:
                cities.append(city)

    return cities or ["unbekannt"]
