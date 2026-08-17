"""Direct EDAG career-page source."""

import re
from html import unescape

from job_finder.http import fetch_text
from job_finder.models import Job, JobSource
from job_finder.paths import EDAG_CACHE_FILE
from job_finder.remote import classify_remote, detect_remote
from job_finder.sources.common import normalize_employment_type, source_job_id, utc_now
from job_finder.sources.company_careers import (
    extract_links,
    fetch_company_jobs,
    identifier_from_url,
)
from job_finder.text import compact_text, html_to_text, normalize_text

SOURCE_NAME = "edag"
COMPANY = "EDAG Engineering GmbH"
LIST_URL = "https://www.edag.com/de/karriere/stellenanzeigen"
CACHE_FILE = EDAG_CACHE_FILE

CAREER_LEVELS = {
    "professionals",
    "studierende",
    "absolventen",
    "schueler",
    "fuehrungskraefte",
}


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
    first_html = fetch_text(LIST_URL)
    pages = [
        int(value)
        for value in re.findall(r"currentPage(?:%5D|\])=(\d+)", first_html)
    ]
    last_page = max(pages, default=1)
    links = []
    seen = set()

    for page in range(1, last_page + 1):
        html = first_html if page == 1 else fetch_text(
            f"{LIST_URL}?tx_successfactors_view%5BcurrentPage%5D={page}"
        )
        for url in extract_local_links(html):
            if url not in seen:
                seen.add(url)
                links.append(url)
    return links


def extract_local_links(html):
    """Keep cards that explicitly include Fulda or an unknown site set."""
    links = []
    for match in re.finditer(
        r'<a[^>]*class="[^"]*sfjob[^"]*"[^>]*href="([^"]+)"[^>]*>'
        r"(.*?)</a>",
        html,
        re.IGNORECASE | re.DOTALL,
    ):
        card_text = normalize_text(html_to_text(unescape(match.group(2))))
        if "fulda" not in card_text and "mehrere standorte" not in card_text:
            continue
        links.extend(
            extract_links(
                f'<a href="{match.group(1)}">',
                LIST_URL,
                r"edag\.com/de/karriere/stellenanzeigen/detail/.+-\d+$",
            )
        )
    return links


def job_from_html(source_name, fallback_company, url, html):
    """Parse EDAG's visible detail block; the site has no JobPosting JSON-LD."""
    title = extract_text(html, r'<h2[^>]*class="[^"]*title[^"]*"[^>]*>(.*?)</h2>')
    if not title:
        title = extract_text(html, r"<title[^>]*>(.*?)</title>")
        title = re.sub(r"\s+-\s+EDAG Group$", "", title).strip()
    if not title:
        raise ValueError("EDAG-Stellentitel nicht gefunden")

    teaser = extract_html(html, r'<div[^>]*class="[^"]*teaser[^"]*"[^>]*>(.*?)</div>')
    description = extract_html(
        html,
        r'<div[^>]*class="[^"]*description[^"]*"[^>]*>(.*?)'
        r'</div>\s*</div>\s*</div>\s*<div[^>]*class="[^"]*actions',
    )
    raw_description = " ".join(part for part in [teaser, description] if part)
    clean_description = html_to_text(raw_description)
    facts = extract_facts(html)
    company = facts[0] if facts else fallback_company
    employment = next(
        (fact for fact in facts if "vollzeit" in normalize_text(fact) or "teilzeit" in normalize_text(fact)),
        None,
    )
    locations = [
        fact
        for fact in facts[1:]
        if is_location_fact(fact, employment)
    ] or ["unbekannt"]
    structured_remote = "homeoffice" if any("hybrid" in normalize_text(fact) for fact in facts) else ""
    remote = detect_remote(
        title,
        clean_description,
        ", ".join(locations),
        structured_remote=structured_remote,
    )
    work_mode, remote_percentage = classify_remote(remote)
    identifier = identifier_from_url(url)

    return Job(
        id=source_job_id(source_name, identifier, url),
        title=title,
        company=company,
        locations=locations,
        sources=[JobSource(source=source_name, source_id=identifier, url=url)],
        description_raw=raw_description,
        description_clean=clean_description,
        work_mode=work_mode,
        remote_percentage=remote_percentage,
        employment_type=normalize_employment_type(employment),
        career_levels=[
            fact for fact in facts if normalize_text(fact) in CAREER_LEVELS
        ],
        fetched_at=utc_now(),
    )


def extract_facts(html):
    match = re.search(
        r'<div[^>]*class="[^"]*short-facts[^"]*"[^>]*>(.*?)'
        r'<div[^>]*class="[^"]*breadcrumb',
        html,
        re.IGNORECASE | re.DOTALL,
    )
    if not match:
        return []
    facts = re.findall(r"<span[^>]*>(.*?)</span>", match.group(1), re.IGNORECASE | re.DOTALL)
    return [
        compact_text(html_to_text(unescape(fact)))
        for fact in facts
        if compact_text(html_to_text(unescape(fact)))
    ]


def is_location_fact(fact, employment):
    normalized = normalize_text(fact)
    if fact == employment or "hybrid" in normalized:
        return False
    return normalized not in CAREER_LEVELS


def extract_text(html, pattern):
    return compact_text(html_to_text(unescape(extract_html(html, pattern))))


def extract_html(html, pattern):
    match = re.search(pattern, html, re.IGNORECASE | re.DOTALL)
    return match.group(1).strip() if match else ""
