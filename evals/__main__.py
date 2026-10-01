"""Start an eval run against the Azure OpenAI deployment: python -m evals --help.

It costs real money, signs in like the agent (locally through the az login)
and stops at its budget; docs/development.md, section Evals, has the details.
"""

import argparse
import os
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path

from evals.harness import DEFAULT_CASES, RESULTS_DIR, VARIANTS, load_cases, run_evals, write_report
from job_finder.agent.cost_guard import euro
from job_finder.agent.run import ENDPOINT_ENV, model_client
from job_finder.agent.settings import REASONING_EFFORTS

MIN_BUDGET_EUR, MAX_BUDGET_EUR = Decimal("0.10"), Decimal("5")


def budget(text):
    """Read a euro amount within the allowed range, so a typo cannot open the purse."""
    try:
        value = Decimal(text.replace(",", "."))
    except InvalidOperation:
        raise argparse.ArgumentTypeError("Eurobetrag erwartet, etwa 1.00") from None
    if not MIN_BUDGET_EUR <= value <= MAX_BUDGET_EUR:
        raise argparse.ArgumentTypeError(f"muss zwischen {euro(MIN_BUDGET_EUR)} und {euro(MAX_BUDGET_EUR)} liegen")
    return value


def parser():
    parser = argparse.ArgumentParser(
        prog="python -m evals",
        description="Steckbriefe zu beschrifteten Fällen schreiben lassen und prüfen. Kostet echtes Geld.",
    )
    parser.add_argument("--cases", default=str(DEFAULT_CASES), help="Falldatei (YAML)")
    parser.add_argument("--variant", action="append", choices=VARIANTS, help="Variante; ohne Angabe beide")
    parser.add_argument("--only", action="append", metavar="FALL", help="nur diese Fall-ID, mehrfach möglich")
    parser.add_argument("--repeat", type=int, default=1, choices=range(1, 6), metavar="1-5", help="Durchgänge")
    parser.add_argument("--budget", type=budget, default=Decimal("1.00"), help="hartes Limit in Euro (Standard 1,00)")
    parser.add_argument("--effort", choices=REASONING_EFFORTS, default="medium", help="Denkaufwand des Modells")
    parser.add_argument("--out", default=str(RESULTS_DIR), help="Ordner für Bericht und Rohdaten")
    return parser


def main(argv=None, environ=os.environ):
    arguments = parser()
    args = arguments.parse_args(argv)
    endpoint = environ.get(ENDPOINT_ENV)
    if not endpoint:
        arguments.error(f"{ENDPOINT_ENV} fehlt: Endpunkt des Azure-OpenAI-Kontos, etwa aus terraform output")
    dataset = load_cases(args.cases)
    if args.only:
        unknown = sorted(set(args.only) - {case["id"] for case in dataset["cases"]})
        if unknown:
            arguments.error(f"Unbekannte Fälle: {', '.join(unknown)}")
        dataset["cases"] = [case for case in dataset["cases"] if case["id"] in args.only]
    variants = tuple(dict.fromkeys(args.variant or VARIANTS))
    print(
        f"{len(dataset['cases'])} Fälle × {len(variants)} Varianten × {args.repeat} Durchgang/Durchgänge, "
        f"höchstens {euro(args.budget)}"
    )
    run = run_evals(
        dataset, variants, model_client(endpoint, environ), args.budget, args.effort, args.repeat, progress=show
    )
    _json_path, markdown_path = write_report(
        dataset, run, variants, args.budget, args.effort, args.repeat, Path(args.out)
    )
    if run["stopp"]:
        print(f"Gestoppt: {run['stopp']}")
    print(f"Kosten {euro(run['kosten_eur'], 3)} · Bericht: {markdown_path}")
    return 0


def show(result):
    """One line per finished case, so a long run shows its progress (plain words for any console)."""
    verdict = result["urteil"] or result["grund"]
    mark = "richtig" if result["pruefungen"].get("urteil") else "falsch"
    print(f"  {result['fall']} · {result['variante']}: {verdict}, {mark} ({euro(Decimal(result['kosten_eur']), 3)})")


if __name__ == "__main__":
    sys.exit(main())
