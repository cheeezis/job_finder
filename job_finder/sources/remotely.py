"""Remotely.de source adapter for recent remote and home-office jobs."""

import json
import re
import time
from datetime import date, datetime, timedelta, timezone
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlsplit

from job_finder.console import print_progress, progress_checkpoint
from job_finder.http import fetch_text, fetch_text_with_final_url
from job_finder.models import Job, JobSource
from job_finder.paths import REMOTELY_CACHE_FILE, REMOTELY_LINKEDIN_STATUS_FILE
from job_finder.remote import classify_remote, detect_remote
from job_finder.sources.common import (
    ListingUnavailableError,
    fetch_cached_details,
    source_job_id,
    utc_now,
)
from job_finder.text import html_to_text


SOURCE_NAME = "remotely"
BASE_URL = "https://www.remotely.de"
LIST_URL = f"{BASE_URL}/alle-jobs"
CACHE_FILE = REMOTELY_CACHE_FILE
LINKEDIN_STATUS_FILE = REMOTELY_LINKEDIN_STATUS_FILE
MAX_LIST_PAGES = 100
OLD_PAGE_STOP_COUNT = 2
LOOKBACK_DAYS = 7
BOUNDARY_BUFFER_DAYS = 2
DETAIL_REFRESH_DAYS = 1
REQUEST_DELAY_SECONDS = 1.0
LINKEDIN_REQUEST_DELAY_SECONDS = 0.4
LINKEDIN_STATUS_MAX_AGE = timedelta(days=1)
LINKEDIN_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 Chrome/128 Safari/537.36"
    ),
    "Accept-Language": "de-DE,de;q=0.9,en;q=0.7",
}
LINKEDIN_CLOSED_MARKERS = (
    "es werden keine bewerbungen mehr angenommen",
    "no longer accepting applications",
)


class RemotelyHttpClient:
    """Pace public page requests so a normal run stays considerate."""

    def __init__(self, delay=REQUEST_DELAY_SECONDS, sleeper=time.sleep):
        self.delay = delay
        self.sleeper = sleeper
        self.has_requested = False

    def get(self, url):
        if self.has_requested:
            self.sleeper(self.delay)
        self.has_requested = True
        return fetch_text(url)


def fetch_jobs(cache_path=CACHE_FILE, client=None, now=None):
    """Fetch only listings published within the rolling lookback window."""
    client = client or RemotelyHttpClient()
    cache_file = Path(cache_path)
    reference_date = (now.date() if hasattr(now, "date") else now) or date.today()
    links = collect_links(
        client,
        today=reference_date,
    )
    jobs = fetch_cached_details(
        links,
        cache_file,
        lambda url: fetch_job(url, client),
        "Remotely",
        now=now,
        max_age=timedelta(days=DETAIL_REFRESH_DAYS),
    )
    return jobs


def enrich_candidate_jobs(
    jobs,
    candidate_ids,
    status_cache_path=LINKEDIN_STATUS_FILE,
    fetcher=fetch_text_with_final_url,
    now=None,
    sleeper=time.sleep,
):
    """Remove prefiltered candidates whose LinkedIn application is closed."""
    targets = [
        (index, linkedin_application_url(job))
        for index, job in enumerate(jobs)
        if job.id in candidate_ids and linkedin_application_url(job)
    ]
    if not targets:
        return 0

    checked_at = now or utc_now()
    status_path = Path(status_cache_path)
    checks = load_linkedin_status_cache(status_path)
    cache_changed = False
    closed_indices = set()
    errors = 0
    requests_made = 0

    for position, (job_index, url) in enumerate(targets, 1):
        key = linkedin_job_key(url)
        cached = checks.get(key)
        closed = fresh_linkedin_status(cached, checked_at)
        if closed is None:
            if requests_made:
                sleeper(LINKEDIN_REQUEST_DELAY_SECONDS)
            requests_made += 1
            try:
                final_url, html = fetcher(url, headers=LINKEDIN_HEADERS)
                closed = linkedin_listing_is_closed(url, final_url, html)
                checks[key] = {
                    "closed": closed,
                    "checked_at": checked_at.isoformat(),
                }
                cache_changed = True
            except Exception:
                errors += 1
                closed = False
        if closed:
            closed_indices.add(job_index)
        if progress_checkpoint(position, len(targets)):
            print_progress(
                "Remotely LinkedIn",
                position,
                len(targets),
                f"{len(closed_indices)} geschlossen",
            )

    if cache_changed:
        save_linkedin_status_cache(status_path, checks)
    if closed_indices:
        jobs[:] = [
            job for index, job in enumerate(jobs) if index not in closed_indices
        ]
        print(
            f"HINWEIS Remotely: {len(closed_indices)} geschlossene "
            "LinkedIn-Bewerbung(en) aus dem Review entfernt"
        )
    if errors:
        print(
            f"WARNUNG Remotely: {errors} LinkedIn-Statusprüfung(en) "
            "nicht erreichbar; Stellen vorsichtshalber behalten"
        )
    return len(closed_indices)


