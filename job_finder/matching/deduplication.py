"""Cross-source duplicate detection for normalized job postings."""

import re
from collections import defaultdict
from dataclasses import replace

from job_finder.models import Job, WorkMode
from job_finder.text import normalize_text

LEGAL_FORMS = ["gmbh", "mbh", "ag", "se", "kg", "ohg", "ug", "co", "ltd", "inc"]
GENDER_LABEL = r"(?:m/w/d|w/m/d|m/f/d|f/m/d|all genders|alle geschlechter|gn)"

WORK_MODE_TITLE_SUFFIX = re.compile(
    r"(?:\s*[-–—|]\s*|\s+\()"
    r"(?:"
    r"(?:100\s*%\s*)?(?:full(?:y)?\s+|vollstaendig\s+)?remote"
    r"|home\s*office|homeoffice"
    r"|hybrid(?:\s+work)?"
    r")\)?\s*$"
)


def deduplicate_jobs(jobs: list[Job]) -> list[Job]:
    """Merge matching cross-source listings into copied Job objects.

    Titles and companies must match after normalization. Locations must
    overlap unless both listings are fully remote. Listings from the
    same source are kept separate. The input list and its jobs are not
    modified; the result retains source links from merged duplicates.
    """
    unique_jobs = []
    positions_by_title = defaultdict(list)

    for original in jobs:
        job = clone_job(original)
        title_key = normalize_title(job.title)
        company_key = normalize_company(job.company)
        position = find_duplicate_position(
            job, company_key, positions_by_title.get(title_key, []), unique_jobs
        )

        if position is None:
            if title_key and company_key:
                positions_by_title[title_key].append(len(unique_jobs))
            unique_jobs.append(job)
            continue

        unique_jobs[position] = merge_jobs(unique_jobs[position], job)

    return unique_jobs


def find_duplicate_position(job, company_key, positions, unique_jobs):
    """Return a compatible cross-source index, or None if none matches."""
    if not company_key:
        return None

    for position in positions:
        existing = unique_jobs[position]
        if set(job.source_names) & set(existing.source_names):
            continue
        if companies_match(company_key, normalize_company(existing.company)) and (
            locations_match(job.locations, existing.locations) or both_fully_remote(job, existing)
        ):
            return position
    return None


def locations_match(first_locations, second_locations):
    """Require a shared normalized place before merging ambiguous portal ads."""
    first = {normalize_location(value) for value in first_locations} - {""}
    second = {normalize_location(value) for value in second_locations} - {""}
    if not first or not second:
        return False
    return any(
        left == right or (len(left) >= 5 and left in right) or (len(right) >= 5 and right in left)
        for left in first
        for right in second
    )


def both_fully_remote(first, second):
    """Ignore conflicting display locations for two fully remote postings."""
    return all(
        job.remote_percentage == 100 or job.work_mode is WorkMode.REMOTE for job in (first, second)
    )


def normalize_location(value):
    """Normalize a location without erasing city-level identity."""
    text = normalize_text(value)
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text).split())


def companies_match(first, second):
    """Return whether normalized names identify the same company."""
    if first == second:
        return True
    shorter, longer = sorted([first, second], key=len)
    return len(shorter) >= 5 and (longer.startswith(shorter + " ") or shorter in longer.split())


def normalize_company(company):
    """Remove legal forms and punctuation for company-name comparison."""
    text = normalize_text(company)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    text = re.sub(r"^bei\s+", "", text)
    return " ".join(word for word in text.split() if word not in LEGAL_FORMS)


def normalize_title(title):
    """Remove gender labels and work-mode suffixes for title comparison."""
    text = normalize_text(title)
    text = re.sub(r"\[[^]]*\]", " ", text)
    # A bare label never contains "(", so it cannot overlap a parenthesized one.
    text = re.sub(rf"\({GENDER_LABEL}\)|\b{GENDER_LABEL}\b", " ", text)
    # Portals often append the work model to the title although location and
    # remote compatibility are checked independently before a merge.
    text = WORK_MODE_TITLE_SUFFIX.sub(" ", text)
    text = re.sub(r"[^a-z0-9+#.]+", " ", text)
    return " ".join(text.split())


def merge_jobs(existing, duplicate):
    """Keep the richer posting and attach provenance from both sources."""
    # max() keeps the first of equally long descriptions, i.e. the existing one.
    richer = max(existing, duplicate, key=lambda job: len(job.description_clean))
    return replace(
        richer,
        id=existing.id,
        locations=list(dict.fromkeys(existing.locations + duplicate.locations)),
        sources=unique_sources(existing.sources + duplicate.sources),
        is_new=existing.is_new or duplicate.is_new,
    )


def clone_job(job):
    """Copy mutable model fields before merging jobs."""
    return replace(job, locations=list(job.locations), sources=list(job.sources))


def unique_sources(sources):
    """Return portal listings once, preserving source order."""
    result = {}
    for source in sources:
        result.setdefault((source.source, source.url), source)
    return list(result.values())
