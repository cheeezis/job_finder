"""Command-line entry point for collecting, remembering, and scoring jobs."""

import argparse
import os
import time

from job_finder.availability import ignore_closed_listings
from job_finder.console import configure_utf8_output, print_phase, print_progress
from job_finder.deduplication import deduplicate_jobs
from job_finder.main import build_score_results, evaluate_jobs, score_jobs
from job_finder.memory import edit_memory, update_memory
from job_finder.notifications import process_notifications, send_run_summary
from job_finder.operations import RunLog, create_backup, timed_step
from job_finder.paths import (
    JOBS_FILE,
    MEMORY_FILE,
    NOTIFICATION_STATE_FILE,
)
from job_finder.reporting import is_international_listing, write_recommendations
from job_finder.storage import write_json_atomic
from job_finder.sources import arbeitnow
from job_finder.sources import arbeitsagentur
from job_finder.sources import bytewerk
from job_finder.sources import css
from job_finder.sources import compose_it
from job_finder.sources import edag
from job_finder.sources import get_in_it
from job_finder.sources import german_tech_jobs
from job_finder.sources import himalayas
from job_finder.sources import jumo
from job_finder.sources import jobicy
from job_finder.sources import manual
from job_finder.sources import nethinks
from job_finder.sources import proemion
from job_finder.sources import remotely
from job_finder.sources import rhoenenergie
from job_finder.sources import stepstone
from job_finder.sources import startup_jobs
from job_finder.sources import studysmarter
from job_finder.sources.common import (
    canonical_detail_url as canonical_url,
    fetch_diagnostics,
    reset_fetch_diagnostics,
)


SOURCES = [
    arbeitsagentur,
    stepstone,
    get_in_it,
    arbeitnow,
    himalayas,
    jobicy,
    german_tech_jobs,
    remotely,
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


class IncompleteSourceSnapshotError(RuntimeError):
    """Stop a run before incomplete source data can replace good output."""


def unavailable_source_count(source_reports):
    return sum(
        report.get("status") == "failed"
        or (report.get("status") == "partial" and not report.get("jobs"))
        for report in source_reports
    )


def source_snapshot_is_usable(source_reports):
    """Accept a snapshot only when at least half its sources were reachable."""
    if not source_reports:
        return False

    unavailable = unavailable_source_count(source_reports)
    return unavailable * 2 <= len(source_reports)


def require_usable_source_snapshot(source_reports):
    """Raise with a concise diagnosis when source coverage is catastrophic."""
    if source_snapshot_is_usable(source_reports):
        return

    unavailable = unavailable_source_count(source_reports)
    raise IncompleteSourceSnapshotError(
        f"{unavailable} von {len(source_reports)} Quellen waren nicht "
        "verwendbar; vorhandene Jobs und Review-Ausgabe bleiben unverändert"
    )

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
    with RunLog(), timed_step("Gesamtlauf"):
        run_pipeline(args)


def run_pipeline(args):
    """Execute one logged run of the complete job-finding pipeline."""
    started = time.monotonic()
    with timed_step('Backup'):
        create_backup([MEMORY_FILE, NOTIFICATION_STATE_FILE])

    print_phase(1, 4, "Quellen")
    with timed_step('Quellen und Deduplizierung'):
        jobs, source_reports = collect_jobs()
        print_source_summary(source_reports, len(jobs))
        require_usable_source_snapshot(source_reports)

    print_phase(2, 4, "Vorfilter und Details")
    with timed_step('Vorfilter'):
        results = score_jobs(jobs)

    candidate_ids = {job["id"] for job in results["included"]}
    with timed_step('Detailanreicherung'):
        enrich_candidate_jobs(jobs, candidate_ids)

    # Validate final details before committing any workflow state. The score
    # stays attached to the job as memory resolves its ID and timestamps.
    with timed_step('Endgültige Bewertung'):
        evaluated_jobs = evaluate_jobs(jobs)

    # Persist only the final post-enrichment set; enrichers may remove closed ads.
    print_phase(3, 4, "Gedächtnis")
    complete_sources = {
        report["name"]
        for report in source_reports
        if report["status"] in {"success", "empty"}
    }
    with timed_step('Gedächtnis speichern'):
        with edit_memory(MEMORY_FILE) as memory:
            memory_stats = update_memory(
                jobs,
                memory,
                successful_sources=complete_sources,
            )

    with timed_step('Offline-Prüfung'):
        closed_ids = ignore_closed_listings(
            jobs, MEMORY_FILE, successful_sources=complete_sources,
            progress=print_availability_progress,
        )

    if closed_ids:
        print(f"Nicht mehr verfügbar: {len(closed_ids)} Stelle(n) auf Nicht interessant gesetzt")
    results = build_score_results(evaluated_jobs)
    print_review_diagnostics(results, memory_stats)
    print(
        f'{memory_stats["new"]} neu · {memory_stats["known"]} bekannt · '
        f'{memory_stats["inactive"]} neu inaktiv · '
        f'{memory_stats["reactivated"]} reaktiviert'
    )
    with timed_step('Ergebnisdateien schreiben'):
        write_json_atomic(JOBS_FILE, [job.to_dict() for job in jobs])
        print(
            f"Vorfilter: {len(results['included'])} weiter · "
            f"{len(results['excluded'])} ausgeschlossen"
        )
        write_recommendations(results)

    print_phase(4, 4, "Ausgabe und Benachrichtigungen")
    with timed_step('Benachrichtigungen'):
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
                    notification_stats=notification_stats,
                ),
                webhook_url=os.getenv("DISCORD_WEBHOOK_URL"),
            )
            if summary_error:
                print(f"Discord-Laufstatistik: {summary_error}")
            else:
                print("Discord-Laufstatistik gesendet")


