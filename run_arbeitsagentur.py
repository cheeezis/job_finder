import json
from pathlib import Path

from job_agent.arbeitsagentur_import import fetch_job
from job_agent.arbeitsagentur_search import collect_links
from job_agent.main import print_results, score_jobs
from job_agent.memory import load_memory, save_memory, update_memory


LINKS_FILE = "data/job_links.txt"
JOBS_FILE = "data/jobs_imported.json"
MEMORY_FILE = "data/seen_jobs.json"


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

    print("\n3/4 Aktualisiere Job-Gedaechtnis")
    memory = load_memory(MEMORY_FILE)
    memory_stats = update_memory(jobs, memory)
    save_memory(memory, MEMORY_FILE)
    print(f'Neue Jobs: {memory_stats["new"]}')
    print(f'Bekannte Jobs: {memory_stats["known"]}')
    print(f"Gedaechtnis gespeichert in {MEMORY_FILE}")

    print("\n4/4 Bewerte Jobs")
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
