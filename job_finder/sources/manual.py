"""User-supplied job links persisted as a small local source."""

import ipaddress
import re
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit

from job_finder.http import fetch_text_with_final_url
from job_finder.models import Job, JobSource
from job_finder.paths import MANUAL_CACHE_FILE
from job_finder.remote import classify_remote, detect_remote
from job_finder.sources.common import (
    canonical_detail_url,
    detail_is_fresh,
    load_detail_cache,
    mark_content_change,
    normalize_employment_type,
    parse_published_date,
    save_detail_cache,
    source_job_id,
    utc_now,
)
from job_finder.sources.company_careers import (
    identifier_from_url,
    job_from_json_ld,
)
from job_finder.structured_data import extract_json_ld_job_posting
from job_finder.text import html_to_text, normalize_text


SOURCE_NAME = "manual"
_BLOCK_TAGS = {"h1", "h2", "h3", "p", "li", "dt", "dd"}
_SKIP_TAGS = {"script", "style", "noscript", "nav", "footer", "form", "button"}


def add_url(url, cache_path=MANUAL_CACHE_FILE):
    """Fetch and persist one explicitly supplied public job URL."""
    requested_url = validate_public_url(url)
    final_url, html = fetch_text_with_final_url(requested_url)
    final_url = validate_public_url(final_url)
    cache_file = Path(cache_path)
    cache = load_detail_cache(cache_file)
    cache_key = canonical_detail_url(final_url)
    previous = cache.get(cache_key) or cache.get(canonical_detail_url(requested_url))
    job = job_from_page(final_url, html)
    mark_content_change(job, previous)
    cache.pop(canonical_detail_url(requested_url), None)
    cache[cache_key] = job
    save_detail_cache(cache_file, cache)
    return job


def fetch_jobs(cache_path=MANUAL_CACHE_FILE, now=None):
    """Return saved manual jobs and refresh details at the shared weekly cadence."""
    cache_file = Path(cache_path)
    cache = load_detail_cache(cache_file)
    jobs = []
    refreshed = {}
    errors = 0

    for saved_url, cached_job in cache.items():
        if detail_is_fresh(cached_job, now):
            cached_job.content_changed = False
            refreshed[saved_url] = cached_job
            jobs.append(cached_job)
            continue
        try:
            final_url, html = fetch_text_with_final_url(saved_url)
            final_url = validate_public_url(final_url)
            job = job_from_page(final_url, html)
            mark_content_change(job, cached_job)
            refreshed[canonical_detail_url(final_url)] = job
            jobs.append(job)
        except Exception:
            errors += 1
            cached_job.content_changed = False
            refreshed[saved_url] = cached_job
            jobs.append(cached_job)

    if refreshed != cache:
        save_detail_cache(cache_file, refreshed)
    if errors:
        print(
            f"WARNUNG Manuell: {errors} Detailseite(n) nicht erreichbar, "
            "aus lokalem Cache übernommen"
        )
    return jobs


def job_from_page(url, html):
    """Create a Job from structured data or the visible main page content."""
    posting = extract_json_ld_job_posting(html)
    if posting:
        job = job_from_json_ld(SOURCE_NAME, "", url, html)
        if not job.locations:
            remote_region = applicant_region(posting)
            if remote_region:
                job.locations = [remote_region]
        return job
    return job_from_visible_page(url, html)


def applicant_region(posting):
    """Read schema.org's allowed country for fully remote positions."""
    requirement = posting.get("applicantLocationRequirements")
    if isinstance(requirement, dict):
        return str(requirement.get("name") or "").strip()
    if isinstance(requirement, list):
        names = [
            str(item.get("name") or "").strip()
            for item in requirement
            if isinstance(item, dict)
        ]
        return ", ".join(name for name in names if name)
    return ""


