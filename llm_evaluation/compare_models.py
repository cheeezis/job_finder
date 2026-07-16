"""Run the blind local-LLM comparison against the fixed job test set."""

import argparse

from job_agent.console import configure_utf8_output
from job_agent.llm.errors import LLMError
from job_agent.llm.ollama import DEFAULT_MODEL, OllamaClient, OllamaError
from llm_evaluation.benchmark import (
    JOB_ANALYSIS_SPLITS,
    run_job_analysis_evaluation,
    run_model_benchmark,
    run_two_stage_evaluation,
    write_benchmark_result,
    write_job_analysis_result,
    write_two_stage_result,
)
from llm_evaluation.profile_matching import (
    run_profile_match_evaluation,
    write_profile_match_result,
)


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "models",
        nargs="*",
        help="Model names (provider-specific default when omitted)",
    )
    parser.add_argument(
        "--provider",
        choices=("ollama", "openai"),
        default="ollama",
        help="LLM provider (default: ollama)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        help="Only evaluate the first N jobs for a quick smoke test",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=300,
        help="Maximum seconds per Ollama request",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--job-analysis-only",
        action="store_true",
        help="Extract job facts without profile matching or recommendations",
    )
    mode.add_argument(
        "--two-stage",
        action="store_true",
        help="Extract job facts and match them to the profile without scoring",
    )
    mode.add_argument(
        "--profile-match-only",
        action="store_true",
        help="Match cached job analyses to the profile without rerunning stage 1",
    )
    parser.add_argument(
        "--analysis-model",
        default=DEFAULT_MODEL,
        help=f"Model used to create the stage-1 cache (default: {DEFAULT_MODEL})",
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

    if args.provider == "openai":
        from job_agent.llm.openai import (
            DEFAULT_MODEL as default_openai_model,
            OpenAIClient,
        )

        models = args.models or [default_openai_model]
        try:
            client = OpenAIClient(timeout=args.timeout)
        except LLMError as error:
            raise SystemExit(str(error)) from error
    else:
        models = args.models or [DEFAULT_MODEL]
        client = OllamaClient(timeout=args.timeout)
        try:
            installed = set(client.list_models())
        except OllamaError as error:
            raise SystemExit(f"Ollama nicht erreichbar: {error}") from error

        missing = [model for model in models if model not in installed]
        if missing:
            names = ", ".join(missing)
            raise SystemExit(f"Nicht lokal installiert: {names}")

    for model in models:
        if args.profile_match_only:
            result = run_profile_match_evaluation(
                args.analysis_model,
                model,
                client,
                limit=args.limit,
                split=args.split,
            )
            path = write_profile_match_result(result)
            print_quality_summary(model, result["summary"])
            print(f"Gespeichert: {path}")
            continue

        if args.two_stage:
            result = run_two_stage_evaluation(
                model,
                client,
                limit=args.limit,
                split=args.split,
            )
            path = write_two_stage_result(result)
            print_quality_summary(model, result["summary"])
            print(f"Gespeichert: {path}")
            continue

        if args.job_analysis_only:
            result = run_job_analysis_evaluation(
                model,
                client,
                limit=args.limit,
                split=args.split,
            )
            path = write_job_analysis_result(result)
            summary = result["summary"]
            print(
                f"{model}: {summary['valid_responses']}/{summary['jobs']} gueltig, "
                f"{summary['average_seconds_per_job']} s/Job"
            )
            print(f"Gespeichert: {path}")
            continue

        result = run_model_benchmark(model, client, limit=args.limit)
        path = write_benchmark_result(result)
        print_quality_summary(model, result["summary"])
        print(f"Gespeichert: {path}")


if __name__ == "__main__":
    main()
