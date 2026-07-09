import json
import sys
from pathlib import Path

from job_agent.scoring import score_job


def main():
    jobs_file = sys.argv[1] if len(sys.argv) > 1 else "data/jobs_imported.json"
    jobs = load_jobs(jobs_file)
    results = score_jobs(jobs)
    print_results(results)


def score_jobs(jobs):
    results = []

    # Jeden Job einzeln bewerten und das Scoring-Ergebnis an die Jobdaten haengen.
    for job in jobs:
        result = score_job(job)
        results.append({**job, **result})

    # Erlaubte und ausgeschlossene Jobs getrennt ausgeben, damit Filtergruende sichtbar bleiben.
    included = [job for job in results if job["status"] == "included"]
    excluded = [job for job in results if job["status"] == "excluded"]

    # Sortierung basiert auf echten Rohpunkten, nicht auf der spaeteren Prozentanzeige.
    included.sort(key=lambda job: job["raw_score"], reverse=True)
    add_match_percent(included)

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

    print("AUSGESCHLOSSENE JOBS")
    print("=" * 60)
    for job in excluded:
        print(f'{job["title"]} | {job["company"]} | {job["location"]}')
        print(f'      - {job["reasons"][0]}')
        print()


def load_jobs(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def add_match_percent(jobs):
    # Prozentwerte sind relativ zum besten Job im aktuellen Lauf.
    # Dadurch bleibt raw_score unbegrenzt, waehrend die Anzeige lesbar bleibt.
    if not jobs:
        return

    best_score = jobs[0]["raw_score"]
    for job in jobs:
        job["match_percent"] = round(job["raw_score"] / best_score * 100)


if __name__ == "__main__":
    main()
