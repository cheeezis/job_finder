"""Score imported jobs without running the source searches again."""

import json
import sys
from pathlib import Path

from job_agent.console import configure_utf8_output
from job_agent.deduplication import deduplicate_jobs
from job_agent.reporting import write_review_files
from job_agent.scoring import score_job


def main():
    """Score an existing import file and write review output."""
    configure_utf8_output()
    jobs_file = sys.argv[1] if len(sys.argv) > 1 else "data/jobs_imported.json"
    jobs = load_jobs(jobs_file)
    results = score_jobs(jobs)
    write_review_files(results)
    print_results(results)


def score_jobs(jobs):
    """Score imported jobs and split them into included/excluded buckets."""
    results = []

    # Quellenuebergreifende Duplikate sollen nur einmal im Review auftauchen.
    for job in deduplicate_jobs(jobs):
        result = score_job(job)
        results.append({**job, **result})

    # Separate buckets keep hard-filter reasons visible in the review.
    included = [job for job in results if job["status"] == "included"]
    excluded = [job for job in results if job["status"] == "excluded"]

    included.sort(
        key=lambda job: (
            -job["match_percent"],
            job["experience_rank"],
            job.get("title", "").lower(),
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
            f'{job["location"]} | Remote: {job["remote"]}'
        )
        print(new_marker + summary)
        for reason in job["reasons"]:
            print(f"      - {reason}")
        print()

    print(f"AUSGESCHLOSSENE JOBS: {len(excluded)}")
    print("=" * 60)
    for job in excluded[:30]:
        print(f'{job["title"]} | {job["company"]} | {job["location"]}')
        print(f'      - {job["reasons"][0]}')
        print()

    if len(excluded) > 30:
        print(f"... {len(excluded) - 30} weitere in data/jobs_scored.json")


def load_jobs(path):
    """Load imported jobs from a UTF-8 JSON file."""
    return json.loads(Path(path).read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()
