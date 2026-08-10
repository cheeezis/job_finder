"""Command-line entry point for collecting, remembering, and scoring jobs."""

import argparse
import json
import os
import time
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from job_agent.console import configure_utf8_output
from job_agent.deduplication import deduplicate_jobs
from job_agent.main import print_results, score_jobs
from job_agent.llm.service import analyze_results
from job_agent.memory import load_memory, save_memory, update_memory
from job_agent.notifications import process_notifications, send_run_summary
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
from job_agent.sources import bytewerk
from job_agent.sources import css
from job_agent.sources import compose_it
from job_agent.sources import edag
from job_agent.sources import get_in_it
from job_agent.sources import jumo
from job_agent.sources import nethinks
from job_agent.sources import proemion
from job_agent.sources import rhoenenergie
from job_agent.sources import stepstone


SOURCES = [
    arbeitsagentur,
    stepstone,
    get_in_it,
    arbeitnow,
    compose_it,
    bytewerk,
    rhoenenergie,
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
    started = time.monotonic()
    backup = create_backup(
        [MEMORY_FILE, LLM_CACHE_FILE, NOTIFICATION_STATE_FILE]
    )
    if backup:
        print(f"Sicherung erstellt: {backup}")
    print("1/4 Sammle Jobs aus Quellen")
    jobs, source_status, source_reports = collect_jobs()

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
    candidate_ids = {job["id"] for job in results["included"]}
    enriched = arbeitnow.enrich_candidate_jobs(jobs, candidate_ids)
    if enriched:
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

    if args.notify:
        summary_error = send_run_summary(
            build_run_summary(
                duration_seconds=time.monotonic() - started,
                jobs=jobs,
                results=results,
                memory_stats=memory_stats,
                llm_stats=llm_stats,
                source_reports=source_reports,
            ),
            webhook_url=os.getenv("DISCORD_WEBHOOK_URL"),
        )
        if summary_error:
            print(f"Discord-Laufstatistik: {summary_error}")
        else:
            print("Discord-Laufstatistik gesendet")


def collect_jobs(sources=None):
    """Collect jobs from all configured sources and merge duplicates."""
    jobs = []
    seen_urls = set()
    source_status = {}
    source_reports = []

    for source in sources or SOURCES:
        print(f"\nQuelle: {source.SOURCE_NAME}")
        try:
            source_jobs = source.fetch_jobs()
        except Exception as error:
            source_status[source.SOURCE_NAME] = False
            source_reports.append(
                {"name": source.SOURCE_NAME, "status": "failed", "jobs": 0}
            )
            print(f"FEHLER Quelle {source.SOURCE_NAME}: {type(error).__name__}: {error}")
            print("Der Lauf wird mit den uebrigen Quellen fortgesetzt")
            continue
        source_status[source.SOURCE_NAME] = bool(source_jobs)
        source_reports.append(
            {
                "name": source.SOURCE_NAME,
                "status": "success" if source_jobs else "empty",
                "jobs": len(source_jobs),
            }
        )
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

    return deduplicate_jobs(jobs), source_status, source_reports


def build_run_summary(
    *, duration_seconds, jobs, results, memory_stats, llm_stats, source_reports
):
    """Collect the reliable counts shown in Discord after one complete run."""
    new_by_source = {}
    for job in jobs:
        if not job.is_new:
            continue
        for source in job.sources:
            new_by_source[source.source] = new_by_source.get(source.source, 0) + 1

    reviewed = [job for job in results["included"] if job.get("llm_result")]
    recommended = sum(
        job["llm_result"].get("recommendation")
        in {"strong_match", "match", "borderline"}
        for job in reviewed
    )
    summary_sources = [
        {
            "label": source_label(report["name"]),
            "status": report["status"],
            "jobs": report["jobs"],
            "new": new_by_source.get(report["name"], 0),
        }
        for report in source_reports
    ]
    return {
        "duration": format_duration(duration_seconds),
        "jobs_total": len(jobs),
        "jobs_new": memory_stats["new"],
        "jobs_known": memory_stats["known"],
        "included": len(results["included"]),
        "excluded": len(results["excluded"]),
        "analyzed": llm_stats["analyzed"],
        "cached": llm_stats["cached"],
        "analysis_failed": llm_stats["failed"],
        "recommended": recommended,
        "not_recommended": len(reviewed) - recommended,
        "sources": summary_sources,
    }


def format_duration(duration_seconds):
    """Format elapsed runtime without distracting sub-second precision."""
    seconds = max(0, round(duration_seconds))
    minutes, seconds = divmod(seconds, 60)
    if minutes:
        return f"{minutes} Min. {seconds:02d} Sek."
    return f"{seconds} Sek."


def source_label(name):
    """Make source adapter names pleasant to read in Discord."""
    return {
        "arbeitsagentur": "Arbeitsagentur",
        "stepstone": "StepStone",
        "get_in_it": "get-in-IT",
        "arbeitnow": "Arbeitnow",
        "compose_it": "Compose IT",
        "bytewerk": "bytewerk",
        "rhoenenergie": "RhönEnergie",
        "jumo": "JUMO",
        "edag": "EDAG",
        "css": "CSS",
        "proemion": "Proemion",
        "nethinks": "NETHINKS",
    }.get(name, name)


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
