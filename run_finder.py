"""Command-line entry point for collecting, remembering, and scoring jobs."""

import argparse
import os
import time
from collections import Counter

from job_finder.agent.run import agent_phase
from job_finder.console import configure_utf8_output, log_event, print_phase, print_progress
from job_finder.matching.deduplication import deduplicate_jobs
from job_finder.matching.user_settings import SETTINGS_SOURCE
from job_finder.operations import RunLog, create_backup, timed_step
from job_finder.paths import JOBS_FILE, MEMORY_FILE
from job_finder.persistence.database import worker_lock
from job_finder.persistence.storage import publish_results
from job_finder.sources import (
    arbeitnow,
    arbeitsagentur,
    compose_it,
    edag,
    german_tech_jobs,
    get_in_it,
    himalayas,
    jobicy,
    jumo,
    manual,
    remotely,
    startup_jobs,
    stepstone,
    studysmarter,
)
from job_finder.sources.common import (
    canonical_detail_url as canonical_url,
    fetch_diagnostics,
    reset_fetch_diagnostics,
)
from job_finder.sources.company_careers import BYTEWERK, CSS, NETHINKS, PROEMION, RHOENENERGIE
from job_finder.workflow.availability import ignore_closed_listings
from job_finder.workflow.main import build_score_results, evaluate_jobs, score_jobs
from job_finder.workflow.memory import edit_memory, update_memory
from job_finder.workflow.notifications import process_notifications, send_run_summary
from job_finder.workflow.reporting import is_visible_in_default_review, write_recommendations

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
    BYTEWERK,
    RHOENENERGIE,
    jumo,
    edag,
    CSS,
    PROEMION,
    NETHINKS,
]


class IncompleteSourceSnapshotError(RuntimeError):
    """Stop a run before incomplete source data can replace good output."""


def require_usable_source_snapshot(source_reports):
    """Raise unless at least half the sources were reachable.

    Failed sources and partial sources that returned no jobs count as unreachable.
    """
    unavailable = sum(
        report.get("status") == "failed"
        or (report.get("status") == "partial" and not report.get("jobs"))
        for report in source_reports
    )
    if not source_reports or unavailable * 2 > len(source_reports):
        raise IncompleteSourceSnapshotError(
            f"{unavailable} von {len(source_reports)} Quellen waren nicht "
            "verwendbar; vorhandene Jobs und Review-Ausgabe bleiben unverändert"
        )


def parse_args():
    """Parse command-line options for one Job Finder run."""
    parser = argparse.ArgumentParser(description=__doc__)
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument(
        "--exclude-sources",
        default="",
        help=(
            "Comma-separated SOURCE_NAME values to skip this run, e.g. "
            "'stepstone,remotely' for a split cloud/local schedule"
        ),
    )
    selection.add_argument(
        "--only-sources",
        default=None,
        help=(
            "Comma-separated SOURCE_NAME values to run exclusively; every other "
            "source keeps its previous jobs like an excluded one"
        ),
    )
    return parser.parse_args()


def parse_source_names(value):
    """Split a comma-separated source-name option into a clean set."""
    return {name.strip() for name in value.split(",") if name.strip()}


def excluded_source_names(args):
    """Return the sources to skip; --only-sources skips every source it does not name.

    A selection that leaves no source is refused instead of running nothing.
    """
    known = {source.SOURCE_NAME for source in SOURCES}
    if args.only_sources is None:
        excluded = parse_source_names(args.exclude_sources)
    else:
        only = parse_source_names(args.only_sources)
        if unknown := only - known:
            raise SystemExit(f"Unbekannte Quellen: {', '.join(sorted(unknown))}")
        excluded = known - only
    if known <= excluded:
        raise SystemExit("Keine Quelle ausgewählt; der Lauf würde nichts abrufen.")
    return excluded


def main():
    """Run collection and scoring before persisting state and reporting."""
    configure_utf8_output()
    args = parse_args()
    with worker_lock(), RunLog() as run_log:
        run_pipeline(exclude_sources=excluded_source_names(args), run_id=run_log.run_id)


