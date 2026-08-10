"""Direct Compose IT career-page source for Fulda."""

import re
from html import unescape

from job_agent.http import fetch_text
from job_agent.models import Job, JobSource
from job_agent.paths import COMPOSE_IT_CACHE_FILE
from job_agent.remote import classify_remote, detect_remote
from job_agent.sources.common import normalize_employment_type, source_job_id, utc_now
from job_agent.sources.company_careers import (
    extract_links,
    fetch_company_jobs,
    identifier_from_url,
)
from job_agent.text import compact_text, html_to_text, normalize_text

SOURCE_NAME = "compose_it"
COMPANY = "COMPOSE IT"
LIST_URL = "https://compose-it.de/unternehmen/karriere/"
CACHE_FILE = COMPOSE_IT_CACHE_FILE


def fetch_jobs(cache_path=CACHE_FILE, now=None):
    links = collect_links()
    return fetch_company_jobs(
        SOURCE_NAME,
        COMPANY,
        links,
        cache_path,
        now=now,
        parser=job_from_html,
    )


def collect_links():
    html = fetch_text(LIST_URL)
    return extract_links(html, LIST_URL, r"compose-it\.de/job/[^/?#]+/$")


def job_from_html(source_name, fallback_company, url, html):
    """Parse Compose IT's visible Elementor job content."""
    title_match = re.search(r"<h1[^>]*>(.*?)</h1>", html, re.IGNORECASE | re.DOTALL)
    if not title_match:
        raise ValueError("Compose-IT-Stellentitel nicht gefunden")
    title = compact_text(html_to_text(unescape(title_match.group(1))))

    header = html[max(0, title_match.start() - 5000) : title_match.start()]
    facts = [
        compact_text(html_to_text(unescape(value)))
        for value in re.findall(
            r'<span[^>]*class="[^"]*elementor-icon-list-text[^"]*"[^>]*>'
            r"(.*?)</span>",
            header,
            re.IGNORECASE | re.DOTALL,
        )
    ]
    locations = [fact for fact in facts if "fulda" in normalize_text(fact)]
    if not locations:
        raise ValueError("Compose-IT-Standort nicht gefunden")
    employment = next(
        (
            fact
            for fact in facts
            if any(
                word in normalize_text(fact)
                for word in ["festanstellung", "vollzeit", "teilzeit", "ausbildung", "werkstudent"]
            )
        ),
        None,
    )

    content_match = re.search(
        r'<div[^>]*data-elementor-type="wp-post"[^>]*>',
        html,
        re.IGNORECASE,
    )
    form_match = re.search(r'<div[^>]*id="bewerberform"[^>]*>', html, re.IGNORECASE)
    if not content_match or not form_match or form_match.start() <= content_match.start():
        raise ValueError("Compose-IT-Stellenbeschreibung nicht gefunden")
    raw_description = html[content_match.start() : form_match.start()].strip()
    description = html_to_text(raw_description)
    if not description:
        raise ValueError("Compose-IT-Stellenbeschreibung ist leer")

    remote = detect_remote(title, description, ", ".join(locations))
    work_mode, remote_percentage = classify_remote(remote)
    identifier = identifier_from_url(url)

    return Job(
        id=source_job_id(source_name, identifier, url),
        title=title,
        company=fallback_company,
        locations=locations,
        sources=[JobSource(source=source_name, source_id=identifier, url=url)],
        description_raw=raw_description,
        description_clean=description,
        work_mode=work_mode,
        remote_percentage=remote_percentage,
        employment_type=normalize_employment_type(employment),
        fetched_at=utc_now(),
    )
