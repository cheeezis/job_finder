"""Command-line entry point for collecting, remembering, and scoring jobs."""

import argparse
import json
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from job_agent.console import configure_utf8_output
from job_agent.deduplication import deduplicate_jobs
from job_agent.main import print_results, score_jobs
from job_agent.llm.service import analyze_results
from job_agent.memory import MEMORY_FILE, load_memory, save_memory, update_memory
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


def parse_args():
    """Parse explicit options for potentially billable LLM analysis."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--llm",
        action="store_true",
        help="Analyze new included jobs with the configured OpenAI model",
    )
    parser.add_argument(
        "--llm-limit",
        type=int,
        help="Analyze at most N eligible jobs in this run",
    )
    return parser.parse_args()


def main():
    """Run the full pipeline: collect jobs, update memory, then score."""
    configure_utf8_output()
    args = parse_args()
    print("1/3 Sammle Jobs aus Quellen")
    jobs = collect_jobs()

    print("\n2/3 Aktualisiere Job-Gedaechtnis")
    memory = load_memory(MEMORY_FILE)
    memory_stats = update_memory(jobs, memory)
    save_memory(memory, MEMORY_FILE)
    Path(JOBS_FILE).write_text(
        json.dumps([job.to_dict() for job in jobs], indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"{len(jobs)} Job(s) gespeichert in {JOBS_FILE}")
    print(f'Neue Jobs: {memory_stats["new"]}')
    print(f'Bekannte Jobs: {memory_stats["known"]}')
    print(f"Gedaechtnis gespeichert in {MEMORY_FILE}")

    print("\n3/3 Bewerte Jobs")
    results = score_jobs(jobs)
    if args.llm:
        print("\nKI-Bewertung")
        llm_stats = analyze_results(
            results,
            limit=args.llm_limit,
        )
        print(
            f"KI: {llm_stats['analyzed']} neu analysiert, "
            f"{llm_stats['cached']} aus Cache, "
            f"{llm_stats['failed']} fehlgeschlagen"
        )
    write_review_files(results)
    print_results(results)


def collect_jobs():
    """Collect jobs from all configured sources and merge duplicates."""
    jobs = []
    seen_urls = set()

    for source in SOURCES:
        print(f"\nQuelle: {source.SOURCE_NAME}")
        source_jobs = source.fetch_jobs()
        print(f"{len(source_jobs)} Job(s) aus {source.SOURCE_NAME}")

        for job in source_jobs:
            url = job.primary_url
            dedupe_key = canonical_url(url)
            if dedupe_key in seen_urls:
                continue
            seen_urls.add(dedupe_key)
            jobs.append(job)

    return deduplicate_jobs(jobs)


def canonical_url(url):
    """Remove query parameters and fragments used only for tracking."""
    parts = urlsplit(url or "")
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


if __name__ == "__main__":
    main()