def print_availability_progress(current, total):
    if total:
        print_progress("Offline-URLs", current, total)
    else:
        print("Offline-Prüfung: keine URLs zu prüfen", flush=True)


def print_review_diagnostics(results, memory_stats):
    """Explain the difference between discovered jobs and new review candidates."""
    new_included = sum(bool(job.get("is_new")) for job in results["included"])
    new_excluded = sum(bool(job.get("is_new")) for job in results["excluded"])
    pending = sum(job.get("workflow_status") == "new" for job in results["included"])
    standard_new = sum(
        bool(job.get("is_new")) and job.get("workflow_status") == "new"
        and not is_international_listing(job)
        and not str(job.get("location_precheck") or "").startswith("Junior-Hybrid")
        for job in results["included"]
    )
    print(
        f"Review-Diagnose: {memory_stats['new']} erstmals gespeichert · "
        f"{memory_stats['known']} bereits bekannt · "
        f"{new_included} erstmals gefunden und im Vorfilter passend · "
        f"{new_excluded} erstmals gefunden und ausgeschlossen · "
        f"{pending} passende Stellen mit Status Neu · "
        f"{standard_new} im Standardfilter Neu (ohne weitere Suchfilter)", flush=True,
    )


def collect_jobs(sources=None):
    """Collect jobs from all configured sources and merge duplicates."""
    jobs = []
    seen_urls = set()
    source_reports = []

    selected_sources = list(sources or SOURCES)
    for source in selected_sources:
        label = source_label(source.SOURCE_NAME)
        print_progress(
            label,
            0,
            1,
            "wird geladen",
        )
        reset_fetch_diagnostics()
        try:
            with timed_step(f"Quelle {label}"):
                report_fetcher = getattr(source, "fetch_jobs_with_report", None)
                if report_fetcher is None:
                    source_jobs = source.fetch_jobs()
                    source_status = "success" if source_jobs else "empty"
                    report_details = {}
                else:
                    source_result = report_fetcher()
                    source_jobs = source_result["jobs"]
                    source_status = source_result["status"]
                    report_details = source_result.get("details", {})
                handled_failures = fetch_diagnostics()["failed_segments"]
                if handled_failures and source_status != "partial":
                    source_status = "partial"
                    report_details = {
                        **report_details,
                        "failed_segments": handled_failures,
                    }
        except Exception as error:
            source_reports.append(
                {
                    "name": source.SOURCE_NAME,
                    "status": "failed",
                    "jobs": 0,
                    "error": source_error_label(error),
                }
            )
            print_progress(
                label,
                1,
                1,
                "fehlgeschlagen",
            )
            continue
        source_reports.append(
            {
                "name": source.SOURCE_NAME,
                "status": source_status,
                "jobs": len(source_jobs),
                **report_details,
            }
        )
        print_progress(
            label,
            1,
            1,
            f"{len(source_jobs)} Stellen",
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
            label = source_label(getattr(source, "SOURCE_NAME", "Details"))
            with timed_step(f"Details {label}"):
                enriched += enricher(jobs, candidate_ids)
    return enriched


def print_source_summary(source_reports, total_jobs):
    """Print one source total plus exceptional source states."""
    counts = {
        status: sum(report["status"] == status for report in source_reports)
        for status in ("success", "partial", "empty", "failed")
    }
    print(
        f"  Quellen: {counts['success']} vollständig · "
        f"{counts['partial']} teilweise · {counts['empty']} ohne Treffer · "
        f"{counts['failed']} fehlgeschlagen · "
        f"{total_jobs} Stellen nach Deduplizierung"
    )
    for report in source_reports:
        label = source_label(report["name"])
        if report["status"] == "failed":
            print(f"  WARNUNG {label}: {report.get('error', 'Fehler')}")
        elif report["status"] == "empty":
            print(f"  HINWEIS {label}: keine verwertbaren Treffer")
        elif report["status"] == "partial":
            print(
                f"  WARNUNG {label}: Teilergebnis; "
                f"{report.get('failed_segments', '?')} Segment(e) fehlgeschlagen"
            )


def build_run_summary(
    *,
    duration_seconds,
    jobs,
    results,
    memory_stats,
    source_reports,
    notification_stats=None,
):
    """Collect the reliable counts shown in Discord after one complete run."""
    new_by_source = {}
    for job in jobs:
        if not job.is_new:
            continue
        for source in job.sources:
            new_by_source[source.source] = new_by_source.get(source.source, 0) + 1

    review_new = sum(
        bool(job.get("is_new"))
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
        "review_new": review_new,
        "notifications": dict(notification_stats or {}),
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
        "german_tech_jobs": "GermanTechJobs",
        "remotely": "Remotely",
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
