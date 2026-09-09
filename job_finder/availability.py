"""Confirm closed shortlisted listings without treating search gaps as closure."""

import re
from datetime import datetime, timezone
from urllib.error import HTTPError
from urllib.parse import urlsplit

from job_finder.applications import record_status_change
from job_finder.http import fetch_text_with_final_url
from job_finder.memory import edit_memory, has_application_state, load_memory
from job_finder.models import WorkflowStatus
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
    parser = VisibleJobParser()
    parser.feed(html)
    if CLOSED_MESSAGE.fullmatch(" ".join(parser.title.split())):
        return True
    if extract_json_ld_job_posting(html):
        return False
    return any(CLOSED_MESSAGE.fullmatch(" ".join(text.split())) for text in parser.lines)


def ignore_closed_listings(jobs, memory_path):
    """Check outside the write lock; ignore only unchanged, proven-closed jobs."""
    present_ids = {job.id for job in jobs if not job.cache_stale}
    snapshot = load_memory(memory_path)
    confirmed = {}
    checked_urls = {}
    for job_id, entry in snapshot.items():
        status = entry.get("workflow_status")
        if status not in {"new", "interesting"} or has_application_state(entry):
            continue
        if status == "new" and job_id in present_ids:
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
