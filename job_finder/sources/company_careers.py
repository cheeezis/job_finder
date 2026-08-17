"""Shared detail importing for selected company career pages."""

import re
from html import unescape
from pathlib import Path
from urllib.parse import urljoin, urlsplit

from job_finder.http import fetch_text
from job_finder.models import Job, JobSource
from job_finder.remote import classify_remote, detect_remote
from job_finder.sources.common import (
    DETAIL_CACHE_SAVE_INTERVAL,
    canonical_detail_url,
    detail_is_fresh,
    extract_annual_salary_eur,
    extract_schema_locations,
    load_detail_cache,
    mark_content_change,
    normalize_employment_type,
    parse_published_date,
    save_detail_cache,
    source_job_id,
    utc_now,
)
from job_finder.structured_data import extract_json_ld_job_posting
from job_finder.text import html_to_text


def fetch_company_jobs(
    source_name,
    company,
    links,
    cache_path,
    now=None,
    parser=None,
):
    """Import company details with the same weekly cache used by job boards."""
    cache_file = Path(cache_path)
    cache = load_detail_cache(cache_file)
    jobs = []
    unsaved = 0
    cache_updated = False
    parser = parser or job_from_json_ld

    for url in links:
        cache_key = canonical_detail_url(url)
        cached_job = cache.get(cache_key)
        if detail_is_fresh(cached_job, now):
            cache_updated = ensure_url_identity(cached_job, source_name, url) or cache_updated
            cached_job.content_changed = False
            jobs.append(cached_job)
            print(f"CACHE: {url}")
            continue

        try:
            html = fetch_text(url)
            job = parser(source_name, company, url, html)
            ensure_url_identity(job, source_name, url)
            mark_content_change(job, cached_job)
            jobs.append(job)
            cache[cache_key] = job
            unsaved += 1
            if unsaved >= DETAIL_CACHE_SAVE_INTERVAL:
                save_detail_cache(cache_file, cache)
                unsaved = 0
            print(f"OK: {url}")
        except Exception as error:
            print(f"FEHLER: {url}")
            print(f"       {error}")
            if cached_job:
                cache_updated = ensure_url_identity(cached_job, source_name, url) or cache_updated
                cached_job.content_changed = False
                jobs.append(cached_job)
                print(f"CACHE (veraltet): {url}")

    if unsaved or cache_updated:
        save_detail_cache(cache_file, cache)
    return jobs


def job_from_json_ld(source_name, fallback_company, url, html):
    """Create a shared Job from a company's schema.org JobPosting."""
    posting = extract_json_ld_job_posting(html)
    if not posting:
        raise ValueError("JobPosting JSON-LD nicht gefunden")

    raw_description = posting.get("description", "")
    description = html_to_text(raw_description)
    locations = extract_schema_locations(posting.get("jobLocation"))
    location_text = ", ".join(locations)
    title = str(posting.get("title") or "").strip()
    structured_remote = (
        "100%"
        if str(posting.get("jobLocationType") or "").upper() == "TELECOMMUTE"
        else ""
    )
    remote = detect_remote(
        title,
        description,
        location_text,
        structured_remote=structured_remote,
    )
    work_mode, remote_percentage = classify_remote(remote)
    identifier = identifier_from_url(url)
    salary_min, salary_max = extract_annual_salary_eur(posting)
    organization = posting.get("hiringOrganization") or {}
    company = (
        organization.get("name", "") if isinstance(organization, dict) else ""
    )

    return Job(
        id=source_job_id(source_name, identifier, url),
        title=title,
        company=str(company or fallback_company).strip(),
        locations=locations,
        sources=[JobSource(source=source_name, source_id=identifier, url=url)],
        description_raw=raw_description,
        description_clean=description,
        work_mode=work_mode,
        remote_percentage=remote_percentage,
        employment_type=normalize_employment_type(posting.get("employmentType")),
        salary_min_eur=salary_min,
        salary_max_eur=salary_max,
        published_at=parse_published_date(posting.get("datePosted")),
        fetched_at=utc_now(),
    )


def extract_links(html, base_url, pattern):
    """Return canonical links whose absolute URLs match a regex."""
    links = []
    seen = set()
    for match in re.findall(r'href=["\']([^"\']+)', html, re.IGNORECASE):
        url = canonical_detail_url(urljoin(base_url, unescape(match)))
        if not re.search(pattern, url, re.IGNORECASE) or url in seen:
            continue
        seen.add(url)
        links.append(url)
    return links


def ensure_url_identity(job, source_name, url):
    """Use the unique career-page URL as the source ID.

    Some career sites publish one generic schema.org identifier for every
    opening.  Using it would merge unrelated postings in memory and caches.
    """
    identifier = identifier_from_url(url)
    changed = job.id != source_job_id(source_name, identifier, url)
    job.id = source_job_id(source_name, identifier, url)
    for source in job.sources:
        if source.source == source_name:
            changed = changed or source.source_id != identifier or source.url != url
            source.source_id = identifier
            source.url = url
            break
    return changed


def identifier_from_url(url):
    """Prefer a numeric or hexadecimal ID at the end of a career URL."""
    match = re.search(
        r"(?:jobOfferId=|/job/|[-/])([a-f0-9]{8,}|\d{3,})(?:\D*$|$)",
        url,
        re.IGNORECASE,
    )
    if match:
        return match.group(1)
    return urlsplit(url).path.rstrip("/").rsplit("/", 1)[-1]
