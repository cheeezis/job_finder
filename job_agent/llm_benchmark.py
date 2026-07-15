"""Blind comparison of local LLMs against hand-labelled job cases."""

import json
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter

import yaml
from jsonschema import ValidationError, validate

from job_agent.llm_contract import (
    ANALYSIS_SCHEMA,
    MODEL_RESPONSE_SCHEMA,
    RUBRIC,
    RUBRIC_VERSION,
    SCORE_BANDS,
    recommendation_for_score,
)
from job_agent.llm_profile import load_llm_profile
from job_agent.ollama import OllamaError


PROMPT_VERSION = 2
TEST_INPUTS_PATH = Path("evaluation/llm_test_inputs.json")
EXPECTED_RESULTS_PATH = Path("evaluation/llm_expected_results.yaml")
RESULTS_DIR = Path("evaluation/results")
LABEL_ORDER = [
    "not_recommended",
    "borderline",
    "match",
    "strong_match",
]

SYSTEM_PROMPT = """Du bewertest Stellenanzeigen fuer einen Berufseinsteiger.
Nutze ausschliesslich belegte Fakten aus Profil und Stellenanzeige.
Erfinde oder erhoehe keine Kenntnisse und zaehle Studium, Praktikum und Projekte
nicht als regulaere Berufserfahrung. Bewerte die tatsaechlichen Aufgaben und
Anforderungen, nicht nur den Titel.

Verbindliche Auslegung:
- Nutze location_precheck als geprueften Fakt und berechne keine Entfernung.
- native erfuellt Sprachforderungen einschliesslich C1.
- basic_knowledge ist nur Grundlage, nicht praktische Berufserfahrung.
- Laufende Kurse und persoenliche Projekte sind keine Berufserfahrung.
- Eine Forderung nach mehreren Technologien braucht mehrere getrennte Belege.
- Leite persoenliche oder kommunikative Staerken nur aus passenden Belegen ab.
- Fehlende Belege muessen als missing, unknown, gap oder uncertainty erscheinen.
- Zentrale fehlende Pflichtanforderungen muessen die betroffenen Teilwerte senken.

Vergib fuer jede Dimension einen eigenstaendigen Wert anhand der Rubrik. Python
berechnet daraus anschliessend Gesamtwert und Empfehlung. Antworte
ausschliesslich im vorgegebenen JSON-Schema."""


class BenchmarkDataError(ValueError):
    """Raised when benchmark fixtures are incomplete or inconsistent."""


class AnalysisValidationError(ValueError):
    """Raised when a model response violates schema or scoring rules."""


def load_benchmark_data(
    inputs_path=TEST_INPUTS_PATH,
    expected_path=EXPECTED_RESULTS_PATH,
):
    """Load blind model inputs and separate human labels."""
    inputs = json.loads(Path(inputs_path).read_text(encoding="utf-8"))
    expected = yaml.safe_load(Path(expected_path).read_text(encoding="utf-8"))

    if inputs.get("rubric_version") != RUBRIC_VERSION:
        raise BenchmarkDataError("Input verwendet eine andere Rubrikversion")
    if expected.get("rubric_version") != RUBRIC_VERSION:
        raise BenchmarkDataError("Erwartungen verwenden eine andere Rubrikversion")

    input_ids = [case["job_id"] for case in inputs.get("cases", [])]
    expected_ids = [case["job_id"] for case in expected.get("cases", [])]
    if input_ids != expected_ids:
        raise BenchmarkDataError("Inputs und Erwartungen enthalten andere Jobs")

    return inputs, expected


def build_messages(profile, job):
    """Build a blind prompt without human labels or deterministic scores."""
    prompt_data = {
        "profile": profile.data,
        "rubric": RUBRIC,
        "score_bands": [
            {"minimum": minimum, "recommendation": label}
            for minimum, label in SCORE_BANDS
        ],
        "job": job,
        "output_schema": MODEL_RESPONSE_SCHEMA,
    }
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": json.dumps(prompt_data, ensure_ascii=False),
        },
    ]


