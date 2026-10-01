"""Runs labelled cases through the agent and a one-call baseline, grades the fact sheets, writes a report.

A run costs real money on the Azure OpenAI deployment, so it starts only from
the command line and stops at its own budget. RunLedger keeps that cost out
of the shared ledger in PostgreSQL: the production agent's daily and monthly
limits stay untouched, while the Azure budget still sees the spending.
Nothing is stored in the database; fact sheets go only into the report.
"""

import hashlib
import json
import subprocess
import time
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import yaml
from langchain_core.messages import HumanMessage

from evals.grading import grade, summarize
from job_finder.agent.cost_guard import AgentStopped, CostGuard, JobLimitReached, euro
from job_finder.agent.fact_sheet import parse_fact_sheet
from job_finder.agent.instructions import RULES, instructions, job_prompt, profile_with_places
from job_finder.agent.runner import (
    MAX_OUTPUT_TOKENS,
    MODEL,
    OUTPUT_FORMAT,
    draft_fact_sheet,
    forget,
    invoke,
    keep_seen_sources,
    urls_in_ad,
    usage_of,
)
from job_finder.agent.settings import AgentLimits, AgentSettings
from job_finder.paths import PROJECT_DIR

EVALS_DIR = Path(__file__).resolve().parent
DEFAULT_CASES = EVALS_DIR / "cases" / "synthetic.yaml"
RESULTS_DIR = EVALS_DIR / "results"
VARIANTS = ("agent", "einzelaufruf")


class RunLedger:
    """What one eval run has cost so far; the run budget stands in for the day and month limits."""

    def __init__(self):
        self.total = Decimal(0)

    def spent(self):
        return self.total, self.total

    def record(self, job_id, model, usage, cost):
        self.total += cost


def load_cases(path=DEFAULT_CASES):
    """Read a case file and add what the agent needs: profile with places and decision rows."""
    raw = Path(path).read_bytes()
    dataset = yaml.safe_load(raw)
    dataset["profile_text"] = profile_with_places(dataset["profile"], dataset.get("settings") or {})
    # The rows as persistence.decisions.decided_jobs returns them for the past_decisions tool.
    dataset["decision_rows"] = [
        (
            row["job_id"],
            row["title"],
            row["company"],
            row["status"],
            row.get("rating"),
            row.get("note"),
            row["decided_on"],
        )
        for row in dataset.get("decisions") or []
    ]
    dataset["sha256"] = hashlib.sha256(raw).hexdigest()
    return dataset


def eval_settings(budget, effort):
    """Use the production limits per job, no paid web search, and the run budget as day and month limit."""
    limits = AgentLimits(job_max_web_searches=0, daily_max_cost_eur=budget, monthly_max_cost_eur=budget)
    return AgentSettings(True, limits, reasoning_effort=effort)


def agent_variant(job, dataset, guard, model, settings):
    """Run the production graph with its tools; past_decisions searches the case file's decisions."""
    profile = dataset["profile_text"]
    return draft_fact_sheet(job, profile, guard, model, settings, dataset["today"], dataset["decision_rows"])


def single_call_variant(job, dataset, guard, model, settings):
    """Ask once with the same rules, profile and ad, without graph and tools: the baseline."""
    guard.start_job(job["id"])
    guard.before_model_call()
    bound = model.chat.bind(
        response_format=OUTPUT_FORMAT,
        instructions=instructions(dataset["profile_text"]),
        reasoning={"effort": settings.reasoning_effort},
        max_output_tokens=MAX_OUTPUT_TOKENS,
    )
    answer = invoke(bound, [HumanMessage(job_prompt(job, dataset["today"], 0))])
    try:
        guard.after_model_call(usage_of(answer, model.billed_searches))
        if answer.response_metadata.get("status") != "completed":
            reason = (answer.response_metadata.get("incomplete_details") or {}).get("reason")
            raise JobLimitReached(f"Stelle abgebrochen: Antwort unvollständig ({reason or 'unbekannt'})")
        sheet = parse_fact_sheet(answer.text)
    finally:
        forget(model, [answer.response_metadata.get("id")])
    return sheet, keep_seen_sources(sheet, urls_in_ad(job))


