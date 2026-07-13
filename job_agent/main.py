import json
import sys
from pathlib import Path

from job_agent.deduplication import deduplicate_jobs
from job_agent.reporting import write_review_files
from job_agent.scoring import score_job


# Keep console output stable for German job titles and company names.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


def main():
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

    # Erlaubte und ausgeschlossene Jobs getrennt ausgeben, damit Filtergruende sichtbar bleiben.
    included = [job for job in results if job["status"] == "included"]
    excluded = [job for job in results if job["status"] == "excluded"]

    # Einstiegsstellen stehen immer vor Jobs mit hoeherer Erfahrungsanforderung.
    included.sort(key=lambda job: (job["experience_rank"], -job["match_percent"]))

    return {
        "included": included,
        "excluded": excluded,
    }


def print_results(results):
    included = results["included"]
    excluded = results["excluded"]

    print("PASSENDE JOBS")
    print("=" * 60)
    for job in included:
        new_marker = "NEU | " if job.get("is_new") else ""
        print(
            new_marker +
            f'{job["match_percent"]:>3}% | '
            f'{job["raw_score"]:>3} Punkte | '
            f'{job["title"]} | {job["company"]} | {job["location"]} | Remote: {job["remote"]}'
        )
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
    return json.loads(Path(path).read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()
