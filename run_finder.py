"""Command-line entry point for collecting, remembering, and scoring jobs."""

import argparse
import json
import os
import time

from job_finder.console import configure_utf8_output
from job_finder.deduplication import deduplicate_jobs
from job_finder.main import score_jobs
from job_finder.memory import load_memory, save_memory, update_memory
from job_finder.notifications import process_notifications, send_run_summary
from job_finder.operations import RunLog, create_backup
from job_finder.paths import (
    JOBS_FILE,
    MEMORY_FILE,
    NOTIFICATION_STATE_FILE,
)
from job_finder.reporting import write_recommendations
from job_finder.sources import arbeitnow
from job_finder.sources import arbeitsagentur
from job_finder.sources import bytewerk
from job_finder.sources import css
from job_finder.sources import compose_it
from job_finder.sources import edag
from job_finder.sources import get_in_it
from job_finder.sources import himalayas
from job_finder.sources import jumo
from job_finder.sources import jobicy
from job_finder.sources import manual
from job_finder.sources import nethinks
from job_finder.sources import proemion
from job_finder.sources import rhoenenergie
from job_finder.sources import stepstone
from job_finder.sources import startup_jobs
from job_finder.sources import studysmarter
from job_finder.sources.common import canonical_detail_url as canonical_url


SOURCES = [
    arbeitsagentur,
    stepstone,
    get_in_it,
    arbeitnow,
    himalayas,
    jobicy,
    *([startup_jobs] if startup_jobs.is_configured() else []),
    studysmarter,
    manual,
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
    """Parse command-line options for one Job Finder run."""
    parser = argparse.ArgumentParser(description=__doc__)
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
    """Execute one logged run of the complete job-finding pipeline."""
    started = time.monotonic()
    create_backup([MEMORY_FILE, NOTIFICATION_STATE_FILE])
    print("1/4 Quellen")
    jobs, source_reports = collect_jobs()
    print_source_summary(source_reports, len(jobs))

    print("\n2/4 Gedächtnis")
    memory = load_memory(MEMORY_FILE)
    successful_sources = {
        report["name"]
        for report in source_reports
        if report["status"] == "success"
    }
    memory_stats = update_memory(
        jobs,
        memory,
        successful_sources=successful_sources,
    )
    save_memory(memory, MEMORY_FILE)
    print(
        f'{memory_stats["new"]} neu · {memory_stats["known"]} bekannt · '
        f'{memory_stats["inactive"]} neu inaktiv · '
        f'{memory_stats["reactivated"]} reaktiviert'
    )

    print("\n3/4 Vorfilter")
    results = score_jobs(jobs)
    candidate_ids = {job["id"] for job in results["included"]}
    enriched = enrich_candidate_jobs(jobs, candidate_ids)
    if enriched:
        results = score_jobs(jobs)
    JOBS_FILE.parent.mkdir(parents=True, exist_ok=True)
    JOBS_FILE.write_text(
        json.dumps([job.to_dict() for job in jobs], indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(
        f"Vorfilter: {len(results['included'])} weiter · "
        f"{len(results['excluded'])} ausgeschlossen"
    )
    write_recommendations(results)

    print("\n4/4 Benachrichtigungen")
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
    source_reports = []

    for source in sources or SOURCES:
        try:
            source_jobs = source.fetch_jobs()
        except Exception as error:
            source_reports.append(
                {
                    "name": source.SOURCE_NAME,
                    "status": "failed",
                    "jobs": 0,
                    "error": source_error_label(error),
                }
            )
            continue
        source_reports.append(
            {
                "name": source.SOURCE_NAME,
                "status": "success" if source_jobs else "empty",
                "jobs": len(source_jobs),
            }
        )
        for job in source_jobs:
            url = job.primary_url
            dedupe_key = canonical_url(url)
            if dedupe_key in seen_urls:
                continue
            seen_urls.add(dedupe_key)
            jobs.append(job)

    return deduplicate_jobs(jobs), source_reports


def enrich_candidate_jobs(jobs, candidate_ids, sources=None):
    """Run the optional second detail step offered by individual sources."""
    enriched = 0
    for source in sources or SOURCES:
        enricher = getattr(source, "enrich_candidate_jobs", None)
        if enricher is not None:
            enriched += enricher(jobs, candidate_ids)
    return enriched


def print_source_summary(source_reports, total_jobs):
    """Print one source total plus exceptional source states."""
    successful = sum(report["status"] == "success" for report in source_reports)
    print(
        f"  {successful}/{len(source_reports)} Quellen mit Treffern · "
        f"{total_jobs} Stellen nach Deduplizierung"
    )
    for report in source_reports:
        label = source_label(report["name"])
        if report["status"] == "failed":
            print(f"  WARNUNG {label}: {report.get('error', 'Fehler')}")
        elif report["status"] == "empty":
            print(f"  HINWEIS {label}: keine verwertbaren Treffer")


def build_run_summary(
    *, duration_seconds, jobs, results, memory_stats, source_reports
):
    """Collect the reliable counts shown in Discord after one complete run."""
    new_by_source = {}
    for job in jobs:
        if not job.is_new:
            continue
        for source in job.sources:
            new_by_source[source.source] = new_by_source.get(source.source, 0) + 1

    review_updates = sum(
        bool(job.get("is_new") or job.get("content_changed"))
        for job in results["included"]
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
        "review_updates": review_updates,
        "sources": summary_sources,
    }


def source_error_label(error):
    """Describe a source failure without leaking request URLs or messages."""
    status_code = getattr(error, "code", None) or getattr(error, "status_code", None)
    return f"HTTP {status_code}" if isinstance(status_code, int) else type(error).__name__


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
        "himalayas": "Himalayas",
        "jobicy": "Jobicy",
        "startup_jobs": "Startup Jobs",
        "studysmarter": "StudySmarter",
        "manual": "Manuell hinzugefügt",
        "compose_it": "Compose IT",
        "bytewerk": "bytewerk",
        "rhoenenergie": "RhönEnergie",
        "jumo": "JUMO",
        "edag": "EDAG",
        "css": "CSS",
        "proemion": "Proemion",
        "nethinks": "NETHINKS",
    }.get(name, name)


if __name__ == "__main__":
    main()