VARIANT_RUNNERS = {"agent": agent_variant, "einzelaufruf": single_call_variant}


def run_case(case, variant, dataset, guard, model, settings, clock=time.monotonic):
    """Let one variant write the fact sheet of one case and grade it; AgentStopped ends the run."""
    job = {**case["job"], "id": f"eval:{case['id']}"}
    started = clock()
    sheet, dropped, status, reason = None, [], "fertig", ""
    try:
        sheet, dropped = VARIANT_RUNNERS[variant](job, dataset, guard, model, settings)
    except JobLimitReached as stop:
        status, reason = "abgebrochen", str(stop)
    except ValueError as error:
        status, reason = "abgebrochen", f"Steckbrief unbrauchbar: {error}"
    return {
        "fall": case["id"],
        "kategorie": case["category"],
        "variante": variant,
        "status": status,
        "grund": reason,
        "urteil": sheet["fazit"]["stufe"] if sheet else None,
        "pruefungen": grade(case, sheet) if sheet else {},
        "verworfene_quellen": dropped,
        "kosten_eur": str(guard.job_cost),
        "modellaufrufe": guard.model_calls,
        "werkzeugaufrufe": guard.tool_calls,
        "sekunden": round(clock() - started, 1),
        "steckbrief": sheet,
    }


def run_evals(dataset, variants, model, budget, effort, repeat=1, progress=None, clock=time.monotonic):
    """Run every case with every variant, case by case, until all are done or the budget is used up.

    The variants alternate per case, so an early stop leaves comparable results.
    """
    ledger = RunLedger()
    settings = eval_settings(budget, effort)
    guard = CostGuard(settings, MODEL, ledger)
    results, stop = [], ""
    try:
        for round_number in range(1, repeat + 1):
            for case in dataset["cases"]:
                for variant in variants:
                    result = {
                        **run_case(case, variant, dataset, guard, model, settings, clock),
                        "durchgang": round_number,
                    }
                    results.append(result)
                    if progress:
                        progress(result)
    except AgentStopped as error:
        spent_up = ledger.total >= budget
        stop = f"Budget des Laufs erreicht: {euro(ledger.total)} von {euro(budget)}" if spent_up else str(error)
    return {"ergebnisse": results, "stopp": stop, "kosten_eur": ledger.total}


