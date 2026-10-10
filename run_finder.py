"""Command-line entry point for collecting, remembering, and scoring jobs."""

import argparse
import os
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from dataclasses import dataclass, field

from psycopg import errors

from job_finder.agent.run import agent_phase
from job_finder.console import configure_utf8_output, log_event, print_phase, print_progress
from job_finder.matching.deduplication import deduplicate_jobs
from job_finder.matching.user_settings import current_settings
from job_finder.models import WorkflowStatus
from job_finder.operations import RunLog, create_backup, timed_step
from job_finder.paths import JOBS_FILE, MEMORY_FILE
from job_finder.persistence.database import lock, transaction, worker_lock
from job_finder.persistence.health import backlog
from job_finder.persistence.runs import finish_run, reconcile_runs, start_run
from job_finder.persistence.storage import publish_results
from job_finder.sources import (
    arbeitnow,
    arbeitsagentur,
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
from job_finder.sources.common import canonical_detail_url as canonical_url, collecting_diagnostics
from job_finder.sources.company_careers import BYTEWERK, CSS, NETHINKS, PROEMION, RHOENENERGIE
from job_finder.sources.compose_it import COMPOSE_IT
from job_finder.sources.registry import source_label
from job_finder.telemetry import annotate, span, start_tracing, step
from job_finder.workflow.availability import (
    apply_closed_listing_checks,
    prepare_closed_listing_checks,
    unchanged_check_ids,
)
from job_finder.workflow.main import build_score_results, combine_listings, evaluate_jobs, score_jobs
from job_finder.workflow.memory import edit_memory, update_memory
from job_finder.workflow.notifications import deliver_notifications, queue_notifications, send_run_summary
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
]
# Career pages of single companies near the home location; sources.companies
# in the settings chooses among them, without it all are searched.
COMPANY_SOURCES = [COMPOSE_IT, BYTEWERK, RHOENENERGIE, jumo, edag, CSS, PROEMION, NETHINKS]
SOURCES = [*SOURCES, *COMPANY_SOURCES]


@dataclass
class SourceResult:
    """One source's jobs with its status, coverage details and duration."""

    name: str
    jobs: list = field(default_factory=list)
    status: str = "success"
    details: dict = field(default_factory=dict)
    duration_seconds: float = 0.0

    def report(self):
        """Return the source report that the summary, Discord and the logs read."""
        return {"name": self.name, "status": self.status, "jobs": len(self.jobs), **self.details}


# Sources run side by side, at most this many at once; each keeps its own pauses,
# and the HTTP client sends only one request per host at a time.
MAX_PARALLEL_SOURCES = 4


class IncompleteSourceSnapshotError(RuntimeError):
    """Stop a run before incomplete source data can replace good output."""


def require_usable_source_snapshot(source_reports):
    """Raise unless at least half the sources were reachable.

    Failed sources and partial sources that returned no jobs count as unreachable.
    """
    unavailable = sum(
        report.get("status") == "failed" or (report.get("status") == "partial" and not report.get("jobs"))
        for report in source_reports
    )
    if not source_reports or unavailable * 2 > len(source_reports):
        raise IncompleteSourceSnapshotError(
            f"{unavailable} von {len(source_reports)} Quellen waren nicht "
            "verwendbar; vorhandene Jobs und Review-Ausgabe bleiben unverändert"
        )


def parse_args():
    """Parse command-line options for one Job Finder run."""
    parser = argparse.ArgumentParser(prog="job-finder", description=__doc__)
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


def unselected_companies(settings):
    """Return the company career pages the settings leave out; refuse names no adapter has."""
    chosen = settings.sources.companies
    if chosen is None:
        return set()
    known = {source.SOURCE_NAME for source in COMPANY_SOURCES}
    if unknown := set(chosen) - known:
        raise SystemExit(f"Unbekannte Firmen in sources.companies: {', '.join(sorted(unknown))}")
    return known - set(chosen)


