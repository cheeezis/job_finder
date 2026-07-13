"""Cross-source duplicate detection for normalized job postings."""

import re
from collections import defaultdict

from job_agent.text import normalize_text


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


def deduplicate_jobs(jobs):
    """Merge exact normalized title/company duplicates across sources."""
    unique_jobs = []
    positions_by_title = defaultdict(list)

    for original in jobs:
        job = dict(original)
        title_key = normalize_title(job.get("title", ""))
        company_key = normalize_company(job.get("company", ""))
        position = find_duplicate_position(
            job,
            company_key,
            positions_by_title.get(title_key, []),
            unique_jobs,
        )

        if position is None:
            job["sources"] = source_names(job)
            job["duplicate_urls"] = []
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
        if job.get("source") in existing.get("sources", []):
            continue
        existing_company = normalize_company(existing.get("company", ""))
        if companies_match(company_key, existing_company):
            return position
    return None


def companies_match(first, second):
    if first == second:
        return True
    shorter, longer = sorted([first, second], key=len)
    return len(shorter) >= 5 and (longer.startswith(shorter + " ") or shorter in longer.split())


def normalize_company(company):
    text = normalize_text(company)
    text = re.sub(r"[^a-z0-9]+", " ", text)
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
    text = re.sub(r"\b(?:m/w/d|w/m/d|m/f/d|f/m/d|all genders|alle geschlechter|gn)\b", " ", text)
    text = re.sub(r"[^a-z0-9+#.]+", " ", text)
    return " ".join(text.split())


def source_names(job):
    sources = job.get("sources") or [job.get("source", "")]
    return [source for source in sources if source]


def merge_jobs(existing, duplicate):
    """Keep the richer posting and attach provenance from both sources."""
    richer = duplicate if len(duplicate.get("description", "")) > len(existing.get("description", "")) else existing
    merged = dict(richer)
    merged["sources"] = unique_values(source_names(existing) + source_names(duplicate))

    urls = existing.get("duplicate_urls", []) + duplicate.get("duplicate_urls", [])
    for job in [existing, duplicate]:
        url = job.get("url")
        if url and url != merged.get("url"):
            urls.append(url)
    merged["duplicate_urls"] = unique_values(urls)
    merged["is_new"] = bool(existing.get("is_new") or duplicate.get("is_new"))
    return merged


def unique_values(values):
    result = []
    seen = set()
    for value in values:
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result
