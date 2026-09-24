"""Score jobs and assemble the sorted result views."""

from job_finder.matching.scoring import score_job
from job_finder.models import FilterStatus, Job
from job_finder.persistence.storage import read_json


def score_jobs(jobs):
    """Evaluate jobs and return their sorted output views."""
    return build_score_results(evaluate_jobs(jobs))


def evaluate_jobs(jobs):
    """Keep each pure score attached to its job while memory adds metadata."""
    return [(job, score_for_pipeline(job)) for job in jobs]


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
    """Load imported jobs from a UTF-8 JSON file."""
    values = read_json(path, [])
    try:
        return [Job.from_dict(job) for job in values]
    except KeyError as error:
        raise ValueError(
            "Importdatei verwendet das alte Jobformat; zuerst einen neuen "
            "vollstaendigen Lauf starten"
        ) from error
