"""Score jobs and assemble the sorted result views."""

from dataclasses import replace

from job_finder.matching.deduplication import unique_sources
from job_finder.matching.scoring import score_job
from job_finder.models import FilterStatus, Job
from job_finder.persistence.storage import read_json


def score_jobs(jobs):
    """Evaluate jobs and return their sorted output views."""
    return build_score_results(evaluate_jobs(jobs))


def evaluate_jobs(jobs):
    """Keep each pure score attached to its job while memory adds metadata."""
    return [(job, score_for_pipeline(job)) for job in jobs]


def combine_listings(evaluated_jobs):
    """Turn the listings memory gave one ID into one card.

    The best-rated listing leads with its own text and score; the others
    add their places and links, so every way to the ad stays on the card.
    """
    cards = {}
    for job, result in evaluated_jobs:
        if job.id not in cards:
            cards[job.id] = (job, result)
            continue
        # sorted() is stable: on a tie the listing seen first keeps the lead.
        (lead, lead_result), (other, _) = sorted(
            [cards[job.id], (job, result)], key=lambda card: card_rank(card[1])
        )
        cards[job.id] = (join_listings(lead, other), lead_result)
    return list(cards.values())


def card_rank(result):
    """Order listings of one job: included first, then by score and entry level."""
    return (
        result["filter_status"] != FilterStatus.INCLUDED.value,
        -result["match_percent"],
        result["experience_rank"],
    )


def join_listings(lead, other):
    """Add the places and links of another listing to the leading one."""
    return replace(
        lead,
        locations=list(dict.fromkeys(lead.locations + other.locations)),
        sources=unique_sources(lead.sources + other.sources),
        is_new=lead.is_new or other.is_new,
    )


def build_score_results(evaluated_jobs):
    """Serialize current job metadata with its already validated score."""
    # Excluded jobs keep their reasons for console diagnostics and notifications.
    results = {FilterStatus.INCLUDED.value: [], FilterStatus.EXCLUDED.value: []}
    for job, result in evaluated_jobs:
        results[result["filter_status"]].append({**job.to_dict(), "is_new": job.is_new, **result})
    results["included"].sort(
        key=lambda job: (-job["match_percent"], job["experience_rank"], job["title"].lower())
    )
    return results


def score_for_pipeline(job):
    """Keep explicit manual submissions reviewable without weakening searches."""
    result = score_job(job)
    if result["filter_status"] != FilterStatus.EXCLUDED.value or "manual" not in job.source_names:
        return result

    warning = result["reasons"][0]
    location_conflict = "Ort/Remote" in warning
    return {
        "filter_status": FilterStatus.INCLUDED.value,
        "match_percent": 0,
        "experience_rank": 99,
        "experience_level": "manuell zur Prüfung eingereicht",
        "role_group": "manual_review",
        "location_precheck": (f"Konflikt: {warning}" if location_conflict else "Manuelle Prüfung"),
        "reasons": [f"Manuell geprüft trotz Vorfilter: {warning}"],
        "prefilter_warning": warning,
    }


def load_jobs(path):
    """Load jobs from the stored jobs dataset or an explicit JSON import file."""
    values = read_json(path, [])
    try:
        return [Job.from_dict(job) for job in values]
    except KeyError as error:
        raise ValueError(
            "Importdatei verwendet das alte Jobformat; zuerst einen neuen "
            "vollstaendigen Lauf starten"
        ) from error
