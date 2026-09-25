"""The agent's part of a finder run: pick jobs, write fact sheets, stop at the limits.

It runs after the finder has saved its results and never breaks the finder
run. It only starts when the switch is on, a model endpoint is configured
(the Azure worker has one, the local hybrid run not) and a profile exists.
"""

import os
import time
from datetime import date

from azure.identity import DefaultAzureCredential, get_bearer_token_provider
from openai import OpenAI

from job_finder.agent.cost_guard import AgentStopped, CostGuard
from job_finder.agent.profile import configured_profile
from job_finder.agent.runner import MODEL, write_fact_sheet
from job_finder.agent.settings import agent_settings
from job_finder.console import log_event
from job_finder.matching.user_settings import USER_SETTINGS
from job_finder.paths import JOBS_FILE
from job_finder.persistence.agent_usage import spent_today_and_this_month
from job_finder.persistence.fact_sheets import fact_sheets
from job_finder.persistence.postgres_store import read_jobs
from job_finder.persistence.storage import dataset_name
from job_finder.workflow.review_data import load_review_jobs

ENDPOINT_ENV = "JOBFINDER_OPENAI_ENDPOINT"
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
        print(f"  Agent übersprungen: {ENDPOINT_ENV} fehlt (so im lokalen Hybrid-Lauf)")
        return None
    try:
        profile_text, source = configured_profile(environ)
    except ValueError as error:
        print(f"  Agent aus: {error}")
        return None
    print(f"  Profil: {source} · Denkaufwand: {settings.reasoning_effort}")
    try:
        stats = run_agent(settings, profile_text, model_client(endpoint, environ))
    except Exception as error:
        # The finder's results are saved already; only the agent's part fails.
        print(f"  Agent abgebrochen: {type(error).__name__}")
        log_event("agent_failed", run_id=run_id, level="error", error=type(error).__name__)
        return None
    print(
        f"  {stats['fertig']} fertig · {stats['abgebrochen']} abgebrochen · "
        f"{stats['offen']} offen · heute {euro(stats['heute_eur'])} von "
        f"{euro(settings.limits.daily_max_cost_eur)}"
    )
    if stats["stopp"]:
        print(f"  Stopp: {stats['stopp']}")
    log_event(
        "agent_completed",
        run_id=run_id,
        **{key: str(value) if key.endswith("_eur") else value for key, value in stats.items()},
    )
    return stats


def run_agent(settings, profile_text, client, clock=time.monotonic, today=None):
    """Write fact sheets for the best waiting jobs until money, time or jobs run out."""
    guard = CostGuard(settings, MODEL)
    started = clock()
    stats = {"fertig": 0, "abgebrochen": 0, "offen": 0, "stopp": ""}
    waiting = waiting_jobs()
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
            )
        except AgentStopped as stop:
            stats["stopp"] = str(stop)
            stats["offen"] = len(waiting) - position
            break
        stats[outcome] += 1
    stats["heute_eur"], stats["monat_eur"] = spent_today_and_this_month()
    return stats


def waiting_jobs():
    """Return the jobs the agent should do next, exactly as the review sees them, best first.

    The review's own list decides ids and states, because it maps a listing to
    the memory entry the user decided on, which can carry another id. Taken
    are undecided jobs without a fact sheet (finished or aborted) that pass
    the gate, entry level (experience rank 0) or a score above 50, and that the
    review shows by default: money for sheets nobody sees would be wasted.
    """
    done = fact_sheets().keys()
    jobs = [
        job
        for job in load_review_jobs()
        if job.get("recommendation_id")
        and job.get("workflow_status") in {"new", "review"}
        and job["id"] not in done
        and (job.get("experience_rank") == 0 or (job.get("match_percent") or 0) > 50)
        and shown_by_default(job)
    ]
    return sorted(jobs, key=lambda job: job.get("match_percent") or 0, reverse=True)


def shown_by_default(job):
    """Apply the review's default filters (review.js): no international or junior-hybrid jobs."""
    junior_hybrid = str(job.get("location_precheck") or "").startswith("Junior-Hybrid")
    return not job.get("international") and not junior_hybrid


def model_client(endpoint, environ=os.environ):
    """Client for the Azure deployment, signed in with Entra ID instead of a key.

    In Azure the worker's managed identity signs in, locally the az login.
    """
    credential = DefaultAzureCredential(
        managed_identity_client_id=environ.get("JOBFINDER_MANAGED_IDENTITY_CLIENT_ID")
    )
    token = get_bearer_token_provider(credential, "https://cognitiveservices.azure.com/.default")
    return OpenAI(
        base_url=f"{endpoint.rstrip('/')}/openai/v1/", api_key=token, timeout=120, max_retries=2
    )


def euro(value):
    return f"{value:.2f} €".replace(".", ",")
