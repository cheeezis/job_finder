"""Command-line entry point for collecting, remembering, and scoring jobs."""

import json
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from job_agent.console import configure_utf8_output
from job_agent.main import print_results, score_jobs
from job_agent.memory import load_memory, save_memory, update_memory
from job_agent.reporting import write_review_files
from job_agent.sources import arbeitsagentur
from job_agent.sources import get_in_it
from job_agent.sources import stepstone


SOURCES = [
    arbeitsagentur,
    stepstone,
    get_in_it,
]

JOBS_FILE = "data/jobs_imported.json"
MEMORY_FILE = "data/seen_jobs.json"


def main():
    """Run the full pipeline: collect jobs, update memory, then score."""
    configure_utf8_output()
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
    write_review_files(results)
    print_results(results)


def collect_jobs():
    """Collect jobs from all configured sources and deduplicate by URL."""
    jobs = []
    seen_urls = set()

    for source in SOURCES:
        print(f"\nQuelle: {source.SOURCE_NAME}")
        source_jobs = source.fetch_jobs()
        print(f"{len(source_jobs)} Job(s) aus {source.SOURCE_NAME}")

        for job in source_jobs:
            url = job.get("url")
            dedupe_key = canonical_url(url)
            if dedupe_key in seen_urls:
                continue
            seen_urls.add(dedupe_key)
            jobs.append(job)

    return jobs


def canonical_url(url):
    """Remove query parameters and fragments used only for tracking."""
    parts = urlsplit(url or "")
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


if __name__ == "__main__":
    main()
