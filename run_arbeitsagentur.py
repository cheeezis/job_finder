import json
from pathlib import Path

from import_arbeitsagentur import fetch_job
from main import print_results, score_jobs
from search_arbeitsagentur import collect_links


LINKS_FILE = "job_links.txt"
JOBS_FILE = "jobs_imported.json"


def main():
    print("1/3 Suche Arbeitsagentur-Links")
    links = collect_links()
    Path(LINKS_FILE).write_text("\n".join(links) + "\n", encoding="utf-8")
    print(f"{len(links)} Link(s) gespeichert in {LINKS_FILE}")

    print("\n2/3 Importiere Jobdetails")
    jobs = import_jobs(links)
    Path(JOBS_FILE).write_text(
        json.dumps(jobs, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"{len(jobs)} Job(s) gespeichert in {JOBS_FILE}")

    print("\n3/3 Bewerte Jobs")
    results = score_jobs(jobs)
    print_results(results)


def import_jobs(links):
    jobs = []
    for url in links:
        try:
            jobs.append(fetch_job(url))
            print(f"OK: {url}")
        except Exception as error:
            print(f"FEHLER: {url}")
            print(f"       {error}")
    return jobs


if __name__ == "__main__":
    main()