def linkedin_application_url(job):
    """Return the LinkedIn application URL contributed by Remotely."""
    for source in job.sources:
        if source.source != SOURCE_NAME:
            continue
        url = str(source.application_url or "").strip()
        parts = urlsplit(url)
        host = (parts.hostname or "").casefold()
        if (
            (host == "linkedin.com" or host.endswith(".linkedin.com"))
            and "/jobs/view/" in parts.path.casefold()
        ):
            return url
    return ""


def linkedin_listing_is_closed(original_url, final_url, html):
    """Recognize LinkedIn's closed message and expired-job redirects."""
    original_id = linkedin_job_id(original_url)
    final_id = linkedin_job_id(final_url)
    if not final_id or (original_id and final_id != original_id):
        return True
    text = str(html or "").casefold()
    return any(marker in text for marker in LINKEDIN_CLOSED_MARKERS)


def linkedin_job_id(url):
    path = urlsplit(str(url or "")).path.rstrip("/")
    match = re.search(r"-(\d+)$", path)
    return match.group(1) if match else ""


def linkedin_job_key(url):
    identifier = linkedin_job_id(url)
    return identifier or normalize_detail_url(url)


def load_linkedin_status_cache(path):
    try:
        document = json.loads(Path(path).read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return {}
    if document.get("version") != 1:
        return {}
    return document.get("checks", {})


def save_linkedin_status_cache(path, checks):
    status_path = Path(path)
    status_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = status_path.with_suffix(f"{status_path.suffix}.tmp")
    temporary.write_text(
        json.dumps(
            {"version": 1, "checks": checks},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    temporary.replace(status_path)


def fresh_linkedin_status(entry, now):
    if not isinstance(entry, dict) or not isinstance(entry.get("closed"), bool):
        return None
    try:
        checked_at = datetime.fromisoformat(entry["checked_at"])
    except (KeyError, TypeError, ValueError):
        return None
    if checked_at.tzinfo is None:
        checked_at = checked_at.replace(tzinfo=timezone.utc)
    current = now
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    if current - checked_at >= LINKEDIN_STATUS_MAX_AGE:
        return None
    return entry["closed"]


def collect_links(
    client=None,
    known_urls=None,
    today=None,
    max_pages=None,
    initial_scan=None,
):
    """Collect recent listings and stop at the old or known frontier."""
    client = client or RemotelyHttpClient()
    page_limit = max_pages or MAX_LIST_PAGES
    reference_date = today or date.today()
    cutoff = reference_date - timedelta(days=LOOKBACK_DAYS)
    links = []
    seen = set()
    consecutive_old_pages = 0
    for page in range(1, page_limit + 1):
        url = LIST_URL if page == 1 else f"{LIST_URL}/seite/{page}"
        entries = extract_list_entries(client.get(url))
        page_links = [
            entry["url"]
            for entry in entries
            if entry_is_recent(entry, cutoff=cutoff, today=reference_date)
        ]
        new_links = [link for link in page_links if link not in seen]
        if not entries:
            break
        for link in new_links:
            seen.add(link)
            links.append(link)
        if page_is_before_cutoff(entries, cutoff, reference_date):
            consecutive_old_pages += 1
        else:
            consecutive_old_pages = 0
        if consecutive_old_pages >= OLD_PAGE_STOP_COUNT:
            break
    return links


def extract_detail_links(html):
    """Extract canonical public job URLs without relying on CSS classes."""
    return [entry["url"] for entry in extract_list_entries(html)]


def extract_list_entries(html):
    """Return each job link together with its visible relative date label."""
    parser = _RemotelyListParser()
    parser.feed(str(html or ""))
    parser.close()
    entries = []
    seen = set()
    for href, text, promoted in parser.entries:
        url = normalize_detail_url(urljoin(BASE_URL, unescape(href)))
        if url in seen:
            continue
        seen.add(url)
        date_match = re.search(
            r"\b(heute|gestern|vor \d+ (?:tag(?:en)?|woche(?:n)?|monat(?:en)?|jahr(?:en)?))\b",
            clean_text(text).casefold(),
        )
        entries.append(
            {
                "url": url,
                "published_label": date_match.group(1) if date_match else "",
                "promoted": promoted,
            }
        )
    return entries


def entry_is_recent(entry, cutoff, today):
    """Reject cards whose visible publication date is outside the window."""
    published_at = parse_relative_date(entry.get("published_label"), today=today)
    return published_at is not None and published_at >= cutoff


def page_is_before_cutoff(entries, cutoff, today):
    """Use old regular cards as a conservative pagination stop signal."""
    regular_entries = [entry for entry in entries if not entry.get("promoted")]
    dates = [
        parse_relative_date(entry.get("published_label"), today=today)
        for entry in regular_entries
    ]
    if not dates or any(value is None for value in dates):
        return False
    safe_boundary = cutoff - timedelta(days=BOUNDARY_BUFFER_DAYS)
    return all(value < safe_boundary for value in dates)


def normalize_detail_url(url):
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}{parts.path}".rstrip("/")


def fetch_job(url, client=None):
    """Convert one visible Remotely detail page into the shared Job model."""
    client = client or RemotelyHttpClient()
    return job_from_html(normalize_detail_url(url), client.get(url))


def job_from_html(url, html, today=None):
    parser = _RemotelyDetailParser()
    parser.feed(str(html or ""))
    parser.close()
    title = clean_text(parser.title)
    company = clean_text(parser.company)
    description_raw = parser.description_html.strip()
    description = html_to_text(description_raw)
    if is_unavailable_listing(title, description):
        raise ListingUnavailableError("Remotely-Anzeige ist bereits vergeben")
    if not title or not company or not description:
        raise ValueError("Remotely-Anzeige ohne Titel, Unternehmen oder Beschreibung")

    location = clean_text(parser.location) or "Remote"
    work_model = clean_text(parser.work_model)
    detected_remote = detect_remote(title, description, location, work_model)
    work_mode, remote_percentage = classify_remote(detected_remote)
    identifier = urlsplit(url).path.rsplit("/", 1)[-1]
    source = JobSource(
        source=SOURCE_NAME,
        source_id=identifier,
        url=url,
        application_url=parser.application_url or None,
    )
    return Job(
        id=source_job_id(SOURCE_NAME, identifier, url),
        title=title,
        company=company,
        locations=[location],
        sources=[source],
        description_raw=description_raw,
        description_clean=description,
        work_mode=work_mode,
        remote_percentage=remote_percentage,
        published_at=parse_relative_date(parser.published_label, today=today),
        fetched_at=utc_now(),
    )


def is_unavailable_listing(title, description):
    """Recognize closure markers in rendered job fields, excluding scripts."""
    normalized_title = clean_text(title).casefold()
    normalized_description = clean_text(description).casefold()
    return normalized_title in {"bereits vergeben", "stelle nicht verfügbar"} or bool(
        re.search(
            r"\bjob bereits vergeben\b|"
            r"(?:stelle|stellenanzeige).{0,40}nicht mehr verfügbar",
            normalized_description,
        )
    )


def parse_relative_date(value, today=None):
    """Translate the German relative date shown on Remotely into a date."""
    label = clean_text(value).casefold()
    current = today or date.today()
    if label == "heute":
        return current
    if label == "gestern":
        return current - timedelta(days=1)
    match = re.fullmatch(
        r"vor (\d+) (tag(?:en)?|woche(?:n)?|monat(?:en)?|jahr(?:en)?)",
        label,
    )
    if not match:
        return None
    amount = int(match.group(1))
    unit = match.group(2)
    if unit.startswith("tag"):
        days = amount
    elif unit.startswith("woche"):
        days = amount * 7
    elif unit.startswith("monat"):
        days = amount * 30
    else:
        days = amount * 365
    return current - timedelta(days=days)


def clean_text(value):
    return " ".join(unescape(str(value or "")).split())


class _RemotelyDetailParser(HTMLParser):
    """Read visible semantic fields and the job-description container."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.company = ""
        self.location = ""
        self.work_model = ""
        self.published_label = ""
        self.application_url = ""
        self.description_parts = []
        self.capture_title = False
        self.capture_company = False
        self.capture_heading = False
        self.heading_parts = []
        self.active_section = ""
        self.description_depth = 0
        self.next_value = ""

    @property
    def description_html(self):
        return "".join(self.description_parts)

    def handle_starttag(self, tag, attrs):
        values = {name: value or "" for name, value in attrs}
        classes = values.get("class", "")
        if self.description_depth:
            self.description_parts.append(self.get_starttag_text())
            if tag == "div":
                self.description_depth += 1
            return
        if tag == "div" and "prose" in classes.split():
            self.description_depth = 1
            return
        if tag == "h1" and not self.title:
            self.capture_title = True
        elif tag == "h3":
            self.capture_heading = True
            self.heading_parts = []
        elif (
            tag == "p"
            and not self.company
            and "uppercase" in classes.split()
            and "tracking-wide" in classes.split()
        ):
            self.capture_company = True
        if tag == "a" and values.get("data-apply-cta") == "true":
            href = values.get("href", "")
            if href.startswith(("http://", "https://")):
                self.application_url = unescape(href)
        if "lucide-map-pin" in classes:
            self.next_value = "location"
        elif "lucide-calendar-days" in classes:
            self.next_value = "published_label"

    def handle_startendtag(self, tag, attrs):
        if self.description_depth:
            self.description_parts.append(self.get_starttag_text())

    def handle_endtag(self, tag):
        if self.description_depth:
            if tag == "div":
                self.description_depth -= 1
                if not self.description_depth:
                    return
            self.description_parts.append(f"</{tag}>")
            return
        if tag == "h1":
            self.capture_title = False
        elif tag == "p":
            self.capture_company = False
        elif tag == "h3" and self.capture_heading:
            heading = clean_text("".join(self.heading_parts)).casefold()
            self.active_section = heading
            self.capture_heading = False

    def handle_data(self, data):
        text = clean_text(data)
        if not text:
            return
        if self.description_depth:
            self.description_parts.append(data)
            return
        if self.capture_title:
            self.title += data
        if self.capture_company:
            self.company += data
        if self.capture_heading:
            self.heading_parts.append(data)
            return
        if self.next_value:
            setattr(self, self.next_value, data)
            self.next_value = ""
        elif self.active_section == "arbeitsmodell" and not self.work_model:
            self.work_model = data


class _RemotelyListParser(HTMLParser):
    """Capture visible text inside public job-card links."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.entries = []
        self.href = ""
        self.in_anchor = False
        self.text_parts = []
        self.promoted = False

    def handle_starttag(self, tag, attrs):
        values = {name: value or "" for name, value in attrs}
        if self.in_anchor:
            classes = values.get("class", "").casefold()
            if "featured" in classes or "sponsored" in classes:
                self.promoted = True
            return
        href = values.get("href", "") if tag == "a" else ""
        if "/job/" in href:
            self.href = href
            self.in_anchor = True
            self.text_parts = []
            self.promoted = False

    def handle_startendtag(self, tag, attrs):
        return

    def handle_endtag(self, tag):
        if not self.in_anchor or tag != "a":
            return
        self.entries.append(
            (self.href, " ".join(self.text_parts), self.promoted)
        )
        self.href = ""
        self.in_anchor = False
        self.text_parts = []
        self.promoted = False

    def handle_data(self, data):
        if self.in_anchor and clean_text(data):
            self.text_parts.append(data)
            if clean_text(data).casefold() == "gesponsert":
                self.promoted = True
