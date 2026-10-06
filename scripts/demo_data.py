"""Fill a separate demo database with made-up jobs, decisions and fact sheets, then optionally serve the review.

Everything comes from demo/demo.yaml and the fictional ads in evals/cases/synthetic.yaml; the real
scoring pipeline rates them with the demo settings. The script only writes to the local database
jobfinder_demo next to the development database from .env.postgres (docker compose), and empties it
on every run, so it never touches real data.

    uv run python scripts/demo_data.py --serve      # then http://127.0.0.1:8770
"""

import argparse
import os
import sys
import webbrowser
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import psycopg
import yaml
from dotenv import dotenv_values
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR))

DEMO_FILE = PROJECT_DIR / "demo" / "demo.yaml"
CASES_FILE = PROJECT_DIR / "evals" / "cases" / "synthetic.yaml"
DEMO_DATABASE = "jobfinder_demo"
LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}
WORK_MODES = {"vor Ort": "onsite", "hybrid": "hybrid", "remote": "remote"}


def demo_database_urls(environ=os.environ, env_file=PROJECT_DIR / ".env.postgres"):
    """Return the admin URL and the demo database URL on the same local server; refuse any other host."""
    admin = environ.get("JOBFINDER_ADMIN_DATABASE_URL") or dotenv_values(env_file).get("JOBFINDER_ADMIN_DATABASE_URL")
    if not admin:
        raise SystemExit("Keine lokale Datenbank gefunden: erst scripts/setup_postgres.py und docker compose up.")
    if conninfo_to_dict(admin).get("host") not in LOCAL_HOSTS:
        raise SystemExit("Die Demo läuft nur gegen eine lokale Datenbank.")
    return admin, make_conninfo(admin, dbname=DEMO_DATABASE)


def create_database(admin_url):
    with psycopg.connect(admin_url, autocommit=True, connect_timeout=10) as connection:
        if not connection.execute("SELECT 1 FROM pg_database WHERE datname = %s", (DEMO_DATABASE,)).fetchone():
            connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(DEMO_DATABASE)))


def use_demo_environment(demo_url, settings, environ=os.environ):
    """Point the app at the demo database and settings; job_finder reads both when first imported."""
    environ["JOBFINDER_DATABASE_URL"] = demo_url
    environ["JOBFINDER_ADMIN_DATABASE_URL"] = demo_url
    environ["JOBFINDER_USER_SETTINGS"] = yaml.safe_dump(settings, allow_unicode=True)


def demo_ads(demo, cases):
    """Yield (id, ad) for the fictional ads of the eval cases and the demo's extra jobs."""
    for case in cases["cases"]:
        yield case["id"], case["job"]
    for extra in demo.get("extra_jobs") or []:
        yield extra["id"], extra["job"]


def demo_job(case_id, ad, today, cases_today, now):
    """Build a job like a source would; dates keep their distance to today, so the demo never ages."""
    from job_finder.models import Job, JobSource, WorkMode

    published = date.fromisoformat(ad["published_at"]) + (today - cases_today) if ad.get("published_at") else None
    return Job(
        id=f"demo:{case_id}",
        title=ad["title"],
        company=ad["company"],
        locations=list(ad.get("locations") or []),
        sources=[JobSource(source=item["source"], url=item["url"], source_id=case_id) for item in ad["sources"]],
        description_raw=ad["description_clean"],
        description_clean=ad["description_clean"],
        work_mode=WorkMode(WORK_MODES.get(ad.get("work_mode"), "unknown")),
        remote_percentage=ad.get("remote_percentage"),
        employment_type=ad.get("employment_type"),
        salary_min_eur=ad.get("salary_min_eur"),
        salary_max_eur=ad.get("salary_max_eur"),
        published_at=published,
        fetched_at=now,
        is_new=True,
    )


def seed(demo, cases, today):
    """Empty the demo database and fill it like one finder run plus the demo's decisions and fact sheets."""
    from job_finder.agent.runner import MODEL
    from job_finder.paths import JOBS_FILE, MEMORY_FILE
    from job_finder.persistence.database import initialize, transaction
    from job_finder.persistence.fact_sheets import save_fact_sheet
    from job_finder.persistence.storage import publish_results
    from job_finder.workflow.applications import record_status_change
    from job_finder.workflow.main import build_score_results, combine_listings, evaluate_jobs
    from job_finder.workflow.memory import edit_memory, update_memory
    from job_finder.workflow.reporting import write_recommendations

    initialize()
    with transaction() as connection:
        connection.execute("TRUNCATE job_state, datasets, agent_fact_sheets, agent_usage CASCADE")
    now = datetime.now(UTC)
    jobs = [demo_job(case_id, ad, today, cases["today"], now) for case_id, ad in demo_ads(demo, cases)]
    sources = {source.source for job in jobs for source in job.sources}
    evaluated = evaluate_jobs(jobs)
    with edit_memory(MEMORY_FILE) as memory:
        update_memory(jobs, memory, successful_sources=sources, run_sources=sources)
        # As if the finder had first seen the jobs a month ago, before every event of the demo's history;
        # otherwise their "new" step would be the latest and win over the applications.
        for entry in memory.values():
            entry["first_seen_at"] = (now - timedelta(days=30)).isoformat()
        for case_id, events in demo["history"].items():
            for status, days_ago in events:
                record_status_change(memory[f"demo:{case_id}"], status, (today - timedelta(days=days_ago)).isoformat())
    evaluated = combine_listings(evaluated)
    results = build_score_results(evaluated)
    publish_results([job for job, _result in evaluated], results, jobs_path=JOBS_FILE, writer=write_recommendations)
    for case_id, sheet in demo["fact_sheets"].items():
        save_fact_sheet(f"demo:{case_id}", MODEL, sheet, Decimal("0.005"))
    return results


def serve(port, open_browser):
    import threading

    import uvicorn

    from job_finder.review_app import create_app

    url = f"http://127.0.0.1:{port}"
    print(f"Demo-Review unter {url} – Strg+C beendet sie.", flush=True)
    if open_browser:
        threading.Timer(1.0, webbrowser.open, args=(url,)).start()
    uvicorn.run(create_app(), host="127.0.0.1", port=port, log_level="warning", access_log=False)


def main(argv=None):
    parser = argparse.ArgumentParser(prog="scripts/demo_data.py", description=__doc__.splitlines()[0])
    parser.add_argument("--serve", action="store_true", help="danach die Review mit den Demo-Daten starten")
    parser.add_argument("--port", type=int, default=8770)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args(argv)
    demo = yaml.safe_load(DEMO_FILE.read_text(encoding="utf-8"))
    cases = yaml.safe_load(CASES_FILE.read_text(encoding="utf-8"))
    admin_url, demo_url = demo_database_urls()
    create_database(admin_url)
    use_demo_environment(demo_url, demo["settings"])
    results = seed(demo, cases, date.today())
    print(
        f"Demo-Datenbank {DEMO_DATABASE}: {len(results['included'])} Stellen in der Review, "
        f"{len(results['excluded'])} vom Vorfilter aussortiert."
    )
    if args.serve:
        serve(args.port, not args.no_browser)


if __name__ == "__main__":
    main()
