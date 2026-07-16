"""Run the blind local-LLM comparison against the fixed job test set."""

import argparse

from job_agent.console import configure_utf8_output
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


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "models",
        nargs="*",
        default=[DEFAULT_MODEL],
        help=f"Ollama model names (default: {DEFAULT_MODEL})",
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
    parser.add_argument(
        "--split",
        choices=JOB_ANALYSIS_SPLITS,
        default="development",
        help="Job-analysis split to evaluate (default: development)",
    )
    return parser.parse_args()


def main():
    configure_utf8_output()
    args = parse_args()
    client = OllamaClient(timeout=args.timeout)

    try:
        installed = set(client.list_models())
    except OllamaError as error:
        raise SystemExit(f"Ollama nicht erreichbar: {error}") from error

    missing = [model for model in args.models if model not in installed]
    if missing:
        names = ", ".join(missing)
        raise SystemExit(f"Nicht lokal installiert: {names}")

    for model in args.models:
        if args.two_stage:
            result = run_two_stage_evaluation(
                model,
                client,
                limit=args.limit,
                split=args.split,
            )
            path = write_two_stage_result(result)
            summary = result["summary"]
            print(
                f"{model}: {summary['valid_responses']}/{summary['jobs']} gueltig, "
                f"{summary['average_seconds_per_job']} s/Job"
            )
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
        summary = result["summary"]
        print(
            f"{model}: {summary['exact_matches']}/{summary['jobs']} exakt, "
            f"{summary['within_one_band_rate']:.0%} max. eine Stufe entfernt, "
            f"{summary['dangerous_false_positives']} Fehlalarm(e), "
            f"{summary['missed_positive_jobs']} verpasste Treffer, "
            f"{summary['valid_responses']}/{summary['jobs']} gueltig, "
            f"{summary['average_seconds_per_job']} s/Job"
        )
        print(f"Gespeichert: {path}")


if __name__ == "__main__":
    main()