def excluded_source_names(args, settings=None):
    """Return the sources to skip; --only-sources skips every source it does not name.

    Company career pages the settings leave out are skipped as well; like
    any skipped source they keep their earlier jobs. A selection that leaves
    no source is refused instead of running nothing.
    """
    known = {source.SOURCE_NAME for source in SOURCES}
    if args.only_sources is None:
        excluded = parse_source_names(args.exclude_sources)
    else:
        only = parse_source_names(args.only_sources)
        if unknown := only - known:
            raise SystemExit(f"Unbekannte Quellen: {', '.join(sorted(unknown))}")
        excluded = known - only
    if settings is not None:
        excluded |= unselected_companies(settings)
    if known <= excluded:
        raise SystemExit("Keine Quelle ausgewählt; der Lauf würde nichts abrufen.")
    return excluded


def main():
    """Run collection and scoring before persisting state and reporting."""
    configure_utf8_output()
    args = parse_args()
    # Invalid settings stop the run here, before any source is searched.
    excluded = excluded_source_names(args, current_settings())
    tracing = start_tracing()
    try:
        with worker_lock(), RunLog() as run_log, span("finder_run", **{"jobfinder.run_id": run_log.run_id}):
            selected = [source.SOURCE_NAME for source in SOURCES if source.SOURCE_NAME not in excluded]
            record_run(reconcile_runs)
            record_run(start_run, run_log.run_id, selected)
            try:
                run_pipeline(exclude_sources=excluded, run_id=run_log.run_id)
            except BaseException:
                record_run(finish_run, run_log.run_id, "failed")
                raise
    finally:
        if tracing is not None:
            # The container ends with the run: send the spans now, not in the background.
            tracing.shutdown()


