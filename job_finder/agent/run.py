"""The agent's part of a finder run: pick jobs, write fact sheets, stop at the limits.

It runs after the finder has saved its results and never breaks the finder
run. It only starts when the switch is on, a model endpoint is configured
(the Azure worker and the local hybrid run have one) and a profile exists.
"""

import os
import time
from datetime import date

from azure.identity import DefaultAzureCredential, get_bearer_token_provider

from job_finder.agent.basis import changed_parts, job_basis, run_basis
from job_finder.agent.cost_guard import AgentStopped, CostGuard, euro
from job_finder.agent.instructions import profile_with_places
from job_finder.agent.profile import configured_profile
from job_finder.agent.runner import MODEL, agent_model, write_fact_sheet
from job_finder.agent.settings import agent_settings
from job_finder.console import log_event
from job_finder.matching.user_settings import USER_SETTINGS
from job_finder.paths import JOBS_FILE
from job_finder.persistence.agent_usage import spent_today_and_this_month
from job_finder.persistence.fact_sheets import fact_sheets, mark_outdated
from job_finder.persistence.postgres_store import read_jobs
from job_finder.persistence.storage import dataset_name
from job_finder.telemetry import annotate, configure_tracing, span
from job_finder.workflow.memory import edit_job
from job_finder.workflow.notifications import send_warning
from job_finder.workflow.review_actions import RERUN_FIELD
from job_finder.workflow.review_data import load_review_jobs

ENDPOINT_ENV = "JOBFINDER_OPENAI_ENDPOINT"
WARNING_TITLE = "Job Finder · Steckbriefe"
# The worker job ends after 60 minutes and the finder itself needs about 10.
RUN_SECONDS = 35 * 60


def agent_phase(run_id=None, values=USER_SETTINGS, environ=os.environ):
    """Run the agent after the finder; say why when it does not run, never raise."""
    print("\nSteckbriefe (Agent)")
    settings = agent_settings(values)
    if not settings.enabled:
        print(f"  Agent aus: {settings.reason}")
        return None
    endpoint = environ.get(ENDPOINT_ENV)
    if not endpoint:
        print(f"  Agent übersprungen: {ENDPOINT_ENV} fehlt")
        return None
    try:
        profile_text, source = configured_profile(environ)
    except ValueError as error:
        print(f"  Agent aus: {error}")
        return None
    basis = run_basis(profile_text, values, settings)
    # The profile refers to the search settings for the places; the agent needs them itself.
    profile_text = profile_with_places(profile_text, values)
    print(f"  Profil: {source} · Denkaufwand: {settings.reasoning_effort}")
    tracing = start_tracing(environ)
    try:
        stats = run_agent(settings, profile_text, model_client(endpoint, environ), run_id=run_id, basis=basis)
    except Exception as error:
        # The finder's results are saved already; only the agent's part fails.
        print(f"  Agent abgebrochen: {type(error).__name__}")
        log_event("agent_failed", run_id=run_id, level="error", error=type(error).__name__)
        warn(f"Agent abgebrochen: {type(error).__name__}", environ)
        return None
    finally:
        if tracing is not None:
            # The container ends with the run: send the spans now, not in the background.
            tracing.shutdown()
    print(
        f"  {stats['fertig']} fertig · {stats['abgebrochen']} abgebrochen · "
        f"{stats['offen']} offen · {stats.get('veraltet', 0)} veraltet · heute {euro(stats['heute_eur'])} von "
        f"{euro(settings.limits.daily_max_cost_eur)}"
    )
    if stats["stopp"]:
        print(f"  Stopp: {stats['stopp']}")
        warn(f"Agent gestoppt: {stats['stopp']} · {stats['offen']} offen", environ)
    log_event(
        "agent_completed",
        run_id=run_id,
        **{key: str(value) if key.endswith("_eur") else value for key, value in stats.items()},
    )
    return stats


def start_tracing(environ):
    """Turn on traces when Application Insights is configured; a failure only costs the traces."""
    try:
        tracing = configure_tracing(environ)
    except Exception as error:
        print(f"  Traces aus: {type(error).__name__}")
        return None
    if tracing is not None:
        print("  Traces: Application Insights")
    return tracing


def warn(text, environ):
    """Report in Discord that the agent did not finish normally.

    The worker job still succeeds, so no failed-run alert shows it.
    """
    error = send_warning(WARNING_TITLE, text, webhook_url=environ.get("DISCORD_WEBHOOK_URL"))
    if error:
        print(f"  Discord-Warnung: {error}")