def validate_analysis(analysis):
    """Validate JSON shape and semantic consistency of one LLM analysis."""
    try:
        validate(instance=analysis, schema=ANALYSIS_SCHEMA)
    except ValidationError as error:
        raise AnalysisValidationError(error.message) from error

    dimension_total = sum(analysis["dimension_scores"].values())
    if analysis["overall_score"] != dimension_total:
        raise AnalysisValidationError(
            "overall_score entspricht nicht der Summe der Dimensionswerte"
        )

    expected_label = recommendation_for_score(analysis["overall_score"])
    if analysis["recommendation"] != expected_label:
        raise AnalysisValidationError(
            "recommendation entspricht nicht dem festen Scoreband"
        )


def finalize_analysis(model_response):
    """Validate model judgments and derive total score and recommendation."""
    try:
        validate(instance=model_response, schema=MODEL_RESPONSE_SCHEMA)
    except ValidationError as error:
        raise AnalysisValidationError(error.message) from error

    analysis = dict(model_response)
    overall_score = sum(analysis["dimension_scores"].values())
    analysis["overall_score"] = overall_score
    analysis["recommendation"] = recommendation_for_score(overall_score)
    validate_analysis(analysis)
    return analysis


def run_model_benchmark(model, client, limit=None):
    """Evaluate one model and return raw results plus aggregate metrics."""
    inputs, expected = load_benchmark_data()
    profile = load_llm_profile()
    jobs = inputs["cases"][:limit]
    labels = {
        case["job_id"]: case["expected_recommendation"]
        for case in expected["cases"]
    }
    results = []

    for index, job in enumerate(jobs, start=1):
        print(f"[{index}/{len(jobs)}] {model}: {job['title']}")
        started = perf_counter()
        result = {
            "job_id": job["job_id"],
            "expected_recommendation": labels[job["job_id"]],
        }
        try:
            model_response, metadata = client.chat(
                model=model,
                messages=build_messages(profile, job),
                output_schema=MODEL_RESPONSE_SCHEMA,
            )
            analysis = finalize_analysis(model_response)
            result.update(
                {
                    "valid": True,
                    "analysis": analysis,
                    "metadata": metadata,
                }
            )
        except (OllamaError, AnalysisValidationError) as error:
            result.update({"valid": False, "error": str(error)})
        result["elapsed_seconds"] = round(perf_counter() - started, 3)
        results.append(result)

    return {
        "model": model,
        "prompt_version": PROMPT_VERSION,
        "profile_version": profile.version,
        "rubric_version": RUBRIC_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "summary": summarize_results(results),
        "results": results,
    }


def summarize_results(results):
    """Calculate comparable quality, reliability, and runtime metrics."""
    valid = [result for result in results if result["valid"]]
    exact = [
        result
        for result in valid
        if result["analysis"]["recommendation"]
        == result["expected_recommendation"]
    ]
    total = len(results)
    elapsed = sum(result["elapsed_seconds"] for result in results)
    distances = [label_distance(result) for result in valid]
    within_one = sum(distance <= 1 for distance in distances)
    dangerous_false_positives = sum(
        result["expected_recommendation"]
        in {"not_recommended", "borderline"}
        and result["analysis"]["recommendation"] in {"match", "strong_match"}
        for result in valid
    )
    return {
        "jobs": total,
        "valid_responses": len(valid),
        "valid_response_rate": round(len(valid) / total, 3) if total else 0,
        "exact_matches": len(exact),
        "exact_match_rate": round(len(exact) / total, 3) if total else 0,
        "within_one_band_rate": (
            round(within_one / total, 3) if total else 0
        ),
        "mean_band_distance": (
            round(sum(distances) / len(distances), 3) if distances else None
        ),
        "dangerous_false_positives": dangerous_false_positives,
        "average_seconds_per_job": round(elapsed / total, 3) if total else 0,
    }


def label_distance(result):
    """Return the absolute distance between predicted and human label."""
    expected = LABEL_ORDER.index(result["expected_recommendation"])
    predicted = LABEL_ORDER.index(result["analysis"]["recommendation"])
    return abs(expected - predicted)


def write_benchmark_result(result, output_dir=RESULTS_DIR):
    """Persist one model run without modifying the benchmark fixtures."""
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    safe_model = result["model"].replace(":", "-").replace("/", "-")
    path = directory / f"{safe_model}.json"
    path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return path
