"""Score imported jobs without running the source searches again."""

import json
import sys
from pathlib import Path

from job_agent.console import configure_utf8_output
from job_agent.deduplication import deduplicate_jobs
from job_agent.models import FilterStatus, Job
from job_agent.paths import JOBS_FILE
from job_agent.reporting import format_locations, format_remote
from job_agent.scoring import score_job


def main():
    """Score an existing import file and write review output."""
    configure_utf8_output()
    jobs_file = sys.argv[1] if len(sys.argv) > 1 else JOBS_FILE
    jobs = load_jobs(jobs_file)
    results = score_jobs(jobs)
    print_results(results)


def score_jobs(jobs):
    """Score imported jobs and split them into included/excluded buckets."""
    results = []

    # Quellenuebergreifende Duplikate sollen nur einmal im Review auftauchen.
    for job in deduplicate_jobs(jobs):
        result = score_job(job)
        job.filter_status = FilterStatus(result["filter_status"])
        job.rule_score = result["match_percent"]
        job.score_reasons = list(result["reasons"])
        results.append(
            {
                **job.to_dict(),
                "is_new": job.is_new,
                "content_changed": job.content_changed,
                **result,
            }
        )

    # Separate buckets keep hard-filter reasons visible in the review.
    included = [
        job
        for job in results
        if job["filter_status"] == FilterStatus.INCLUDED.value
    ]
    excluded = [
        job
        for job in results
        if job["filter_status"] == FilterStatus.EXCLUDED.value
    ]

    included.sort(
        key=lambda job: (
            -job["match_percent"],
            job["experience_rank"],
            job["title"].lower(),
        )
    )

    return {
        "included": included,
        "excluded": excluded,
    }


def print_results(results):
    """Print included jobs and a short excluded-job preview."""
    included = results["included"]
    excluded = results["excluded"]

    print("PASSENDE JOBS")
    print("=" * 60)
    for job in included:
        new_marker = "NEU | " if job.get("is_new") else ""
        summary = (
            f'{job["match_percent"]:>3}% | '
            f'{job["raw_score"]:>3} Punkte | '
            f'{job["title"]} | {job["company"]} | '
            f'{format_locations(job)} | Remote: {format_remote(job)}'
        )
        print(new_marker + summary)
        for reason in job["reasons"]:
            print(f"      - {reason}")
        print()

    print(f"AUSGESCHLOSSENE JOBS: {len(excluded)}")
    print("=" * 60)
    for job in excluded[:30]:
        print(f'{job["title"]} | {job["company"]} | {format_locations(job)}')
        print(f'      - {job["reasons"][0]}')
        print()

    if len(excluded) > 30:
        print(f"... {len(excluded) - 30} weitere ausgeschlossen")


def load_jobs(path):
    """Load imported jobs from a UTF-8 JSON file."""
    values = json.loads(Path(path).read_text(encoding="utf-8"))
    try:
        return [Job.from_dict(job) for job in values]
    except KeyError as error:
        raise ValueError(
            "Importdatei verwendet das alte Jobformat; zuerst einen neuen "
            "vollstaendigen Lauf starten"
        ) from error


if __name__ == "__main__":
    main()
