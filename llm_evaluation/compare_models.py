"""Run OpenAI models against the fixed two-stage job test set."""

import argparse

from job_finder.console import configure_utf8_output
from job_finder.llm.config import DEFAULT_LLM_SETTINGS
from job_finder.llm.errors import LLMError
from job_finder.llm.openai import OpenAIClient
from llm_evaluation.benchmark import (
    JOB_ANALYSIS_SPLITS,
    run_two_stage_evaluation,
    write_two_stage_result,
)


DEFAULT_MODEL = DEFAULT_LLM_SETTINGS.model


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "models",
        nargs="*",
        help=f"OpenAI model names (default: {DEFAULT_MODEL})",
    )
    parser.add_argument(
        "--limit",
        type=int,
        help="Only evaluate the first N jobs for a quick smoke test",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=DEFAULT_LLM_SETTINGS.timeout_seconds,
        help="Maximum seconds per OpenAI request",
    )
    parser.add_argument(
        "--split",
        choices=JOB_ANALYSIS_SPLITS,
        default="development",
        help="Job-analysis split to evaluate (default: development)",
    )
    return parser.parse_args()


def print_quality_summary(model, summary):
    """Print the comparable recommendation metrics shared by fit modes."""
    print(
        f"{model}: {summary['exact_matches']}/{summary['jobs']} exakt, "
        f"{summary['within_one_band_rate']:.0%} max. eine Stufe entfernt, "
        f"{summary['dangerous_false_positives']} Fehlalarm(e), "
        f"{summary['missed_positive_jobs']} verpasste Treffer, "
        f"{summary['valid_responses']}/{summary['jobs']} gueltig, "
        f"{summary['average_seconds_per_job']} s/Job"
    )


def main():
    configure_utf8_output()
    args = parse_args()
    models = args.models or [DEFAULT_MODEL]
    try:
        client = OpenAIClient(timeout=args.timeout)
    except LLMError as error:
        raise SystemExit(str(error)) from error

    for model in models:
        result = run_two_stage_evaluation(
            model,
            client,
            limit=args.limit,
            split=args.split,
        )
        path = write_two_stage_result(result)
        print_quality_summary(model, result["summary"])
        print(f"Gespeichert: {path}")


if __name__ == "__main__":
    main()
