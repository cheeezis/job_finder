"""Build a private case file from the user's own decisions: real profile, real ads, real verdicts.

The file holds personal data and stays local (evals/private/ is ignored by
Git). Everything is read in read-only snapshots: the review's list of jobs as
the agent would get them (past the gate and the default filters), the ad of
each from the jobs dataset, and the decision as the expected direction. The
past_decisions tool later only sees decisions made before the case's own.

Run from the checkout with the personal files:
python -m evals.private_cases --azure [--limit 40]
"""

import argparse
import os
import random
from datetime import date
from pathlib import Path

import yaml
from dotenv import dotenv_values

from job_finder.agent.profile import configured_profile
from job_finder.agent.run import shown_by_default
from job_finder.agent.tools import DECISION_LABELS
from job_finder.matching.user_settings import USER_SETTINGS
from job_finder.paths import JOBS_FILE, PROJECT_DIR
from job_finder.persistence.decisions import decided_jobs
from job_finder.persistence.postgres_store import read_jobs
from job_finder.persistence.storage import dataset_name
from job_finder.workflow.review_data import load_review_jobs

PRIVATE_DIR = Path(__file__).resolve().parent / "private"
AZURE_ENV_FILE = PROJECT_DIR / ".env.postgres-azure"
# Decisions that wanted the job, even when an application failed later; "closed" says nothing about it.
TOWARDS = {
    "interesting",
    "inquiry",
    "waiting",
    "applied",
    "response",
    "interview",
    "rejected",
    "no_response",
    "offer",
    "withdrawn",
}
AWAY = {"ignored"}
JOB_FIELDS = (
    "title",
    "company",
    "locations",
    "work_mode",
    "remote_percentage",
    "employment_type",
    "salary_min_eur",
    "salary_max_eur",
    "published_at",
    "sources",
    "description_clean",
)


def private_cases(today=None, limit=None, seed=1):
    """Return the case file as a mapping, in the shape of evals/cases/synthetic.yaml."""
    decisions = {row[0]: row for row in decided_jobs()}
    candidates = [
        job
        for job in load_review_jobs()
        if job["id"] in decisions
        and decisions[job["id"]][3] in TOWARDS | AWAY
        and job.get("recommendation_id")
        and (job.get("experience_rank") == 0 or (job.get("match_percent") or 0) > 50)
        and shown_by_default(job)
    ]
    ads = read_jobs(dataset_name(JOBS_FILE), [job["recommendation_id"] for job in candidates])
    cases = [
        case_of(job, ads[job["recommendation_id"]], decisions[job["id"]])
        for job in candidates
        if (ads.get(job["recommendation_id"]) or {}).get("description_clean")
    ]
    profile_text, _source = configured_profile()
    today = today or date.today()
    return {
        "name": "privat",
        "version": today.isoformat(),
        "today": today,
        "settings": {
            "search": {key: USER_SETTINGS.get("search", {}).get(key) for key in ("local_location", "local_radius_km")},
            "matching": {
                key: USER_SETTINGS.get("matching", {}).get(key) for key in ("local_places", "commuter_locations")
            },
        },
        "profile": profile_text,
        "decisions": [
            dict(zip(("job_id", "title", "company", "status", "rating", "note", "decided_on"), row, strict=True))
            for row in decisions.values()
        ],
        "cases": balanced(cases, limit, seed),
    }


def case_of(job, ad, decision):
    """One case: the ad as the agent gets it and the user's decision as the expected side."""
    _job_id, _title, _company, status, _rating, note, decided_on = decision
    towards = status in TOWARDS
    reason = f"Deine Entscheidung: {DECISION_LABELS.get(status, status)}"
    return {
        "id": job["id"],
        "category": "dafür" if towards else "dagegen",
        "why": f"{reason} – {note}" if note else reason,
        "decided_on": decided_on,
        "expected": {"fazit": ["bewerben", "erst_klaeren"] if towards else ["eher_streichen", "streichen"]},
        "job": {key: ad[key] for key in JOB_FIELDS if ad.get(key) is not None},
    }


def balanced(cases, limit, seed):
    """Up to limit cases, half for and half against where possible; a fixed seed keeps the choice repeatable."""
    if not limit or len(cases) <= limit:
        return cases
    shuffled = random.Random(seed).sample(cases, len(cases))
    towards = [case for case in shuffled if case["category"] == "dafür"]
    away = [case for case in shuffled if case["category"] == "dagegen"]
    take_towards = min(len(towards), max(limit // 2, limit - len(away)))
    return towards[:take_towards] + away[: limit - take_towards]


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python -m evals.private_cases", description=__doc__.splitlines()[0])
    parser.add_argument("--azure", action="store_true", help="Azure-Datenbank (.env.postgres-azure) statt der lokalen")
    parser.add_argument("--limit", type=int, help="höchstens so viele Fälle, je zur Hälfte dafür und dagegen")
    parser.add_argument("--out", default=str(PRIVATE_DIR / "faelle.yaml"), help="Zieldatei (bleibt lokal)")
    args = parser.parse_args(argv)
    if args.azure:
        # Only the connection URL, as the local scripts use it; nothing is printed.
        os.environ["JOBFINDER_DATABASE_URL"] = dotenv_values(AZURE_ENV_FILE)["JOBFINDER_DATABASE_URL"]
    cases = private_cases(limit=args.limit)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(yaml.safe_dump(cases, allow_unicode=True, sort_keys=False, width=120), encoding="utf-8")
    towards = sum(case["category"] == "dafür" for case in cases["cases"])
    print(f"{len(cases['cases'])} Fälle ({towards} dafür, {len(cases['cases']) - towards} dagegen) in {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
