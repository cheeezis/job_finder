"""User-supplied job links persisted as a small local source."""

import ipaddress
import re
import socket
from html.parser import HTMLParser
from urllib.parse import urlsplit

from job_finder.http import fetch_text_with_final_url
from job_finder.matching.remote import classify_remote, detect_remote
from job_finder.models import Job, JobSource
from job_finder.paths import MANUAL_CACHE_FILE
from job_finder.sources.common import (
    canonical_detail_url,
    detail_is_fresh,
    detail_within_age,
    load_detail_cache,
    normalize_employment_type,
    parse_published_date,
    record_partial_failure,
    save_detail_cache,
    source_job_id,
    utc_now,
)
from job_finder.sources.company_careers import identifier_from_url, job_from_posting
from job_finder.structured_data import extract_json_ld_job_posting
from job_finder.text import normalize_text

SOURCE_NAME = "manual"
_BLOCK_TAGS = {"h1", "h2", "h3", "p", "li", "dt", "dd"}
_SKIP_TAGS = {"script", "style", "noscript", "nav", "footer", "form", "button"}
_VOID_TAGS = {
    "area",
    "base",
    "br",
    "col",
    "embed",
    "hr",
    "img",
    "input",
    "link",
    "meta",
    "param",
    "source",
    "track",
    "wbr",
}


def add_url(url, cache_path=MANUAL_CACHE_FILE):
    """Fetch and persist one explicitly supplied public job URL."""
    requested_url = validate_public_url(url)
    final_url, html = fetch_text_with_final_url(requested_url, url_validator=validate_public_url)
    cache = load_detail_cache(cache_path)
    cache_key = canonical_detail_url(final_url)
    job = job_from_page(final_url, html)
    cache.pop(canonical_detail_url(requested_url), None)
    cache[cache_key] = job
    save_detail_cache(cache_path, cache)
    return job


def fetch_jobs(cache_path=MANUAL_CACHE_FILE, now=None):
    """Return saved manual jobs and refresh details at the shared weekly cadence."""
    cache = load_detail_cache(cache_path)
    jobs = []
    refreshed = {}
    errors = 0

    for saved_url, cached_job in cache.items():
        if detail_is_fresh(cached_job, now):
            cached_job.cache_stale = False
            refreshed[saved_url] = cached_job
            jobs.append(cached_job)
            continue
        try:
            final_url, html = fetch_text_with_final_url(
                saved_url, url_validator=validate_public_url
            )
            job = job_from_page(final_url, html)
            job.cache_stale = False
            refreshed[canonical_detail_url(final_url)] = job
            jobs.append(job)
        except Exception:
            errors += 1
            # A manual submission is durable input even when its web cache is
            # too old to use as a current result. Keep it for the next refresh.
            refreshed[saved_url] = cached_job
            if detail_within_age(cached_job, now):
                cached_job.cache_stale = True
                jobs.append(cached_job)

    if refreshed != cache:
        save_detail_cache(cache_path, refreshed)
    if errors:
        record_partial_failure(errors)
        print(
            f"WARNUNG Manuell: {errors} Detailseite(n) nicht erreichbar, "
            "aus lokalem Cache übernommen"
        )
    return jobs


def job_from_page(url, html):
    """Create a Job from structured data or the visible main page content."""
    posting = extract_json_ld_job_posting(html)
    if posting:
        job = job_from_posting(SOURCE_NAME, "", url, posting)
        if not job.locations or job.locations == ["unbekannt"]:
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
            str(item.get("name") or "").strip() for item in requirement if isinstance(item, dict)
        ]
        return ", ".join(name for name in names if name)
    return ""


def job_from_visible_page(url, html):
    """Fallback for career pages without schema.org JobPosting data."""
    parser = VisibleJobParser()
    parser.feed(html)
    title = parser.title or parser.metadata.get("og:title", "")
    company = parser.metadata.get("og:site_name", "") or urlsplit(url).hostname
    description_html = parser.main_fragment(html)
    description = " ".join(parser.lines)
    locations = extract_labeled_values(parser.lines, {"standort", "arbeitsort", "location"})
    employment = first_labeled_value(
        parser.lines, {"beschaeftigungsart", "anstellungsart", "employment type"}
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
            JobSource(source=SOURCE_NAME, source_id=identifier, url=canonical_detail_url(url))
        ],
        description_raw=description_html,
        description_clean=description,
        work_mode=work_mode,
        remote_percentage=remote_percentage,
        employment_type=normalize_employment_type(employment),
        published_at=parse_published_date(parser.metadata.get("article:published_time")),
        fetched_at=utc_now(),
    )


