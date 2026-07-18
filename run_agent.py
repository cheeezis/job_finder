"""Command-line entry point for collecting, remembering, and scoring jobs."""

import argparse
import json
import os
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from job_agent.console import configure_utf8_output
from job_agent.deduplication import deduplicate_jobs
from job_agent.main import print_results, score_jobs
from job_agent.llm.service import analyze_results
from job_agent.memory import load_memory, save_memory, update_memory
from job_agent.notifications import process_notifications
from job_agent.operations import RunLog, create_backup
from job_agent.paths import (
    JOBS_FILE,
    LLM_CACHE_FILE,
    MEMORY_FILE,
    NOTIFICATION_STATE_FILE,
)
from job_agent.reporting import write_recommendations
from job_agent.sources import arbeitnow
from job_agent.sources import arbeitsagentur
from job_agent.sources import css
from job_agent.sources import edag
from job_agent.sources import get_in_it
from job_agent.sources import jumo
from job_agent.sources import nethinks
from job_agent.sources import proemion
from job_agent.sources import stepstone


SOURCES = [
    arbeitsagentur,
    stepstone,
    get_in_it,
    arbeitnow,
    jumo,
    edag,
    css,
    proemion,
    nethinks,
]

def parse_args():
    """Parse the optional cost limit for the productive LLM analysis."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--llm-limit",
        type=int,
        help="Analyze at most N eligible jobs in this run",
    )
    parser.add_argument(
        "--notify",
        action="store_true",
        help="Send queued positive recommendations to Discord",
    )
    return parser.parse_args()


def main():
    """Run the full pipeline: collect jobs, update memory, then score."""
    configure_utf8_output()
    args = parse_args()
    with RunLog():
        run_pipeline(args)


def run_pipeline(args):
    """Execute one logged run of the complete agent pipeline."""
    backup = create_backup(
        [MEMORY_FILE, LLM_CACHE_FILE, NOTIFICATION_STATE_FILE]
    )
    if backup:
        print(f"Sicherung erstellt: {backup}")
    print("1/4 Sammle Jobs aus Quellen")
    jobs, source_status = collect_jobs()

    print("\n2/4 Aktualisiere Job-Gedaechtnis")
    memory = load_memory(MEMORY_FILE)
    successful_sources = {
        name for name, succeeded in source_status.items() if succeeded
    }
    memory_stats = update_memory(
        jobs,
        memory,
        successful_sources=successful_sources,
    )
    save_memory(memory, MEMORY_FILE)
    JOBS_FILE.parent.mkdir(parents=True, exist_ok=True)
    JOBS_FILE.write_text(
        json.dumps([job.to_dict() for job in jobs], indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"{len(jobs)} Job(s) gespeichert in {JOBS_FILE}")
    print(f'Neue Jobs: {memory_stats["new"]}')
    print(f'Bekannte Jobs: {memory_stats["known"]}')
    print(f'Neu inaktiv: {memory_stats["inactive"]}')
    print(f'Reaktiviert: {memory_stats["reactivated"]}')
    print(f"Gedaechtnis gespeichert in {MEMORY_FILE}")

    print("\n3/4 Bewerte Jobs")
    results = score_jobs(jobs)
    print("\nKI-Bewertung")
    llm_stats = analyze_results(results, limit=args.llm_limit)
    print(
        f"KI: {llm_stats['analyzed']} neu analysiert, "
        f"{llm_stats['cached']} aus Cache, "
        f"{llm_stats['failed']} fehlgeschlagen"
    )
    write_recommendations(results)
    print_results(results)

    print("\n4/4 Bereite Benachrichtigungen vor")
    notification_stats = process_notifications(
        results,
        send=args.notify,
        webhook_url=os.getenv("DISCORD_WEBHOOK_URL"),
    )
    if notification_stats["configuration_error"]:
        print(f"Discord: {notification_stats['configuration_error']}")
    elif args.notify:
        print(
            f"Discord: {notification_stats['sent']} gesendet, "
            f"{notification_stats['failed']} fehlgeschlagen"
        )
    else:
        print(
            f"Discord: {notification_stats['ready']} bereit; "
            "mit --notify senden"
        )


def collect_jobs(sources=None):
    """Collect jobs from all configured sources and merge duplicates."""
    jobs = []
    seen_urls = set()
    source_status = {}

    for source in sources or SOURCES:
        print(f"\nQuelle: {source.SOURCE_NAME}")
        try:
            source_jobs = source.fetch_jobs()
        except Exception as error:
            source_status[source.SOURCE_NAME] = False
            print(f"FEHLER Quelle {source.SOURCE_NAME}: {type(error).__name__}: {error}")
            print("Der Lauf wird mit den uebrigen Quellen fortgesetzt")
            continue
        source_status[source.SOURCE_NAME] = bool(source_jobs)
        if not source_jobs:
            print("Quelle lieferte keine Jobs; keine Anzeigen als inaktiv markieren")
        print(f"{len(source_jobs)} Job(s) aus {source.SOURCE_NAME}")

        for job in source_jobs:
            url = job.primary_url
            dedupe_key = canonical_url(url)
            if dedupe_key in seen_urls:
                continue
            seen_urls.add(dedupe_key)
            jobs.append(job)

    return deduplicate_jobs(jobs), source_status


def canonical_url(url):
    """Remove tracking parameters while retaining source-stable job IDs."""
    parts = urlsplit(url or "")
    stable_parameters = [
        (name, value)
        for name, value in parse_qsl(parts.query, keep_blank_values=True)
        if name == "jobOfferId"
    ]
    return urlunsplit(
        (
            parts.scheme,
            parts.netloc,
            parts.path,
            urlencode(stable_parameters),
            "",
        )
    )


if __name__ == "__main__":
    main()
