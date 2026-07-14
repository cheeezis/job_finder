"""Shared normalization helpers for source adapters."""

import hashlib
from datetime import date, datetime, timezone


def utc_now():
    """Return a timezone-aware timestamp for source fetches."""
    return datetime.now(timezone.utc)


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
