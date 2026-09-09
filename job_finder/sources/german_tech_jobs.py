"""GermanTechJobs source adapter using its public XML job feed."""

import json
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path

from job_finder.http import fetch_text
from job_finder.models import Job, JobSource
from job_finder.paths import GERMAN_TECH_JOBS_CACHE_FILE
from job_finder.remote import classify_remote, detect_remote
from job_finder.sources.common import (
    normalize_employment_type,
    parse_published_date,
    source_job_id,
    utc_now,
)
from job_finder.storage import write_json_atomic
from job_finder.text import html_to_text


SOURCE_NAME = "german_tech_jobs"
FEED_URL = "https://germantechjobs.de/job_feed.xml"
CACHE_FILE = GERMAN_TECH_JOBS_CACHE_FILE
CACHE_VERSION = 1
MAX_STALE_FEED_AGE = timedelta(days=3)


def fetch_jobs(cache_path=CACHE_FILE, now=None):
    """Return current jobs, using a recent cache only after a feed failure."""
    return fetch_jobs_with_report(cache_path=cache_path, now=now)["jobs"]


def fetch_jobs_with_report(cache_path=CACHE_FILE, now=None):
    """Fetch and parse the complete feed with explicit fallback diagnostics."""
    fetched_at = now or utc_now()
    try:
        jobs, invalid_records = parse_feed(fetch_text(FEED_URL), fetched_at)
    except Exception:
        cached = load_feed_cache(cache_path, fetched_at)
        if not cached:
            raise
        return {
            "jobs": cached,
            "status": "partial",
            "details": {"failed_segments": 1, "total_segments": 1},
        }

    save_feed_cache(cache_path, jobs, fetched_at)
    return {
        "jobs": jobs,
        "status": "partial" if invalid_records else ("success" if jobs else "empty"),
        "details": {
            "failed_segments": invalid_records,
            "total_segments": len(jobs) + invalid_records,
        },
    }


def parse_feed(xml_text, fetched_at=None):
    """Parse valid job elements and report malformed records separately."""
    root = ET.fromstring(xml_text)
    jobs = []
    invalid_records = 0
    timestamp = fetched_at or utc_now()
    for element in root.findall(".//job"):
        try:
            jobs.append(job_from_element(element, timestamp))
        except ValueError:
            invalid_records += 1
    return jobs, invalid_records


def job_from_element(element, fetched_at=None):
    """Convert one XML job element into the shared Job model."""
    identifier = element_text(element, "id") or str(element.get("id") or "").strip()
    title = element_text(element, "title", "name")
    company = element_text(element, "company-name", "company")
    listing_url = element_text(element, "link", "url")
    if not identifier or not title or not company or not listing_url:
        raise ValueError("GermanTechJobs-Eintrag ohne ID, Titel, Firma oder URL")

    raw_description = element_text(element, "description")
    description = html_to_text(raw_description)
    location = element_text(element, "location")
    city = element_text(element, "city")
    remote_text = detect_remote(title, location, description)
    work_mode, remote_percentage = classify_remote(remote_text)
    salary_minimum, salary_maximum = annual_salary_eur(
        element_text(element, "salary")
    )
    application_url = element_text(element, "apply_url") or None

    return Job(
        id=source_job_id(SOURCE_NAME, identifier, listing_url),
        title=title,
        company=company,
        locations=location_names(city, location, element_text(element, "country")),
        sources=[
            JobSource(
                source=SOURCE_NAME,
                source_id=identifier,
                url=listing_url,
                application_url=(
                    application_url if application_url != listing_url else None
                ),
            )
        ],
        description_raw=raw_description,
        description_clean=description,
        work_mode=work_mode,
        remote_percentage=remote_percentage,
        employment_type=normalize_employment_type(
            element_text(element, "job-type", "jobtype", "job-status")
        ),
        salary_min_eur=salary_minimum,
        salary_max_eur=salary_maximum,
        published_at=parse_published_date(element_text(element, "pubdate")),
        fetched_at=fetched_at or utc_now(),
    )


def element_text(element, *names):
    """Return the first non-empty child text among equivalent feed fields."""
    for name in names:
        child = element.find(name)
        value = "" if child is None else "".join(child.itertext()).strip()
        if value:
            return value
    return ""


def location_names(city, location, country):
    """Prefer a city while retaining explicit full-remote feed locations."""
    if re.search(r"(?i)\b(?:full|fully|100\s*%)?\s*remote\b", location):
        return [location]
    return [city or location or country or "unbekannt"]


def annual_salary_eur(value):
    """Parse the feed's annual euro salary range into whole euro values."""
    text = str(value or "").strip()
    if not re.search(r"(?i)(?:€|eur)", text) or not re.search(
        r"(?i)(?:year|jahr|annual)", text
    ):
        return None, None
    amounts = [
        int(re.sub(r"\D", "", match))
        for match in re.findall(r"\d[\d.,'’\s]*", text)
        if re.sub(r"\D", "", match)
    ]
    if not amounts:
        return None, None
    if len(amounts) == 1:
        return amounts[0], amounts[0]
    minimum, maximum = amounts[:2]
    if minimum > maximum:
        return None, None
    return minimum, maximum


def save_feed_cache(path, jobs, fetched_at):
    """Atomically store the last complete parsed feed for short outages."""
    write_json_atomic(
        Path(path),
        {
            "version": CACHE_VERSION,
            "fetched_at": fetched_at.isoformat(),
            "jobs": [job.to_dict() for job in jobs],
        },
    )


def load_feed_cache(path, now=None):
    """Restore a recent successful feed snapshot as a marked fallback."""
    try:
        document = json.loads(Path(path).read_text(encoding="utf-8"))
        if document.get("version") != CACHE_VERSION:
            return []
        fetched_at = datetime.fromisoformat(document["fetched_at"])
        if fetched_at.tzinfo is None:
            fetched_at = fetched_at.replace(tzinfo=timezone.utc)
        current = now or utc_now()
        if current - fetched_at > MAX_STALE_FEED_AGE:
            return []
        jobs = [Job.from_dict(values) for values in document.get("jobs", [])]
    except (KeyError, OSError, ValueError):
        return []
    for job in jobs:
        job.cache_stale = True
    return jobs
