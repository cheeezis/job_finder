"""Run the blind local-LLM comparison against the fixed job test set."""

import argparse

from job_agent.console import configure_utf8_output
from job_agent.llm_benchmark import run_model_benchmark, write_benchmark_result
from job_agent.ollama import OllamaClient, OllamaError


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("models", nargs="+", help="Ollama model names")
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
        result = run_model_benchmark(model, client, limit=args.limit)
        path = write_benchmark_result(result)
        summary = result["summary"]
        print(
            f"{model}: {summary['exact_matches']}/{summary['jobs']} exakt, "
            f"{summary['within_one_band_rate']:.0%} max. eine Stufe entfernt, "
            f"{summary['dangerous_false_positives']} Fehlalarm(e), "
            f"{summary['valid_responses']}/{summary['jobs']} gueltig, "
            f"{summary['average_seconds_per_job']} s/Job"
        )
        print(f"Gespeichert: {path}")


if __name__ == "__main__":
    main()