def run_pipeline(exclude_sources=frozenset(), run_id=None):
    """Execute one logged run of the complete job-finding pipeline, always notifying."""
    started = time.monotonic()
    # A container discards its filesystem, and the ZIP with it; there Azure
    # point-in-time restore and blob versioning protect the data instead.
    if os.environ.get("JOBFINDER_SKIP_RUN_BACKUP") == "1":
        print("  Backup: übersprungen (Container ohne dauerhaftes Dateisystem)")
    else:
        with timed_step("Backup"):
            create_backup()

    print(f"  Einstellungen: {SETTINGS_SOURCE}")
    print_phase(1, 4, "Quellen")
    with timed_step("Quellen und Deduplizierung"):
        selected_sources = [
            source for source in SOURCES if source.SOURCE_NAME not in exclude_sources
        ]
        jobs, source_reports = collect_jobs(selected_sources, run_id=run_id)
        print_source_summary(source_reports, len(jobs))
        require_usable_source_snapshot(source_reports)

    print_phase(2, 4, "Bewertung")
    with timed_step("Vorfilter"):
        results = score_jobs(jobs)

    candidate_ids = {job["id"] for job in results["included"]}
    with timed_step("Detailanreicherung"):
        enrichment_reports = enrich_candidate_jobs(
            jobs, candidate_ids, sources=selected_sources, run_id=run_id
        )

    # Validate final details before committing any workflow state. The score
    # stays attached to the job as memory resolves its ID and timestamps.
    with timed_step("Endgültige Bewertung"):
        evaluated_jobs = evaluate_jobs(jobs)

    # Persist only the final post-enrichment set; enrichers may remove closed ads.
    print_phase(3, 4, "Bestand und Verfügbarkeit")
    complete_sources = {
        report["name"] for report in source_reports if report["status"] in {"success", "empty"}
    }
    with timed_step("Gedächtnis speichern"), edit_memory(MEMORY_FILE) as memory:
        memory_stats = update_memory(jobs, memory, successful_sources=complete_sources)

    with timed_step("Offline-Prüfung"):
        closed_ids = ignore_closed_listings(
            jobs,
            MEMORY_FILE,
            successful_sources=complete_sources,
            progress=print_availability_progress,
        )

    if closed_ids:
        print(f"Nicht mehr verfügbar: {len(closed_ids)} Stelle(n) auf Nicht interessant gesetzt")
    results = build_score_results(evaluated_jobs)
    print(f"{memory_stats['inactive']} neu inaktiv · {memory_stats['reactivated']} reaktiviert")
    with timed_step("Ergebnisdateien schreiben"):
        publish_results(
            jobs,
            results,
            jobs_path=JOBS_FILE,
            writer=write_recommendations,
            exclude_sources=exclude_sources,
        )
        print(
            f"Vorfilter: {len(results['included'])} weiter · "
            f"{len(results['excluded'])} ausgeschlossen"
        )
        log_event(
            "prefilter_completed",
            run_id=run_id,
            jobs_total=len(jobs),
            included=len(results["included"]),
            excluded=len(results["excluded"]),
        )

    print_phase(4, 4, "Ausgabe und Benachrichtigungen")
    with timed_step("Benachrichtigungen"):
        notification_stats = process_notifications(
            results,
            send=True,
            webhook_url=os.getenv("DISCORD_WEBHOOK_URL"),
            review_host=os.getenv("JOBFINDER_REVIEW_HOST"),
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
                enrichment_reports=enrichment_reports,
            ),
            webhook_url=os.getenv("DISCORD_WEBHOOK_URL"),
        )
        if summary_error:
            print(f"Discord-Laufstatistik: {summary_error}")
        else:
            print("Discord-Laufstatistik gesendet")

    print("\nErgebnisübersicht")
    print_review_diagnostics(results, memory_stats)
    # Last, once every result is saved: the agent can fail without the finder failing.
    agent_phase(run_id=run_id)


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


