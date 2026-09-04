"""Cross-source duplicate detection for normalized job postings."""

import re
from collections import defaultdict
from dataclasses import replace

from job_finder.models import Job, WorkMode
from job_finder.text import normalize_text


LEGAL_FORMS = [
    "gmbh",
    "mbh",
    "ag",
    "se",
    "kg",
    "ohg",
    "ug",
    "co",
    "ltd",
    "inc",
]


def deduplicate_jobs(jobs: list[Job]) -> list[Job]:
    """Merge cross-source jobs only when title, company, and location agree."""
    unique_jobs = []
    positions_by_title = defaultdict(list)

    for original in jobs:
        job = clone_job(original)
        title_key = normalize_title(job.title)
        company_key = normalize_company(job.company)
        position = find_duplicate_position(
            job,
            company_key,
            positions_by_title.get(title_key, []),
            unique_jobs,
        )

        if position is None:
            if title_key and company_key:
                positions_by_title[title_key].append(len(unique_jobs))
            unique_jobs.append(job)
            continue

        existing = unique_jobs[position]
        merged = merge_jobs(existing, job)
        unique_jobs[position] = merged

    return unique_jobs


def find_duplicate_position(job, company_key, positions, unique_jobs):
    if not company_key:
        return None

    for position in positions:
        existing = unique_jobs[position]
        if set(job.source_names) & set(existing.source_names):
            continue
        existing_company = normalize_company(existing.company)
        if companies_match(company_key, existing_company) and (
            locations_match(job.locations, existing.locations)
            or both_fully_remote(job, existing)
        ):
            return position
    return None


def locations_match(first_locations, second_locations):
    """Require a shared normalized place before merging ambiguous portal ads."""
    first = {normalize_location(value) for value in first_locations}
    second = {normalize_location(value) for value in second_locations}
    first.discard("")
    second.discard("")
    if not first or not second:
        return False
    return any(
        left == right
        or (len(left) >= 5 and left in right)
        or (len(right) >= 5 and right in left)
        for left in first
        for right in second
    )


def both_fully_remote(first, second):
    """Ignore conflicting display locations for two fully remote postings."""
    return all(
        job.remote_percentage == 100 or job.work_mode is WorkMode.REMOTE
        for job in (first, second)
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
    return len(shorter) >= 5 and (
        longer.startswith(shorter + " ") or shorter in longer.split()
    )


def normalize_company(company):
    text = normalize_text(company)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    text = re.sub(r"^bei\s+", "", text)
    words = [word for word in text.split() if word not in LEGAL_FORMS]
    return " ".join(words)


def normalize_title(title):
    text = normalize_text(title)
    text = re.sub(r"\[[^]]*\]", " ", text)
    text = re.sub(
        r"\((?:m/w/d|w/m/d|m/f/d|f/m/d|all genders|alle geschlechter|gn)\)",
        " ",
        text,
    )
    text = re.sub(
        r"\b(?:m/w/d|w/m/d|m/f/d|f/m/d|all genders|alle geschlechter|gn)\b",
        " ",
        text,
    )
    text = re.sub(r"[^a-z0-9+#.]+", " ", text)
    return " ".join(text.split())


def merge_jobs(existing, duplicate):
    """Keep the richer posting and attach provenance from both sources."""
    existing_description = existing.description_clean
    duplicate_description = duplicate.description_clean
    richer = (
        duplicate
        if len(duplicate_description) > len(existing_description)
        else existing
    )
    sources = unique_sources(existing.sources + duplicate.sources)
    locations = list(dict.fromkeys(existing.locations + duplicate.locations))
    return replace(
        richer,
        id=existing.id,
        locations=locations,
        sources=sources,
        is_new=existing.is_new or duplicate.is_new,
        content_changed=existing.content_changed or duplicate.content_changed,
    )


def clone_job(job):
    """Copy mutable model fields before merging jobs."""
    return replace(
        job,
        locations=list(job.locations),
        sources=list(job.sources),
    )


def unique_sources(sources):
    """Return portal listings once, preserving source order."""
    result = []
    seen = set()
    for source in sources:
        key = (source.source, source.url)
        if key in seen:
            continue
        seen.add(key)
        result.append(source)
    return result
