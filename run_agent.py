import json
from pathlib import Path

from job_agent.main import print_results, score_jobs
from job_agent.memory import load_memory, save_memory, update_memory
from job_agent.sources import arbeitsagentur
from job_agent.sources import stepstone


SOURCES = [
    arbeitsagentur,
    stepstone,
]

JOBS_FILE = "data/jobs_imported.json"
MEMORY_FILE = "data/seen_jobs.json"


def main():
    print("1/3 Sammle Jobs aus Quellen")
    jobs = collect_jobs()
    Path(JOBS_FILE).write_text(
        json.dumps(jobs, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"{len(jobs)} Job(s) gespeichert in {JOBS_FILE}")

    print("\n2/3 Aktualisiere Job-Gedaechtnis")
    memory = load_memory(MEMORY_FILE)
    memory_stats = update_memory(jobs, memory)
    save_memory(memory, MEMORY_FILE)
    print(f'Neue Jobs: {memory_stats["new"]}')
    print(f'Bekannte Jobs: {memory_stats["known"]}')
    print(f"Gedaechtnis gespeichert in {MEMORY_FILE}")

    print("\n3/3 Bewerte Jobs")
    results = score_jobs(jobs)
    print_results(results)


def collect_jobs():
    jobs = []
    seen_urls = set()

    for source in SOURCES:
        print(f"\nQuelle: {source.SOURCE_NAME}")
        source_jobs = source.fetch_jobs()
        print(f"{len(source_jobs)} Job(s) aus {source.SOURCE_NAME}")

        for job in source_jobs:
            url = job.get("url")
            if url in seen_urls:
                continue
            seen_urls.add(url)
            jobs.append(job)

    return jobs


if __name__ == "__main__":
    main()