def write_report(dataset, run, variants, budget, effort, repeat, out_dir=RESULTS_DIR, now=None):
    """Write the raw results as JSON and a summary as Markdown; return both paths."""
    now = now or datetime.now()
    meta = {
        "datum": now.isoformat(timespec="minutes"),
        "commit": git_commit(),
        "datensatz": {
            "name": dataset["name"],
            "version": dataset["version"],
            "sha256": dataset["sha256"][:12],
            "faelle": len(dataset["cases"]),
        },
        "regeln_sha256": short_hash(RULES),
        "profil_sha256": short_hash(dataset["profile_text"]),
        "modell": MODEL,
        "denkaufwand": effort,
        "websuche": "aus",
        "varianten": list(variants),
        "durchgaenge": repeat,
        "budget_eur": str(budget),
        "kosten_eur": str(run["kosten_eur"]),
        "stopp": run["stopp"],
    }
    summary = summarize(run["ergebnisse"])
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{now:%Y-%m-%d-%H%M}-{dataset['name']}"
    json_path, markdown_path = out_dir / f"{stem}.json", out_dir / f"{stem}.md"
    raw = {"meta": meta, "zusammenfassung": summary, "ergebnisse": run["ergebnisse"]}
    json_path.write_text(json.dumps(raw, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    markdown_path.write_text(markdown(dataset, meta, summary, run["ergebnisse"], variants), encoding="utf-8")
    return json_path, markdown_path


def markdown(dataset, meta, summary, results, variants):
    """Write the readable report: totals per variant, verdicts per category and per case."""
    data = meta["datensatz"]
    lines = [
        f"# Eval-Bericht: {data['name']} v{data['version']}",
        "",
        f"- Stand: {meta['datum']}, Commit `{meta['commit']}`, Datensatz `{data['sha256']}` "
        f"({data['faelle']} Fälle), Regeln `{meta['regeln_sha256']}`",
        f"- Modell `{meta['modell']}`, Denkaufwand {meta['denkaufwand']}, Websuche {meta['websuche']}, "
        f"{meta['durchgaenge']} Durchgang/Durchgänge",
        f"- Kosten {euro(Decimal(meta['kosten_eur']), 3)} von höchstens {euro(Decimal(meta['budget_eur']))}",
    ]
    if meta["stopp"]:
        lines.append(f"- **Vorzeitig gestoppt:** {meta['stopp']}")
    lines += [
        "",
        "## Je Variante",
        "",
        "| Variante | Urteil | Richtung | Ampeln | Ohne erfundene Beträge | Abbrüche | Verworfene Links "
        "| Werkzeugaufrufe | Kosten je Fall | Sekunden je Fall |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for variant in variants:
        entry = summary["varianten"].get(variant)
        if not entry:
            continue
        runs = entry["laeufe"]
        lines.append(
            f"| {variant} | {ratio(entry['urteil'])} | {ratio(entry['richtung'])} | {ratio(entry['ampeln'])} "
            f"| {ratio(entry['keine_erfundene_zahl'])} | {entry['abgebrochen']} | {entry['verworfene_links']} "
            f"| {entry['werkzeugaufrufe']} | {euro(entry['kosten_eur'] / runs, 3)} | {entry['sekunden'] / runs:.0f} |"
        )
    lines += [
        "",
        "## Urteil je Kategorie",
        "",
        f"| Kategorie | {' | '.join(variants)} |",
        "|---|" + "---|" * len(variants),
    ]
    for category, per_variant in summary["kategorien"].items():
        lines.append(f"| {category} | " + " | ".join(ratio(per_variant.get(v, [0, 0])) for v in variants) + " |")
    lines += ["", "## Je Fall", "", f"| Fall | Soll | {' | '.join(variants)} |", "|---|---|" + "---|" * len(variants)]
    for case in dataset["cases"]:
        cells = [verdicts(results, case["id"], variant) for variant in variants]
        lines.append(f"| {case['id']} | {', '.join(case['expected']['fazit'])} | " + " | ".join(cells) + " |")
    lines += [
        "",
        "Urteil: Fazit unter den erlaubten Stufen. Richtung: auf der richtigen Seite (bewerben/erst klären "
        "gegenüber eher streichen/streichen). Ampeln: die erwarteten Ampeln der Fälle. Ein Abbruch zählt "
        "als falsches Urteil. Methode und Datensatz: docs/development.md, Abschnitt Evals.",
        "",
    ]
    return "\n".join(lines)


def ratio(counts):
    passed, total = counts
    return f"{passed}/{total} ({passed / total:.0%})".replace("%", " %") if total else "–"


def verdicts(results, case_id, variant):
    """List the variant's verdicts for one case over all rounds, each marked right or wrong."""
    marks = []
    for result in results:
        if result["fall"] == case_id and result["variante"] == variant:
            passed = result["pruefungen"].get("urteil")
            marks.append(f"{result['urteil'] or 'abgebrochen'} {'✓' if passed else '✗'}")
    return ", ".join(marks) or "–"


def git_commit():
    """Return the checked-out commit, marked when the working tree differs from it."""
    try:
        commit = run_git("rev-parse", "--short=12", "HEAD")
        return f"{commit} (geändert)" if run_git("status", "--porcelain", "--untracked-files=no") else commit
    except (OSError, subprocess.CalledProcessError):
        return "unbekannt"


def run_git(*args):
    return subprocess.run(["git", *args], capture_output=True, text=True, check=True, cwd=PROJECT_DIR).stdout.strip()


def short_hash(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]
