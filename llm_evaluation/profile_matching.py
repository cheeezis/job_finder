"""Evaluate profile-matching models against cached job analyses."""

import json
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter

from job_agent.llm.contract import RUBRIC_VERSION
from job_agent.llm.job_analysis import (
    JOB_ANALYSIS_PROMPT_VERSION,
    JOB_ANALYSIS_SCHEMA_VERSION,
    JobAnalysisValidationError,
    validate_job_analysis,
)
from job_agent.llm.errors import LLMError
from job_agent.llm.fit_score import FIT_SCORE_VERSION, score_two_stage_result
from job_agent.llm.profile_loader import load_llm_profile
from job_agent.llm.profile_match import (
    PROFILE_MATCH_PROMPT_VERSION,
    PROFILE_MATCH_SCHEMA_VERSION,
    ProfileMatchValidationError,
    match_job_to_profile,
)
from llm_evaluation.benchmark import (
    BenchmarkDataError,
    JOB_ANALYSIS_SPLITS,
    RESULTS_DIR,
    load_benchmark_data,
    load_job_analysis_split,
    summarize_results,
)


def safe_model_name(model):
    """Return a model name suitable for local result filenames."""
    return model.replace(":", "-").replace("/", "-")


def job_analysis_cache_path(model, split, results_dir=RESULTS_DIR):
    """Return the versioned job-analysis result path used as evaluation cache."""
    filename = f"{safe_model_name(model)}-job-analysis-{split}.json"
    return Path(results_dir) / filename


def load_job_analysis_cache(
    analysis_model,
    split="development",
    limit=None,
    results_dir=RESULTS_DIR,
):
    """Load and revalidate cached analyses for one protected fixture split."""
    if split not in JOB_ANALYSIS_SPLITS:
        raise BenchmarkDataError(f"Unbekannter Job-Analyse-Split: {split}")

    path = job_analysis_cache_path(analysis_model, split, results_dir)
    try:
        cache = json.loads(path.read_text(encoding="utf-8"))
    except OSError as error:
        raise BenchmarkDataError(
            f"Stufe-1-Cache fehlt: {path}. Zuerst --job-analysis-only ausfuehren."
        ) from error
    except json.JSONDecodeError as error:
        raise BenchmarkDataError(f"Stufe-1-Cache ist ungueltig: {path}") from error

    expected_metadata = {
        "model": analysis_model,
        "mode": "job_analysis",
        "split": split,
        "prompt_version": JOB_ANALYSIS_PROMPT_VERSION,
        "schema_version": JOB_ANALYSIS_SCHEMA_VERSION,
    }
    for key, expected in expected_metadata.items():
        if cache.get(key) != expected:
            raise BenchmarkDataError(
                f"Stufe-1-Cache hat unpassendes Feld {key}: {cache.get(key)!r}"
            )

    inputs, _ = load_benchmark_data()
    split_data = load_job_analysis_split()
    jobs_by_id = {job["job_id"]: job for job in inputs["cases"]}
    selected_ids = split_data["splits"][split][:limit]
    cached_by_id = {result["job_id"]: result for result in cache["results"]}
    prepared = []

    for job_id in selected_ids:
        result = cached_by_id.get(job_id)
        if not result or not result.get("valid"):
            raise BenchmarkDataError(
                f"Stufe-1-Cache enthaelt keine gueltige Analyse fuer {job_id}"
            )
        job = jobs_by_id[job_id]
        source_text = "\n".join(
            str(job.get(field) or "") for field in ("title", "description_clean")
        )
        try:
            analysis = validate_job_analysis(result["analysis"], source_text)
        except JobAnalysisValidationError as error:
            raise BenchmarkDataError(
                f"Gespeicherte Analyse fuer {job_id} ist nicht mehr gueltig: {error}"
            ) from error
        prepared.append((job, analysis))

    return prepared


def run_profile_match_evaluation(
    analysis_model,
    match_model,
    client,
    split="development",
    limit=None,
    results_dir=RESULTS_DIR,
):
    """Compare one matching model against fixed cached job analyses."""
    prepared = load_job_analysis_cache(
        analysis_model,
        split=split,
        limit=limit,
        results_dir=results_dir,
    )
    profile = load_llm_profile()
    _, expected = load_benchmark_data()
    labels = {
        case["job_id"]: case["expected_recommendation"]
        for case in expected["cases"]
    }
    results = []

    for index, (job, job_analysis) in enumerate(prepared, start=1):
        print(f"[{index}/{len(prepared)}] {match_model}: {job['title']}")
        started = perf_counter()
        result = {
            "job_id": job["job_id"],
            "title": job["title"],
            "expected_recommendation": labels[job["job_id"]],
        }
        try:
            profile_match, metadata = match_job_to_profile(
                job_analysis,
                profile,
                match_model,
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
                    "metadata": metadata,
                }
            )
        except (LLMError, ProfileMatchValidationError) as error:
            result.update({"valid": False, "error": str(error)})
            raw_match = getattr(error, "raw_match", None)
            if raw_match is not None:
                result["raw_match"] = raw_match
        result["elapsed_seconds"] = round(perf_counter() - started, 3)
        results.append(result)

    return {
        "model": match_model,
        "analysis_model": analysis_model,
        "mode": "profile_match",
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


def write_profile_match_result(result, output_dir=RESULTS_DIR):
    """Persist one isolated profile-matching model run."""
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    analysis_model = safe_model_name(result["analysis_model"])
    match_model = safe_model_name(result["model"])
    split = result["split"]
    path = directory / (
        f"{analysis_model}-analysis__{match_model}-match-{split}.json"
    )
    path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return path
