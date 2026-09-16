"""Confirm closed shortlisted listings without treating search gaps as closure."""

import re
import time
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from urllib.error import HTTPError
from urllib.parse import urlsplit

from job_finder.applications import record_status_change
from job_finder.http import fetch_text_with_final_url
from job_finder.memory import (
    edit_memory,
    has_application_state,
    load_memory,
    sources_succeeded,
)
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
        """Begin capturing an h1 or h2 heading in a possible error page."""
        if tag in {"h1", "h2"}:
            self.parts = []

    def handle_data(self, data):
        """Append visible text while a heading is being captured."""
        if self.parts is not None:
            self.parts.append(data)

    def handle_endtag(self, tag):
        """Store a completed heading in normalized form for closure checks."""
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
    """Recognize a Himalayas detail URL redirected to its jobs index."""
    original = urlsplit(original_url)
    final = urlsplit(final_url)
    hosts = {"himalayas.app", "www.himalayas.app"}
    return (
        original.hostname in hosts
        and final.hostname in hosts
        and re.fullmatch(r"/companies/[^/]+/jobs/[^/]+/?", original.path) is not None
        and final.path.rstrip("/") == "/jobs"
    )


def listing_is_closed(url):
    """Return True only when the response provides known closure evidence.

    Recognize direct 404/410 responses, supported portal redirects and
    explicit visible closure messages. False means closure was not
    confirmed; it also covers network failures or ambiguous responses
    and must not be interpreted as proof that a listing is open.
    """
    try:
        final_url, html = fetch_text_with_final_url(
            url,
            timeout=10,
            max_bytes=2 * 1024 * 1024,
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
    return any(
        CLOSED_MESSAGE.fullmatch(" ".join(text.split())) for text in parser.lines
    )


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
    jobs,
    memory_path,
    *,
    successful_sources,
    progress=None,
    max_urls=MAX_CHECK_URLS,
    budget_seconds=CHECK_BUDGET_SECONDS,
    now=None,
):
    """Check missing shortlisted jobs and persist confirmed closures.

    Consider interesting entries without application history only when
    every known source is in successful_sources. jobs supplies current
    sightings; stale cached sightings count as missing. Reuse recent
    per-URL checks, and apply max_urls and budget_seconds to new work.

    Network requests run outside database write transactions. Recheck
    the current workflow state before changing an entry to ignored so
    concurrent user decisions take precedence. All known URLs must be
    recently confirmed closed. Return the IDs changed by this call.
    progress, if supplied, receives completed and planned URL counts.
    """
    now = now or datetime.now(timezone.utc)
    snapshot = load_memory(memory_path)
    candidates, due = _plan_checks(jobs, snapshot, successful_sources, now)
    selected = sorted(due, key=due.get)[: max(0, max_urls)]
    all_urls = {url for _, urls, _ in candidates.values() for url in urls}
    print(
        f"  Offline: {len(due)} URLs fällig · {len(all_urls) - len(due)} im Prüfintervall · "
        f"höchstens {len(selected)} in diesem Lauf",
        flush=True,
    )
    checked_urls = _check_urls(selected, now, budget_seconds, progress)
    closed = sum(check["closed"] is True for check in checked_urls.values())
    print(
        f"  Offline: {len(checked_urls)} URLs geprüft · {closed} geschlossen · "
        f"{len(checked_urls) - closed} nicht bestätigt · "
        f"{len(due) - len(checked_urls)} zurückgestellt",
        flush=True,
    )
    if not checked_urls:
        return set()
    ignored = _save_checks(candidates, checked_urls, memory_path, now)
    for job in jobs:
        if job.id in ignored:
            job.workflow_status = WorkflowStatus.IGNORED
            job.is_new = False
    return ignored


def _plan_checks(jobs, snapshot, successful_sources, now):
    """Select missing shortlisted entries and prioritize their due URLs."""
    successful = set(successful_sources)
    present_ids = {job.id for job in jobs if not job.cache_stale}
    candidates = {}
    due = {}
    for job_id, entry in snapshot.items():
        status = entry.get("workflow_status")
        if status != "interesting" or has_application_state(entry):
            continue
        if job_id in present_ids:
            continue
        if not sources_succeeded(job_id, entry, successful):
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
            timestamp = (
                str(check.get("checked_at", "")) if isinstance(check, dict) else ""
            )
            priority = (timestamp, url)
            due[url] = min(due.get(url, priority), priority)
    return candidates, due


def _check_urls(selected, now, budget_seconds, progress):
    """Check URLs within the time budget, outside any database transaction."""
    if progress is not None:
        progress(0, len(selected))
    checked_urls = {}
    started = time.monotonic()
    for url in selected:
        # No new request after the budget; an already running request may finish.
        if time.monotonic() - started >= budget_seconds:
            break
        checked_urls[url] = {
            "checked_at": now.isoformat(),
            "closed": listing_is_closed(url),
        }
        if progress is not None:
            progress(len(checked_urls), len(selected))
    return checked_urls


def _save_checks(candidates, checked_urls, memory_path, now):
    """Persist checks only for unchanged entries and return confirmed closures."""
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
            if not all(
                recent_check(check, now) and check.get("closed") is True
                for check in updated.values()
            ):
                continue
            record_status_change(entry, WorkflowStatus.IGNORED)
            entry["workflow_history"][-1]["reason"] = "listing_unavailable"
            entry["availability_checked_at"] = now.isoformat()
            entry["active"] = False
            ignored.add(job_id)
    return ignored