def collect_jobs(sources=None, run_id=None):
    """Return deduplicated jobs and coverage reports from selected sources.

    Each adapter provides SOURCE_NAME and fetch_jobs() and records its
    coverage through the fetch diagnostics (record_total_segments,
    record_partial_failure). Catch source errors so other sources can
    complete. Reports expose name, status, job count and error details.
    """
    jobs = []
    seen_urls = set()
    source_reports = []

    for source in SOURCES if sources is None else sources:
        label = source_label(source.SOURCE_NAME)
        print_progress(label, 0, 1, "wird geladen")
        reset_fetch_diagnostics()
        started = time.monotonic()
        try:
            source_jobs, status, details = fetch_source_jobs(source)
        except Exception as error:
            source_jobs, status, details = [], "failed", {"error": source_error_label(error)}
            level, progress = "error", f"fehlgeschlagen ({details['error']})"
        else:
            level = "warning" if status in {"partial", "failed"} else "info"
            progress = f"{len(source_jobs)} Stellen"
            if status == "partial":
                failed = details.get("failed_segments", "?")
                progress += f" · Teilergebnis ({failed} Segment(e) fehlgeschlagen)"
        source_reports.append(
            {"name": source.SOURCE_NAME, "status": status, "jobs": len(source_jobs), **details}
        )
        print_progress(label, 1, 1, progress)
        log_event(
            "source_completed",
            run_id=run_id,
            level=level,
            source=source.SOURCE_NAME,
            status=status,
            jobs_found=len(source_jobs),
            duration_seconds=round(time.monotonic() - started, 1),
            **details,
        )
        for job in source_jobs:
            if (dedupe_key := canonical_url(job.primary_url)) not in seen_urls:
                seen_urls.add(dedupe_key)
                jobs.append(job)

    return deduplicate_jobs(jobs), source_reports


def fetch_source_jobs(source):
    """Fetch one source and derive its status and details from the recorded diagnostics."""
    source_jobs = source.fetch_jobs()
    diagnostics = fetch_diagnostics()
    failed, total = diagnostics["failed_segments"], diagnostics.get("total_segments")
    status = "partial" if failed else ("success" if source_jobs else "empty")
    if total is not None:
        return source_jobs, status, {"failed_segments": failed, "total_segments": total}
    return source_jobs, status, ({"failed_segments": failed} if failed else {})


def enrich_candidate_jobs(jobs, candidate_ids, sources=None, run_id=None):
    """Let selected adapters update the candidate list in place.

    Each optional adapter hook receives jobs and candidate_ids and
    returns a count of affected jobs. Hooks may replace job objects or
    remove confirmed closed listings, and report candidates whose
    details failed via record_candidate_failure(). Return one report per
    hook with both counts; unexpected hook errors propagate to stop the
    pipeline.
    """
    reports = []
    for source in SOURCES if sources is None else sources:
        enricher = getattr(source, "enrich_candidate_jobs", None)
        if enricher is not None:
            name = getattr(source, "SOURCE_NAME", "Details")
            reset_fetch_diagnostics()
            with timed_step(f"Details {source_label(name)}"):
                enriched = enricher(jobs, candidate_ids)
            failed = fetch_diagnostics()["failed_candidates"]
            reports.append({"name": name, "enriched": enriched, "failed": failed})
            log_event(
                "enrichment_completed",
                run_id=run_id,
                level="warning" if failed else "info",
                source=name,
                enriched=enriched,
                failed=failed,
            )
    return reports


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
    enrichment_reports=(),
):
    """Collect the reliable counts shown in Discord after one complete run."""
    new_by_source = Counter(source.source for job in jobs if job.is_new for source in job.sources)

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
        "detail_failures": [
            {"label": source_label(report["name"]), "failed": report["failed"]}
            for report in enrichment_reports
            if report["failed"]
        ],
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