def extract_labeled_values(lines, labels):
    """Return the first labelled value as a list, or [] when absent."""
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
    """Accept HTTP(S) URLs only when every resolved address is public."""
    text = str(value or "").strip()
    parts = urlsplit(text)
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise ValueError("Bitte eine vollständige http(s)-URL eingeben")
    hostname = parts.hostname.casefold()
    if hostname == "localhost" or hostname.endswith(".local"):
        raise ValueError("Lokale Adressen können nicht importiert werden")
    try:
        addresses = {item[4][0] for item in socket.getaddrinfo(hostname, parts.port)}
    except socket.gaierror as error:
        raise ValueError("Adresse der Stellenanzeige konnte nicht aufgelöst werden") from error
    if not addresses or any(not ipaddress.ip_address(item).is_global for item in addresses):
        raise ValueError("Private Netzwerkadressen können nicht importiert werden")
    # Preserve functional query parameters; canonicalization is only a cache concern.
    return parts._replace(fragment="").geturl()


class VisibleJobParser(HTMLParser):
    """Collect metadata, an H1 title, and readable block lines inside main."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.metadata = {}
        self.lines = []
        self.title = ""
        self._in_main = False
        self._main_stack = []
        self.fragment_start = None
        self.fragment_end = None
        self._skip_depth = 0
        self._parts = []
        self._title_parts = []
        self._in_title = False

    def main_fragment(self, html):
        """Extract the main section from the HTML already fed to this parser."""
        if self.fragment_start is None or self.fragment_end is None:
            raise ValueError("Kein Hauptinhalt für die Stellenanzeige gefunden")
        offsets = [0]
        for line in html.splitlines(keepends=True):
            offsets.append(offsets[-1] + len(line))
        start_line, start_column = self.fragment_start
        end_line, end_column = self.fragment_end
        return html[offsets[start_line - 1] + start_column : offsets[end_line - 1] + end_column]

    def handle_starttag(self, tag, attrs):
        """Track the main container and skip non-job blocks, respecting void tags."""
        attributes = dict(attrs)
        if tag == "meta":
            name = attributes.get("property") or attributes.get("name")
            content = attributes.get("content")
            if name and content:
                self.metadata[name.casefold()] = content.strip()
        if (
            tag in {"main", "article"} or attributes.get("role", "").casefold() == "main"
        ) and self.fragment_start is None:
            self._in_main = True
            self._main_stack = [tag]
            line, column = self.getpos()
            self.fragment_start = (line, column + len(self.get_starttag_text()))
            return
        if not self._in_main:
            return
        if tag not in _VOID_TAGS:
            self._main_stack.append(tag)
        if self._skip_depth:
            if tag not in _VOID_TAGS:
                self._skip_depth += 1
            return
        if (
            tag in _SKIP_TAGS
            or attributes.get("role") in {"navigation", "contentinfo"}
            or attributes.get("id") == "footer"
        ):
            self._flush()
            self._skip_depth = 1
            return
        if tag in _BLOCK_TAGS:
            self._flush()
        if tag == "h1":
            self._in_title = True
            self._title_parts = []

    def handle_endtag(self, tag):
        """Close nested capture scopes and record the main fragment boundary."""
        if not self._in_main:
            return
        if tag not in self._main_stack:
            return
        index = len(self._main_stack) - 1 - self._main_stack[::-1].index(tag)
        closed_count = len(self._main_stack) - index
        del self._main_stack[index:]
        if self._skip_depth:
            self._skip_depth = max(0, self._skip_depth - closed_count)
            if self._main_stack:
                return
        if tag == "h1":
            self.title = " ".join(" ".join(self._title_parts).split())
            self._in_title = False
        if tag in _BLOCK_TAGS:
            self._flush()
        if not self._main_stack:
            self._flush()
            self._in_main = False
            self.fragment_end = self.getpos()

    def handle_startendtag(self, tag, attrs):
        """Process self-closing elements without leaving a capture scope open."""
        self.handle_starttag(tag, attrs)
        if tag not in _VOID_TAGS:
            self.handle_endtag(tag)

    def handle_data(self, data):
        """Collect readable text and title parts from unskipped main content."""
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
