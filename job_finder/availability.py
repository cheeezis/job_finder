"""Confirm closed shortlisted listings without treating search gaps as closure."""

import re
import time
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from urllib.error import HTTPError
from urllib.parse import urlsplit

from job_finder.applications import record_status_change
from job_finder.http import fetch_text_with_final_url
from job_finder.memory import edit_memory, has_application_state, inferred_sources, load_memory
from job_finder.models import WorkflowStatus
from job_finder.sources.arbeitnow import application_page_is_missing
from job_finder.sources.manual import VisibleJobParser, validate_public_url
from job_finder.structured_data import extract_json_ld_job_posting


CLOSED_MESSAGE = re.compile(
    r"^(?:(?:diese|die) (?:stelle|stellenanzeige|position|ausschreibung) "
    r"(?:ist|wurde) (?:leider )?(?:nicht mehr verfügbar|bereits vergeben|geschlossen|besetzt)"
    r"|(?:this|the) (?:job|position|vacancy) (?:is|has been) "
    r"(?:no longer available|closed|filled)"
    r"|(?:job|stelle) (?:bereits vergeben|nicht verfügbar)"
    r"|no longer accepting applications"
    r"|es werden keine bewerbungen mehr angenommen)[.!\s]*$",
    re.IGNORECASE,
)


class MissingPageHeadingParser(HTMLParser):
    """Read page headings even when an error template has no main element."""

    def __init__(self):
        super().__init__()
        self.headings = []
        self.parts = None

    def handle_starttag(self, tag, attrs):
        if tag in {"h1", "h2"}:
            self.parts = []

    def handle_data(self, data):
        if self.parts is not None:
            self.parts.append(data)

    def handle_endtag(self, tag):
        if tag in {"h1", "h2"} and self.parts is not None:
            self.headings.append(" ".join("".join(self.parts).split()).casefold())
            self.parts = None


def arbeitnow_listing_is_missing(original_url, final_url, html):
    """Recognize Arbeitnow's own missing-page response, including HTTP 200."""
    hosts = {"arbeitnow.com", "www.arbeitnow.com"}
    if any(urlsplit(url).hostname not in hosts for url in (original_url, final_url)):
        return False
    if application_page_is_missing(final_url):
        return True
    parser = MissingPageHeadingParser()
    parser.feed(html)
    return "page not found" in parser.headings


def himalayas_listing_redirects_to_search(original_url, final_url):
    """A removed Himalayas detail page redirects to its general jobs index."""
    original = urlsplit(original_url)
    final = urlsplit(final_url)
    hosts = {"himalayas.app", "www.himalayas.app"}
    return (
        original.hostname in hosts and final.hostname in hosts
        and re.fullmatch(r"/companies/[^/]+/jobs/[^/]+/?", original.path) is not None
        and final.path.rstrip("/") == "/jobs"
    )


def listing_is_closed(url):
    """Return true only for a direct 404/410 or an explicit visible closure."""
    try:
        final_url, html = fetch_text_with_final_url(
            url, timeout=10, max_bytes=2 * 1024 * 1024,
            url_validator=validate_public_url,
        )
    except HTTPError as error:
        # A failed redirected login or another site's error is inconclusive.
        return error.code in {404, 410} and (
            urlsplit(error.url).hostname == urlsplit(url).hostname
            and urlsplit(error.url).path.rstrip("/") == urlsplit(url).path.rstrip("/")
        )
    except (OSError, ValueError):
        return False
    if arbeitnow_listing_is_missing(url, final_url, html):
        return True
    if himalayas_listing_redirects_to_search(url, final_url):
        return True
    parser = VisibleJobParser()
    parser.feed(html)
    if CLOSED_MESSAGE.fullmatch(" ".join(parser.title.split())):
        return True
    if extract_json_ld_job_posting(html):
        return False
    return any(CLOSED_MESSAGE.fullmatch(" ".join(text.split())) for text in parser.lines)


MAX_CHECK_URLS = 200
CHECK_BUDGET_SECONDS = 120
CHECK_INTERVAL = timedelta(hours=24)


def recent_check(check, now):
    """Treat malformed or future timestamps as due rather than trusting them."""
    try:
        age = now - datetime.fromisoformat(check["checked_at"])
        return timedelta(0) <= age < CHECK_INTERVAL
    except (KeyError, TypeError, ValueError):
        return False


def ignore_closed_listings(
    jobs, memory_path, *, successful_sources, progress=None,
    max_urls=MAX_CHECK_URLS, budget_seconds=CHECK_BUDGET_SECONDS, now=None,
):
    """Bound requests, retain inconclusive listings, and resume old checks later."""
    now = now or datetime.now(timezone.utc)
    successful = set(successful_sources)
    present_ids = {job.id for job in jobs if not job.cache_stale}
    snapshot = load_memory(memory_path)
    candidates = {}
    due = {}
    for job_id, entry in snapshot.items():
        status = entry.get("workflow_status")
        if status != "interesting" or has_application_state(entry):
            continue
        if job_id in present_ids:
            continue
        known_sources = set(entry.get("source_names") or inferred_sources(job_id))
        if not known_sources or not known_sources.issubset(successful):
            continue
        urls = entry.get("source_urls", [])
        if not isinstance(urls, list):
            continue
        urls = tuple(dict.fromkeys(url for url in urls if isinstance(url, str) and url))
        if not urls:
            continue
        checks = entry.get("availability_checks", {})
        if not isinstance(checks, dict):
            checks = {}
        candidates[job_id] = (entry, urls, checks)
        for url in urls:
            check = checks.get(url, {})
            if recent_check(check, now):
                continue
            # Never-checked/oldest URLs first; URL breaks ties.
            timestamp = str(check.get("checked_at", "")) if isinstance(check, dict) else ""
            priority = (timestamp, url)
            due[url] = min(due.get(url, priority), priority)
    selected = sorted(due, key=due.get)[:max(0, max_urls)]
    print(f"Offline-Prüfung: {len(due)} URLs fällig · höchstens {len(selected)} in diesem Lauf", flush=True)
    if progress is not None:
        progress(0, len(selected))
    checked_urls = {}
    started = time.monotonic()
    for url in selected:
        # No new request after the budget; an already running request may finish.
        if time.monotonic() - started >= budget_seconds:
            break
        checked_urls[url] = {
            "checked_at": now.isoformat(), "closed": listing_is_closed(url),
        }
        if progress is not None:
            progress(len(checked_urls), len(selected))
    print(f"Offline-Prüfung: {len(checked_urls)} URLs geprüft · "
          f"{len(due) - len(checked_urls)} zurückgestellt; Status bleibt erhalten", flush=True)
    if not checked_urls:
        return set()
    ignored = set()
    with edit_memory(memory_path) as memory:
        for job_id, (previous, urls, checks) in candidates.items():
            if not any(url in checked_urls for url in urls):
                continue
            entry = memory.get(job_id)
            # A concurrent user decision or worker update takes precedence.
            if entry != previous:
                continue
            updated = {url: checked_urls.get(url, checks.get(url, {})) for url in urls}
            entry["availability_checks"] = updated
            if not all(recent_check(check, now) and check.get("closed") is True
                       for check in updated.values()):
                continue
            record_status_change(entry, WorkflowStatus.IGNORED)
            entry["workflow_history"][-1]["reason"] = "listing_unavailable"
            entry["availability_checked_at"] = now.isoformat()
            entry["active"] = False
            ignored.add(job_id)
    for job in jobs:
        if job.id in ignored:
            job.workflow_status = WorkflowStatus.IGNORED
            job.is_new = False
    return ignored
