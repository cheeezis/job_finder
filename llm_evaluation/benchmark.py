"""Blind comparison of local LLMs against hand-labelled job cases."""

import json
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter

import yaml
from jsonschema import ValidationError, validate

from job_agent.llm.contract import (
    ANALYSIS_SCHEMA,
    MODEL_RESPONSE_SCHEMA,
    RATING_POINTS,
    RUBRIC,
    RUBRIC_VERSION,
    SCORE_BANDS,
    recommendation_for_analysis,
    recommendation_for_score,
)
from job_agent.llm.fit_score import FIT_SCORE_VERSION, score_two_stage_result
from job_agent.llm.job_analysis import (
    JOB_ANALYSIS_PROMPT_VERSION,
    JOB_ANALYSIS_SCHEMA_VERSION,
    JobAnalysisValidationError,
    analyze_job,
)
from job_agent.llm.errors import LLMError
from job_agent.llm.profile_loader import load_llm_profile
from job_agent.llm.profile_match import (
    PROFILE_MATCH_PROMPT_VERSION,
    PROFILE_MATCH_SCHEMA_VERSION,
    ProfileMatchValidationError,
    match_job_to_profile,
)


PROMPT_VERSION = 5
EVALUATION_ROOT = Path(__file__).resolve().parent
TEST_INPUTS_PATH = EVALUATION_ROOT / "fixtures" / "test_inputs.json"
EXPECTED_RESULTS_PATH = EVALUATION_ROOT / "fixtures" / "expected_results.yaml"
JOB_ANALYSIS_SPLIT_PATH = (
    EVALUATION_ROOT / "fixtures" / "job_analysis_split.yaml"
)
RESULTS_DIR = EVALUATION_ROOT / "results"
JOB_ANALYSIS_SPLITS = ("development", "holdout", "reserve")
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
- Kurse, Studium, Praktikum und Projekte sind trotzdem Technikbelege auf dem
  im Profil genannten Niveau und duerfen fuer technology_fit verwendet werden.
- Eine Forderung nach mehreren Technologien braucht mehrere getrennte Belege.
- Leite persoenliche oder kommunikative Staerken nur aus passenden Belegen ab.
- Fehlende Belege muessen als missing, unknown, gap oder uncertainty erscheinen.
- Bewerte jede Dimension isoliert; ein guter Standort gleicht keine fachliche
  oder erfahrungsbezogene Luecke aus.
- good ist nur zulaessig, wenn die zentralen Anforderungen weitgehend belegt sind.
- Fehlt der zwingende Kern-Stack weitgehend, ist technology_fit weak oder conflict.
- Eine ausdrueckliche Junior- oder Einstiegsrolle ohne Mindestjahre ergibt bei
  passender Ausbildung experience_fit excellent.
- Nur bevorzugte oder wuenschenswerte erste Erfahrung ergibt good oder partial.
- Zwingend geforderte ein bis drei Jahre Berufserfahrung ergeben weak.
- Seniorniveau oder mehr als drei geforderte Jahre ergeben experience_fit conflict.
- task_fit bewertet die inhaltliche Richtung der Aufgaben und darf fehlende
  Technologien nicht ein zweites Mal bestrafen.

Vergib fuer jede Dimension genau eine feste Qualitaetsstufe anhand der Rubrik.
Python berechnet daraus Teilwerte, Gesamtwert und Empfehlung. Antworte
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


def load_job_analysis_split(
    split_path=JOB_ANALYSIS_SPLIT_PATH,
    inputs_path=TEST_INPUTS_PATH,
):
    """Load and validate the external development/holdout/reserve split."""
    split_data = yaml.safe_load(Path(split_path).read_text(encoding="utf-8"))
    inputs = json.loads(Path(inputs_path).read_text(encoding="utf-8"))

    if split_data.get("version") != 1:
        raise BenchmarkDataError("Unbekannte Split-Dateiversion")
    if (
        split_data.get("job_analysis_schema_version")
        != JOB_ANALYSIS_SCHEMA_VERSION
    ):
        raise BenchmarkDataError("Split verwendet eine andere Schemaversion")
    if split_data.get("frozen_prompt_version") != JOB_ANALYSIS_PROMPT_VERSION:
        raise BenchmarkDataError("Split verwendet eine andere Promptversion")

    splits = split_data.get("splits", {})
    if tuple(splits) != JOB_ANALYSIS_SPLITS:
        raise BenchmarkDataError(
            "Split muss development, holdout und reserve enthalten"
        )

    input_ids = [case["job_id"] for case in inputs.get("cases", [])]
    split_ids = [
        job_id
        for name in JOB_ANALYSIS_SPLITS
        for job_id in splits[name]
    ]
    if len(split_ids) != len(set(split_ids)):
        raise BenchmarkDataError("Ein Job kommt in mehreren Splits vor")
    if set(split_ids) != set(input_ids):
        raise BenchmarkDataError("Split und Testeingaben enthalten andere Jobs")

    return split_data


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
    expected_scores = {
        name: RATING_POINTS[rating]
        for name, rating in analysis["dimension_ratings"].items()
    }
    if analysis["dimension_scores"] != expected_scores:
        raise AnalysisValidationError(
            "Dimensionswerte entsprechen nicht den festen Qualitaetsstufen"
        )
    if analysis["overall_score"] != dimension_total:
        raise AnalysisValidationError(
            "overall_score entspricht nicht der Summe der Dimensionswerte"
        )

    expected_label = recommendation_for_analysis(
        analysis["overall_score"],
        analysis["hard_conflicts"],
    )
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
    analysis["dimension_scores"] = {
        name: RATING_POINTS[rating]
        for name, rating in analysis["dimension_ratings"].items()
    }
    overall_score = sum(analysis["dimension_scores"].values())
    analysis["overall_score"] = overall_score
    analysis["recommendation"] = recommendation_for_analysis(
        overall_score,
        analysis["hard_conflicts"],
    )
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
        except (LLMError, AnalysisValidationError) as error:
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