def run_agent(settings, profile_text, client, clock=time.monotonic, today=None, run_id=None, basis=None):
    """Write fact sheets for the best waiting jobs until money, time or jobs run out.

    With the run's basis (run_basis), every new sheet is stamped with it and
    older sheets are marked where it has changed since.
    """
    guard = CostGuard(settings, MODEL)
    started = clock()
    stats = {"fertig": 0, "abgebrochen": 0, "offen": 0, "veraltet": 0, "stopp": ""}
    with span("agent_run", **{"jobfinder.run_id": run_id}) as current:
        jobs, sheets = load_review_jobs(), fact_sheets()
        if basis is not None:
            stats["veraltet"] = note_outdated(jobs, sheets, basis)
        waiting = waiting_jobs(jobs, sheets)
        ads = read_jobs(dataset_name(JOBS_FILE), [job["recommendation_id"] for job in waiting])
        for position, job in enumerate(waiting):
            ad = ads.get(job["recommendation_id"])
            if ad is None:
                continue  # still recommended, but its details are gone
            try:
                if clock() - started > RUN_SECONDS:
                    raise AgentStopped("Zeitbudget des Laufs erreicht")
                # Stored under the review's id, so the review finds the sheet.
                outcome = write_fact_sheet(
                    {**ad, "id": job["id"]},
                    profile_text,
                    guard,
                    client,
                    settings,
                    today or date.today(),
                    run_id,
                    attempt=job.get("agent_attempt", 1),
                    versions=None if basis is None else job_basis(basis, ad),
                )
            except AgentStopped as stop:
                stats["stopp"] = str(stop)
                stats["offen"] = len(waiting) - position
                break
            stats[outcome] += 1
            if job.get("fact_sheet_rerun"):
                clear_rerun_request(job["id"])
        stats["heute_eur"], stats["monat_eur"] = spent_today_and_this_month()
        annotate(
            current,
            **{
                "jobfinder.waiting": len(waiting),
                "jobfinder.fertig": stats["fertig"],
                "jobfinder.abgebrochen": stats["abgebrochen"],
                "jobfinder.offen": stats["offen"],
                "jobfinder.veraltet": stats["veraltet"],
                "jobfinder.stopped": bool(stats["stopp"]),
            },
        )
    return stats


def waiting_jobs(jobs=None, sheets=None):
    """Return the jobs the agent should do next, exactly as the review sees them, best first.

    The review's own list decides ids and states, because it maps a listing to
    the memory entry the user decided on, which can carry another id. First
    come the jobs the user asked to evaluate again. Then undecided jobs without
    a fact sheet, or whose last attempt may be retried, that pass the gate,
    entry level (experience rank 0) or a score above 50, and that the review
    shows by default: money for sheets nobody sees would be wasted. Each job
    carries the number of its attempt (agent_attempt).
    """
    jobs = load_review_jobs() if jobs is None else jobs
    sheets = fact_sheets() if sheets is None else sheets
    picked = []
    for job in jobs:
        sheet = sheets.get(job["id"])
        if not job.get("recommendation_id"):
            continue
        if job.get("fact_sheet_rerun"):
            picked.append({**job, "agent_attempt": 1})
        elif (
            job.get("workflow_status") in {"new", "review"}
            and (sheet is None or sheet["retryable"])
            and (job.get("experience_rank") == 0 or (job.get("match_percent") or 0) > 50)
            and shown_by_default(job)
        ):
            picked.append({**job, "agent_attempt": sheet["attempts"] + 1 if sheet else 1})
    return sorted(picked, key=lambda job: (not job.get("fact_sheet_rerun"), -(job.get("match_percent") or 0)))


def note_outdated(jobs, sheets, basis):
    """Mark the review's sheets whose basis changed since they were written; return how many.

    Only sheets with a stamp count, and nothing is evaluated again: the review
    shows the hint, and the user decides.
    """
    stamped = [job for job in jobs if job.get("recommendation_id") and (sheets.get(job["id"]) or {}).get("versions")]
    ads = read_jobs(dataset_name(JOBS_FILE), [job["recommendation_id"] for job in stamped])
    changes = {
        job["id"]: changed_parts(sheets[job["id"]]["versions"], job_basis(basis, ads[job["recommendation_id"]]))
        for job in stamped
        if job["recommendation_id"] in ads
    }
    mark_outdated(changes)
    return sum(1 for parts in changes.values() if parts)


def clear_rerun_request(job_id):
    """Clear the request once the job is done, finished or aborted; the review's button is free again."""
    with edit_job(job_id) as entry:
        entry.pop(RERUN_FIELD, None)


def shown_by_default(job):
    """Apply the review's default filters (review.js): no international or junior-hybrid jobs."""
    junior_hybrid = str(job.get("location_precheck") or "").startswith("Junior-Hybrid")
    return not job.get("international") and not junior_hybrid


def model_client(endpoint, environ=os.environ):
    """Model for the Azure deployment, signed in with Entra ID instead of a key.

    In Azure the worker's managed identity signs in, in the hybrid container
    the service principal from the environment, otherwise the az login.
    """
    credential = DefaultAzureCredential(managed_identity_client_id=environ.get("JOBFINDER_MANAGED_IDENTITY_CLIENT_ID"))
    token = get_bearer_token_provider(credential, "https://cognitiveservices.azure.com/.default")
    return agent_model(f"{endpoint.rstrip('/')}/openai/v1/", token)
