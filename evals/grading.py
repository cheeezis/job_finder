"""Grading of fact sheets against the expectations of their cases, without a model.

Every check compares one field, so a failed check points at what went wrong:
the verdict (one of the allowed ones), its direction, the expected traffic
lights, and whether the texts name money the ad does not mention.
"""

import re
from decimal import Decimal

from job_finder.agent.fact_sheet import FIXED_LINES

# Verdicts that lead towards an application; the others lead away from it.
POSITIVE = {"bewerben", "erst_klaeren"}
# An amount with a currency, as in "50.000 €", "55k EUR" or "4.200 Euro".
MONEY = re.compile(r"\d[\d.,]*\s*(?:k|tsd\.?|tausend)?\s*(?:€|eur\b|euro\b)", re.IGNORECASE)
GROUPED_CHECKS = ("richtung", "ampeln", "keine_erfundene_zahl")


def grade(case, sheet):
    """Return {check: passed} for one fact sheet; checks that do not apply are left out."""
    expected = case["expected"]
    verdict = sheet["fazit"]["stufe"]
    checks = {"urteil": verdict in expected["fazit"]}
    sides = {stufe in POSITIVE for stufe in expected["fazit"]}
    if len(sides) == 1:
        checks["richtung"] = (verdict in POSITIVE) in sides
    for key, lights in (expected.get("ampeln") or {}).items():
        checks[f"ampel:{key}"] = sheet[key]["ampel"] in lights
    if not money_in_ad(case["job"]):
        checks["keine_erfundene_zahl"] = not any(MONEY.search(text) for text in texts(sheet))
    return checks


def money_in_ad(job):
    return bool(
        job.get("salary_min_eur") or job.get("salary_max_eur") or MONEY.search(job.get("description_clean") or "")
    )


def texts(sheet):
    """Every text of a fact sheet that the review shows."""
    yield from (sheet[key]["text"] for key, _label in FIXED_LINES)
    yield from (line["text"] for line in sheet["zusatz"])
    yield sheet["fazit"]["text"]
    yield sheet["kurzgrund"]


def summarize(results):
    """Count passed checks, aborts, dropped links, cost and time per variant, and verdicts per category.

    A run without a fact sheet counts as a wrong verdict, as the user would
    not get one either; the grouped checks only count runs that have one.
    """
    variants, categories = {}, {}
    for result in results:
        entry = variants.setdefault(result["variante"], empty_entry())
        checks = result["pruefungen"]
        entry["laeufe"] += 1
        entry["abgebrochen"] += result["status"] != "fertig"
        entry["urteil"][0] += checks.get("urteil", False)
        entry["urteil"][1] += 1
        for name, passed in checks.items():
            group = "ampeln" if name.startswith("ampel:") else name
            if group in GROUPED_CHECKS:
                entry[group][0] += passed
                entry[group][1] += 1
        entry["verworfene_links"] += len(result["verworfene_quellen"])
        entry["werkzeugaufrufe"] += result["werkzeugaufrufe"]
        entry["kosten_eur"] += Decimal(result["kosten_eur"])
        entry["sekunden"] += result["sekunden"]
        verdicts = categories.setdefault(result["kategorie"], {}).setdefault(result["variante"], [0, 0])
        verdicts[0] += checks.get("urteil", False)
        verdicts[1] += 1
    return {"varianten": variants, "kategorien": categories}


def empty_entry():
    return {
        "laeufe": 0,
        "abgebrochen": 0,
        "urteil": [0, 0],
        **{name: [0, 0] for name in GROUPED_CHECKS},
        "verworfene_links": 0,
        "werkzeugaufrufe": 0,
        "kosten_eur": Decimal(0),
        "sekunden": 0.0,
    }
