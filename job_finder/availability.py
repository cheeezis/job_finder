"""Confirm closed shortlisted listings without treating search gaps as closure."""

import re
from datetime import datetime, timezone
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


def ignore_closed_listings(jobs, memory_path, *, successful_sources):
    """Check outside the write lock; ignore only unchanged, proven-closed jobs."""
    successful = set(successful_sources)
    present_ids = {job.id for job in jobs if not job.cache_stale}
    snapshot = load_memory(memory_path)
    confirmed = {}
    checked_urls = {}
    for job_id, entry in snapshot.items():
        status = entry.get("workflow_status")
        if status not in {"new", "interesting"} or has_application_state(entry):
            continue
        if job_id in present_ids:
            continue
        # Absence is meaningful only when every known source completed its run.
        known_sources = set(entry.get("source_names") or inferred_sources(job_id))
        if not known_sources or not known_sources.issubset(successful):
            continue
        urls = entry.get("source_urls", [])
        if not isinstance(urls, list):
            continue
        urls = tuple(dict.fromkeys(url for url in urls if isinstance(url, str) and url))
        if not urls:
            continue
        for url in urls:
            if url not in checked_urls:
                checked_urls[url] = listing_is_closed(url)
        if all(checked_urls[url] for url in urls):
            confirmed[job_id] = entry
    if not confirmed:
        return set()
    ignored = set()
    with edit_memory(memory_path) as memory:
        for job_id, previous in confirmed.items():
            entry = memory.get(job_id)
            # A user decision or concurrent finder update takes precedence.
            if entry != previous:
                continue
            record_status_change(entry, WorkflowStatus.IGNORED)
            entry["workflow_history"][-1]["reason"] = "listing_unavailable"
            entry["availability_checked_at"] = datetime.now(timezone.utc).isoformat()
            entry["active"] = False
            ignored.add(job_id)
    for job in jobs:
        if job.id in ignored:
            job.workflow_status = WorkflowStatus.IGNORED
            job.is_new = False
    return ignored