def run_pipeline(exclude_sources=frozenset(), run_id=None):
    """Execute one logged run of the complete job-finding pipeline, always notifying."""
    started = time.monotonic()
    # A container discards its filesystem, and the ZIP with it; there Azure
    # point-in-time restore and blob versioning protect the data instead.
    if os.environ.get("JOBFINDER_SKIP_RUN_BACKUP") == "1":
        print("  Backup: übersprungen (Container ohne dauerhaftes Dateisystem)")
    else:
        with timed_step("Backup", "backup"):
            create_backup()

    print(f"  Einstellungen: {current_settings().source}")
    print_phase(1, 4, "Quellen")
    with timed_step("Quellen und Deduplizierung", "collect_sources"):
        selected_sources = [source for source in SOURCES if source.SOURCE_NAME not in exclude_sources]
        jobs, source_reports = collect_jobs(selected_sources, run_id=run_id)
        print_source_summary(source_reports, len(jobs))
        require_usable_source_snapshot(source_reports)

    print_phase(2, 4, "Bewertung")
    with timed_step("Vorfilter", "prefilter"):
        results = score_jobs(jobs)

    candidate_ids = {job["id"] for job in results["included"]}
    with timed_step("Detailanreicherung", "enrich_details"):
        enrichment_reports = enrich_candidate_jobs(jobs, candidate_ids, sources=selected_sources, run_id=run_id)

    # Validate final details before committing any workflow state. The score
    # stays attached to the job as memory resolves its ID and timestamps.
    with timed_step("Endgültige Bewertung", "evaluate"):
        evaluated_jobs = evaluate_jobs(jobs)

    # Persist only the final post-enrichment set; enrichers may remove closed ads.
    print_phase(3, 4, "Bestand und Verfügbarkeit")
    complete_sources = {report["name"] for report in source_reports if report["status"] in {"success", "empty"}}
    # A split schedule's run answers for missing jobs only through its own sources.
    run_sources = {report["name"] for report in source_reports}
    with timed_step("Offline-Prüfung", "availability_checks"):
        availability_checks = prepare_closed_listing_checks(
            jobs,
            MEMORY_FILE,
            successful_sources=complete_sources,
            run_sources=run_sources,
            progress=print_availability_progress,
            resolve_ids=True,
        )

    # Publication always locks before memory, matching manual imports and restore.
    # No source, closure check or Discord request runs inside this transaction.
    with (
        timed_step("Bestand, Ergebnisse und Benachrichtigungsaufträge speichern", "publish"),
        transaction() as connection,
    ):
        lock(connection, "finder-publication")
        with edit_memory(MEMORY_FILE) as memory:
            unchanged_ids = unchanged_check_ids(availability_checks, memory)
            aliases = {}
            memory_stats = update_memory(
                jobs, memory, successful_sources=complete_sources, run_sources=run_sources, aliases=aliases
            )
            closed_ids = apply_closed_listing_checks(availability_checks, memory, unchanged_ids=unchanged_ids)
            for job in jobs:
                if job.id in closed_ids:
                    job.workflow_status = WorkflowStatus.IGNORED
                    job.is_new = False
        # Canonical IDs are resolved under the lock: several listings become one card.
        evaluated_jobs = combine_listings(evaluated_jobs)
        jobs = [job for job, _result in evaluated_jobs]
        results = build_score_results(evaluated_jobs)
        publish_results(
            jobs,
            results,
            jobs_path=JOBS_FILE,
            writer=write_recommendations,
            exclude_sources=set(exclude_sources) | (run_sources - complete_sources),
        )
        notification_stats = queue_notifications(results, aliases=aliases)

    if closed_ids:
        print(f"Nicht mehr verfügbar: {len(closed_ids)} Stelle(n) auf Nicht interessant gesetzt")
    print(f"{memory_stats['inactive']} neu inaktiv · {memory_stats['reactivated']} reaktiviert")
    print(f"Vorfilter: {len(results['included'])} weiter · {len(results['excluded'])} ausgeschlossen")
    log_event(
        "prefilter_completed",
        run_id=run_id,
        jobs_total=len(jobs),
        included=len(results["included"]),
        excluded=len(results["excluded"]),
    )

    print_phase(4, 4, "Ausgabe und Benachrichtigungen")
    with timed_step("Benachrichtigungen", "notifications"):
        notification_stats = deliver_notifications(
            stats=notification_stats,
            webhook_url=os.getenv("DISCORD_WEBHOOK_URL"),
            review_host=os.getenv("JOBFINDER_REVIEW_HOST"),
        )
        if notification_stats["configuration_error"]:
            print(f"Discord: {notification_stats['configuration_error']}")
        else:
            print(f"Discord: {notification_stats['sent']} gesendet, {notification_stats['failed']} fehlgeschlagen")

        summary = build_run_summary(
            duration_seconds=time.monotonic() - started,
            jobs=jobs,
            results=results,
            memory_stats=memory_stats,
            source_reports=source_reports,
            notification_stats=notification_stats,
            enrichment_reports=enrichment_reports,
        )
        summary_error = send_run_summary(summary, webhook_url=os.getenv("DISCORD_WEBHOOK_URL"))
        if summary_error:
            print(f"Discord-Laufstatistik: {summary_error}")
        else:
            print("Discord-Laufstatistik gesendet")

    print("\nErgebnisübersicht")
    print_review_diagnostics(results, memory_stats)
    # Last, once every result is saved: the agent can fail without the finder failing.
    agent_phase(run_id=run_id)
    log_run_summary(summary, source_reports, time.monotonic() - started, run_id)


def record_run(action, *args, **figures):
    """Write to the runs table; a database without it (before revision 0006) costs only the record."""
    try:
        action(*args, **figures)
    except (errors.UndefinedTable, errors.UndefinedColumn):
        print("  Lauf nicht verzeichnet: Tabelle runs fehlt (Migration 0006)")


def log_run_summary(summary, source_reports, duration_seconds, run_id):
    """Log the run's key figures as one event, after the agent, with the backlogs it leaves; record the run as finished."""
    counts = Counter(report["status"] for report in source_reports)
    if run_id is not None:
        record_run(
            finish_run,
            run_id,
            "finished",
            jobs_total=summary["jobs_total"],
            jobs_new=summary["jobs_new"],
            review_new=summary["review_new"],
            sources_partial=counts["partial"],
            sources_failed=counts["failed"],
        )
    log_event(
        "run_summary",
        run_id=run_id,
        duration_seconds=round(duration_seconds, 1),
        jobs_total=summary["jobs_total"],
        jobs_new=summary["jobs_new"],
        review_new=summary["review_new"],
        sources_partial=counts["partial"],
        sources_failed=counts["failed"],
        notifications_sent=summary["notifications"].get("sent", 0),
        notifications_failed=summary["notifications"].get("failed", 0),
        # summary["sources"] follows source_reports one by one.
        new_by_source={
            report["name"]: source["new"] for report, source in zip(source_reports, summary["sources"], strict=True)
        },
        **backlog(),
    )


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
        job.get("workflow_status") == "new" and is_visible_in_default_review(job) for job in results["included"]
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


