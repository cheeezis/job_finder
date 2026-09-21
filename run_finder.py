"""Command-line entry point for collecting, remembering, and scoring jobs."""

import argparse
import os
import time
from collections import Counter

from job_finder.availability import ignore_closed_listings
from job_finder.console import configure_utf8_output, print_phase, print_progress
from job_finder.database import worker_lock
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
from job_finder.reporting import is_visible_in_default_review, write_recommendations
from job_finder.sources import (
    arbeitnow,
    arbeitsagentur,
    bytewerk,
    compose_it,
    css,
    edag,
    german_tech_jobs,
    get_in_it,
    himalayas,
    jobicy,
    jumo,
    manual,
    nethinks,
    proemion,
    remotely,
    rhoenenergie,
    startup_jobs,
    stepstone,
    studysmarter,
)
from job_finder.sources.common import (
    canonical_detail_url as canonical_url,
)
from job_finder.sources.common import (
    fetch_diagnostics,
    reset_fetch_diagnostics,
)
from job_finder.storage import publish_results

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
    """Count failed sources and partial sources that returned no jobs."""
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
        "--exclude-sources",
        default="",
        help=(
            "Comma-separated SOURCE_NAME values to skip this run, e.g. "
            "'stepstone,remotely' for a split cloud/local schedule"
        ),
    )
    return parser.parse_args()


def parse_source_names(value):
    """Split a comma-separated source-name option into a clean set."""
    return {name.strip() for name in value.split(",") if name.strip()}


def main():
    """Run collection and scoring before persisting state and reporting."""
    configure_utf8_output()
    args = parse_args()
    with worker_lock(), RunLog():
        run_pipeline(exclude_sources=parse_source_names(args.exclude_sources))


def run_pipeline(exclude_sources=frozenset()):
    """Execute one logged run of the complete job-finding pipeline, always notifying."""
    started = time.monotonic()
    with timed_step("Backup"):
        create_backup([MEMORY_FILE, NOTIFICATION_STATE_FILE])

    print_phase(1, 4, "Quellen")
    with timed_step("Quellen und Deduplizierung"):
        selected_sources = [
            source for source in SOURCES if source.SOURCE_NAME not in exclude_sources
        ]
        jobs, source_reports = collect_jobs(selected_sources)
        print_source_summary(source_reports, len(jobs))
        require_usable_source_snapshot(source_reports)

    print_phase(2, 4, "Bewertung")
    with timed_step("Vorfilter"):
        results = score_jobs(jobs)

    candidate_ids = {job["id"] for job in results["included"]}
    with timed_step("Detailanreicherung"):
        enrich_candidate_jobs(jobs, candidate_ids)

    # Validate final details before committing any workflow state. The score
    # stays attached to the job as memory resolves its ID and timestamps.
    with timed_step("Endgültige Bewertung"):
        evaluated_jobs = evaluate_jobs(jobs)

    # Persist only the final post-enrichment set; enrichers may remove closed ads.
    print_phase(3, 4, "Bestand und Verfügbarkeit")
    complete_sources = {
        report["name"]
        for report in source_reports
        if report["status"] in {"success", "empty"}
    }
    with timed_step("Gedächtnis speichern"):
        with edit_memory(MEMORY_FILE) as memory:
            memory_stats = update_memory(
                jobs,
                memory,
                successful_sources=complete_sources,
            )

    with timed_step("Offline-Prüfung"):
        closed_ids = ignore_closed_listings(
            jobs,
            MEMORY_FILE,
            successful_sources=complete_sources,
            progress=print_availability_progress,
        )

    if closed_ids:
        print(
            f"Nicht mehr verfügbar: {len(closed_ids)} Stelle(n) auf Nicht interessant gesetzt"
        )
    results = build_score_results(evaluated_jobs)
    print(
        f"{memory_stats['inactive']} neu inaktiv · "
        f"{memory_stats['reactivated']} reaktiviert"
    )
    with timed_step("Ergebnisdateien schreiben"):
        publish_results(
            jobs, results, jobs_path=JOBS_FILE, writer=write_recommendations
        )
        print(
            f"Vorfilter: {len(results['included'])} weiter · "
            f"{len(results['excluded'])} ausgeschlossen"
        )

    print_phase(4, 4, "Ausgabe und Benachrichtigungen")
    with timed_step("Benachrichtigungen"):
        notification_stats = process_notifications(
            results,
            send=True,
            webhook_url=os.getenv("DISCORD_WEBHOOK_URL"),
        )
        if notification_stats["configuration_error"]:
            print(f"Discord: {notification_stats['configuration_error']}")
        else:
            print(
                f"Discord: {notification_stats['sent']} gesendet, "
                f"{notification_stats['failed']} fehlgeschlagen"
            )

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

    print("\nErgebnisübersicht")
    print_review_diagnostics(results, memory_stats)


def print_availability_progress(current, total):
    """Print offline-check counts or explain that no URLs need checking."""
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
        job.get("workflow_status") == "new" and is_visible_in_default_review(job)
        for job in results["included"]
    )
    print(
        f"  Erstfunde: {memory_stats['new']} · "
        f"{memory_stats['known']} bereits bekannt · "
        f"{new_included} davon passend · "
        f"{new_excluded} davon ausgeschlossen\n"
        f"  Review Neu: {standard_new} im Standardfilter · "
        f"{pending} unbearbeitet einschließlich Sonderfilter",
        flush=True,
    )


def collect_jobs(sources=None):
    """Return deduplicated jobs and coverage reports from selected sources.

    Each adapter provides SOURCE_NAME and fetch_jobs(). Prefer the
    optional fetch_jobs_with_report() when available; its result has
    jobs, status and optional details. Catch source errors so other
    sources can complete, and include handled partial failures in each
    report. Reports expose name, status, job count and error details.
    """
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
            source_jobs, source_status, report_details = fetch_source_jobs(source)
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
                f"fehlgeschlagen ({source_error_label(error)})",
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
            f"{len(source_jobs)} Stellen"
            + (
                f" · Teilergebnis ({report_details.get('failed_segments', '?')} Segment(e) fehlgeschlagen)"
                if source_status == "partial"
                else ""
            ),
        )
        for job in source_jobs:
            url = job.primary_url
            dedupe_key = canonical_url(url)
            if dedupe_key in seen_urls:
                continue
            seen_urls.add(dedupe_key)
            jobs.append(job)

    return deduplicate_jobs(jobs), source_reports


def fetch_source_jobs(source):
    """Apply the optional coverage report and include internally handled failures."""
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
    return source_jobs, source_status, report_details


def enrich_candidate_jobs(jobs, candidate_ids, sources=None):
    """Let selected adapters update the candidate list in place.

    Each optional adapter hook receives jobs and candidate_ids and
    returns a count of affected jobs. Hooks may replace job objects or
    remove confirmed closed listings. Return the sum of hook counts;
    unexpected hook errors propagate to stop the pipeline.
    """
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
    counts = Counter(report["status"] for report in source_reports)
    print(
        f"  Quellen: {counts['success']} vollständig · "
        f"{counts['partial']} teilweise · {counts['empty']} ohne Treffer · "
        f"{counts['failed']} fehlgeschlagen · "
        f"{total_jobs} Stellen nach Deduplizierung"
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
    new_by_source = Counter(
        source.source for job in jobs if job.is_new for source in job.sources
    )

    review_new = sum(bool(job.get("is_new")) for job in results["included"])
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
    return (
        f"HTTP {status_code}" if isinstance(status_code, int) else type(error).__name__
    )


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
