import json
from pathlib import Path

from scoring import score_job


def main():
    jobs = load_jobs("jobs_sample.json")
    results = []

    for job in jobs:
        result = score_job(job)
        results.append({**job, **result})

    included = [job for job in results if job["status"] == "included"]
    excluded = [job for job in results if job["status"] == "excluded"]

    included.sort(key=lambda job: job["score"], reverse=True)

    print("PASSENDE JOBS")
    print("=" * 60)
    for job in included:
        print(f'{job["score"]:>3}% | {job["title"]} | {job["company"]} | {job["location"]} | Remote: {job["remote"]}')
        for reason in job["reasons"]:
            print(f"      - {reason}")
        print()

    print("AUSGESCHLOSSENE JOBS")
    print("=" * 60)
    for job in excluded:
        print(f'{job["title"]} | {job["company"]} | {job["location"]}')
        print(f'      - {job["reasons"][0]}')
        print()


def load_jobs(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()