def job_from_visible_page(url, html):
    """Fallback for career pages without schema.org JobPosting data."""
    parser = VisibleJobParser()
    parser.feed(html)
    title = parser.title or parser.metadata.get("og:title", "")
    company = parser.metadata.get("og:site_name", "") or urlsplit(url).hostname
    description_html = main_fragment(html)
    description = " ".join(parser.lines)
    locations = extract_labeled_values(parser.lines, {"standort", "arbeitsort", "location"})
    employment = first_labeled_value(
        parser.lines,
        {"beschaeftigungsart", "anstellungsart", "employment type"},
    )

    if not title or not company or len(description) < 200:
        raise ValueError("Auf der Seite wurde keine vollständige Stellenanzeige erkannt")

    remote = detect_remote(title, " ".join(locations), description)
    work_mode, remote_percentage = classify_remote(remote)
    identifier = identifier_from_url(url)
    return Job(
        id=source_job_id(SOURCE_NAME, identifier, url),
        title=title,
        company=company,
        locations=locations,
        sources=[
            JobSource(
                source=SOURCE_NAME,
                source_id=identifier,
                url=canonical_detail_url(url),
            )
        ],
        description_raw=description_html,
        description_clean=description,
        work_mode=work_mode,
        remote_percentage=remote_percentage,
        employment_type=normalize_employment_type(employment),
        published_at=parse_published_date(
            parser.metadata.get("article:published_time")
        ),
        fetched_at=utc_now(),
    )


def main_fragment(html):
    """Keep only the main visible document section for fallback imports."""
    match = re.search(r"<main\b[^>]*>(.*?)</main>", html, re.IGNORECASE | re.DOTALL)
    if not match:
        match = re.search(
            r"<article\b[^>]*>(.*?)</article>",
            html,
            re.IGNORECASE | re.DOTALL,
        )
    if not match:
        raise ValueError("Kein Hauptinhalt für die Stellenanzeige gefunden")
    return match.group(1)


def extract_labeled_values(lines, labels):
    value = first_labeled_value(lines, labels)
    return [value] if value else []


def first_labeled_value(lines, labels):
    """Return the value following a compact label such as 'Standort'."""
    normalized_labels = {normalize_text(label).rstrip(":") for label in labels}
    for index, line in enumerate(lines):
        normalized = normalize_text(line).strip()
        key = normalized.rstrip(":")
        if key in normalized_labels:
            return lines[index + 1].strip() if index + 1 < len(lines) else ""
        for label in normalized_labels:
            match = re.match(rf"^{re.escape(label)}\s*:\s*(.+)$", normalized)
            if match:
                return line[line.find(":") + 1 :].strip()
    return ""


def validate_public_url(value):
    """Accept ordinary public HTTP(S) links, never local filesystem/network URLs."""
    text = str(value or "").strip()
    parts = urlsplit(text)
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise ValueError("Bitte eine vollständige http(s)-URL eingeben")
    hostname = parts.hostname.casefold()
    if hostname == "localhost" or hostname.endswith(".local"):
        raise ValueError("Lokale Adressen können nicht importiert werden")
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        address = None
    if address and not address.is_global:
        raise ValueError("Private Netzwerkadressen können nicht importiert werden")
    return canonical_detail_url(text)


class VisibleJobParser(HTMLParser):
    """Collect metadata, an H1 title, and readable block lines inside main."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.metadata = {}
        self.lines = []
        self.title = ""
        self._in_main = False
        self._skip_depth = 0
        self._parts = []
        self._title_parts = []
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "meta":
            name = attributes.get("property") or attributes.get("name")
            content = attributes.get("content")
            if name and content:
                self.metadata[name.casefold()] = content.strip()
        if tag in {"main", "article"} and not self._in_main:
            self._in_main = True
            return
        if not self._in_main:
            return
        if self._skip_depth:
            self._skip_depth += 1
            return
        if tag in _SKIP_TAGS:
            self._flush()
            self._skip_depth = 1
            return
        if tag in _BLOCK_TAGS:
            self._flush()
        if tag == "h1":
            self._in_title = True
            self._title_parts = []

    def handle_endtag(self, tag):
        if not self._in_main:
            return
        if self._skip_depth:
            self._skip_depth -= 1
            return
        if tag == "h1":
            self.title = " ".join(" ".join(self._title_parts).split())
            self._in_title = False
        if tag in _BLOCK_TAGS:
            self._flush()
        if tag in {"main", "article"}:
            self._flush()
            self._in_main = False

    def handle_data(self, data):
        if not self._in_main or self._skip_depth:
            return
        text = " ".join(data.split())
        if not text:
            return
        self._parts.append(text)
        if self._in_title:
            self._title_parts.append(text)

    def _flush(self):
        text = " ".join(" ".join(self._parts).split())
        if text:
            self.lines.append(text)
        self._parts = []