def collect_jobs(sources, run_id=None):
    """Return deduplicated jobs and coverage reports from selected sources.

    Up to MAX_PARALLEL_SOURCES sources run at once (fetch_source); a failing
    source cannot stop the others. Each runs in a copy of the caller's context,
    so its trace step and diagnostics stay its own. Results keep the order of
    sources. Reports expose name, status, job count and error details.
    """
    jobs = []
    seen_urls = set()
    with ThreadPoolExecutor(max_workers=MAX_PARALLEL_SOURCES, thread_name_prefix="source") as pool:
        futures = [pool.submit(copy_context().run, fetch_source, source, run_id) for source in sources]
        results = [future.result() for future in futures]
    for result in results:
        for job in result.jobs:
            if (dedupe_key := canonical_url(job.primary_url)) not in seen_urls:
                seen_urls.add(dedupe_key)
                jobs.append(job)

    return deduplicate_jobs(jobs), [result.report() for result in results]


def fetch_source(source, run_id=None):
    """Fetch one source and return its SourceResult; log and trace it, never raise.

    The adapter provides SOURCE_NAME and fetch_jobs() and records its coverage
    in the diagnostics collected for this call (record_total_segments,
    record_partial_failure).
    """
    name = source.SOURCE_NAME
    label = source_label(name)
    print_progress(label, 0, 1, "wird geladen")
    started = time.monotonic()
    with step("source", **{"jobfinder.source": name}) as current:
        try:
            result = fetch_source_jobs(source)
        except Exception as error:
            result = SourceResult(name, status="failed", details={"error": source_error_label(error)})
            level, progress = "error", f"fehlgeschlagen ({result.details['error']})"
        else:
            level = "warning" if result.status in {"partial", "failed"} else "info"
            progress = f"{len(result.jobs)} Stellen"
            if result.status == "partial":
                failed = result.details.get("failed_segments", "?")
                progress += f" · Teilergebnis ({failed} Segment(e) fehlgeschlagen)"
        annotate(current, **{"jobfinder.status": result.status, "jobfinder.jobs": len(result.jobs)})
    result.duration_seconds = round(time.monotonic() - started, 1)
    print_progress(label, 1, 1, progress)
    log_event(
        "source_completed",
        run_id=run_id,
        level=level,
        source=name,
        status=result.status,
        jobs_found=len(result.jobs),
        duration_seconds=result.duration_seconds,
        **result.details,
    )
    return result


def fetch_source_jobs(source):
    """Fetch one source and derive its status and details from the diagnostics it recorded."""
    with collecting_diagnostics() as diagnostics:
        source_jobs = source.fetch_jobs()
    failed, total = diagnostics.failed_segments, diagnostics.total_segments
    status = "partial" if failed else ("success" if source_jobs else "empty")
    if total is not None:
        details = {"failed_segments": failed, "total_segments": total}
    else:
        details = {"failed_segments": failed} if failed else {}
    return SourceResult(source.SOURCE_NAME, source_jobs, status, details)


def enrich_candidate_jobs(jobs, candidate_ids, sources, run_id=None):
    """Let selected adapters update the candidate list in place.

    Each optional adapter hook receives jobs and candidate_ids and
    returns a count of affected jobs. Hooks may replace job objects or
    remove confirmed closed listings, and report candidates whose
    details failed via record_candidate_failure(). Return one report per
    hook with both counts; unexpected hook errors propagate to stop the
    pipeline.
    """
    reports = []
    for source in sources:
        enricher = getattr(source, "enrich_candidate_jobs", None)
        if enricher is not None:
            name = getattr(source, "SOURCE_NAME", "Details")
            with timed_step(f"Details {source_label(name)}"), collecting_diagnostics() as diagnostics:
                enriched = enricher(jobs, candidate_ids)
            failed = diagnostics.failed_candidates
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
    *, duration_seconds, jobs, results, memory_stats, source_reports, notification_stats=None, enrichment_reports=()
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


if __name__ == "__main__":
    main()