def run_job_analysis_evaluation(
    model,
    client,
    limit=None,
    split="development",
):
    """Extract profile-free job facts for manual inspection."""
    inputs, _ = load_benchmark_data()
    split_data = load_job_analysis_split()
    if split not in JOB_ANALYSIS_SPLITS:
        raise BenchmarkDataError(f"Unbekannter Job-Analyse-Split: {split}")

    jobs_by_id = {job["job_id"]: job for job in inputs["cases"]}
    jobs = [jobs_by_id[job_id] for job_id in split_data["splits"][split]]
    jobs = jobs[:limit]
    results = []

    for index, job in enumerate(jobs, start=1):
        print(f"[{index}/{len(jobs)}] {model}: {job['title']}")
        started = perf_counter()
        result = {"job_id": job["job_id"], "title": job["title"]}
        try:
            analysis, metadata = analyze_job(job, model, client)
            result.update(
                {
                    "valid": True,
                    "analysis": analysis,
                    "metadata": metadata,
                }
            )
        except (LLMError, JobAnalysisValidationError) as error:
            result.update({"valid": False, "error": str(error)})
            raw_analysis = getattr(error, "raw_analysis", None)
            if raw_analysis is not None:
                result["raw_analysis"] = raw_analysis
        result["elapsed_seconds"] = round(perf_counter() - started, 3)
        results.append(result)

    valid = sum(result["valid"] for result in results)
    elapsed = sum(result["elapsed_seconds"] for result in results)
    return {
        "model": model,
        "mode": "job_analysis",
        "split": split,
        "prompt_version": JOB_ANALYSIS_PROMPT_VERSION,
        "schema_version": JOB_ANALYSIS_SCHEMA_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "summary": {
            "jobs": len(results),
            "valid_responses": valid,
            "average_seconds_per_job": (
                round(elapsed / len(results), 3) if results else 0
            ),
        },
        "results": results,
    }


def run_two_stage_evaluation(
    model,
    client,
    limit=None,
    split="development",
):
    """Run objective extraction followed by evidence-based profile matching."""
    inputs, expected = load_benchmark_data()
    split_data = load_job_analysis_split()
    if split not in JOB_ANALYSIS_SPLITS:
        raise BenchmarkDataError(f"Unbekannter Job-Analyse-Split: {split}")

    profile = load_llm_profile()
    jobs_by_id = {job["job_id"]: job for job in inputs["cases"]}
    jobs = [jobs_by_id[job_id] for job_id in split_data["splits"][split]]
    jobs = jobs[:limit]
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
            "title": job["title"],
            "expected_recommendation": labels[job["job_id"]],
        }
        current_stage = "job_analysis"
        try:
            job_analysis, analysis_metadata = analyze_job(job, model, client)
            result["job_analysis"] = job_analysis
            current_stage = "profile_match"
            profile_match, match_metadata = match_job_to_profile(
                job_analysis,
                profile,
                model,
                client,
            )
            analysis = score_two_stage_result(
                job,
                job_analysis,
                profile_match["match"],
            )
            result.update(
                {
                    "valid": True,
                    "profile_match": profile_match["match"],
                    "analysis": analysis,
                    "metadata": {
                        "job_analysis": analysis_metadata,
                        "profile_match": match_metadata,
                    },
                }
            )
        except (
            LLMError,
            JobAnalysisValidationError,
            ProfileMatchValidationError,
        ) as error:
            result.update(
                {
                    "valid": False,
                    "failed_stage": current_stage,
                    "error": str(error),
                }
            )
            raw_output = getattr(error, "raw_analysis", None)
            if raw_output is None:
                raw_output = getattr(error, "raw_match", None)
            if raw_output is not None:
                result["raw_output"] = raw_output
        result["elapsed_seconds"] = round(perf_counter() - started, 3)
        results.append(result)

    return {
        "model": model,
        "mode": "two_stage",
        "split": split,
        "profile_version": profile.version,
        "job_analysis_prompt_version": JOB_ANALYSIS_PROMPT_VERSION,
        "job_analysis_schema_version": JOB_ANALYSIS_SCHEMA_VERSION,
        "profile_match_prompt_version": PROFILE_MATCH_PROMPT_VERSION,
        "profile_match_schema_version": PROFILE_MATCH_SCHEMA_VERSION,
        "fit_score_version": FIT_SCORE_VERSION,
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
    missed_positive_jobs = sum(
        result["expected_recommendation"] in {"match", "strong_match"}
        and result["analysis"]["recommendation"]
        in {"not_recommended", "borderline"}
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
        "missed_positive_jobs": missed_positive_jobs,
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


def write_job_analysis_result(result, output_dir=RESULTS_DIR):
    """Persist profile-free extraction results separately from fit benchmarks."""
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    safe_model = result["model"].replace(":", "-").replace("/", "-")
    split = result["split"]
    path = directory / f"{safe_model}-job-analysis-{split}.json"
    path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return path


def write_two_stage_result(result, output_dir=RESULTS_DIR):
    """Persist combined extraction and profile-matching evaluation results."""
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    safe_model = result["model"].replace(":", "-").replace("/", "-")
    split = result["split"]
    path = directory / f"{safe_model}-two-stage-{split}.json"
    path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return path
