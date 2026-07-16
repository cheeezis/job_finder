"""Productive two-stage LLM analysis with versioned local caching."""

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from job_agent.llm.config import DEFAULT_LLM_SETTINGS
from job_agent.llm.errors import LLMError
from job_agent.llm.fit_score import FIT_SCORE_VERSION, score_two_stage_result
from job_agent.llm.job_analysis import (
    JOB_ANALYSIS_PROMPT_VERSION,
    JOB_ANALYSIS_SCHEMA_VERSION,
    JobAnalysisValidationError,
    analyze_job,
)
from job_agent.llm.openai import OpenAIClient
from job_agent.llm.profile_loader import load_llm_profile
from job_agent.llm.profile_match import (
    PROFILE_MATCH_PROMPT_VERSION,
    PROFILE_MATCH_SCHEMA_VERSION,
    ProfileMatchValidationError,
    match_job_to_profile,
)


CACHE_VERSION = 1


def analyze_results(
    results,
    settings=DEFAULT_LLM_SETTINGS,
    client=None,
    limit=None,
):
    """Analyze eligible result rows and attach cached or fresh LLM output."""
    profile = load_llm_profile()
    cache = load_cache(settings.cache_path)
    jobs_with_keys = [
        (job, analysis_cache_key(job, profile.version, settings))
        for job in results["included"]
    ]
    eligible = []
    cached_count = 0
    cache_changed = False
    for job, key in jobs_with_keys:
        cached = cache.get(key)
        if cached is not None:
            cache_changed |= attach_result(job, cached, "cached")
            cached_count += 1
        elif job.get("is_new"):
            eligible.append((job, key))

    if limit is not None:
        eligible = eligible[:limit]
    stats = {
        "eligible": len(eligible),
        "analyzed": 0,
        "cached": cached_count,
        "failed": 0,
    }

    if eligible and client is None:
        try:
            client = OpenAIClient(
                timeout=settings.timeout_seconds,
                reasoning_effort=settings.reasoning_effort,
                max_output_tokens=settings.max_output_tokens,
            )
        except LLMError as error:
            for job, _key in eligible:
                job["llm_status"] = "failed"
                job["llm_error"] = str(error)
            stats["failed"] = len(eligible)
            sort_included_results(results["included"])
            return stats

    for current, (job, key) in enumerate(eligible, start=1):
        print(f"[{current}/{len(eligible)}] KI-Analyse: {job['title']}")
        try:
            record = analyze_job_record(job, profile, settings, client, key)
        except (
            LLMError,
            JobAnalysisValidationError,
            ProfileMatchValidationError,
        ) as error:
            job["llm_status"] = "failed"
            job["llm_error"] = str(error)
            stats["failed"] += 1
            continue

        cache[key] = record
        attach_result(job, record, "analyzed")
        stats["analyzed"] += 1

    if stats["analyzed"] or cache_changed:
        save_cache(cache, settings.cache_path)
    sort_included_results(results["included"])
    return stats


def analyze_job_record(job, profile, settings, client, cache_key):
    """Run both LLM stages and build one auditable cache record."""
    job_analysis, analysis_metadata = analyze_job(job, settings.model, client)
    profile_result, match_metadata = match_job_to_profile(
        job_analysis,
        profile,
        settings.model,
        client,
    )
    fit = score_two_stage_result(job, job_analysis, profile_result["match"])
    analyzed_at = datetime.now(timezone.utc).isoformat()
    return {
        "cache_key": cache_key,
        "score": fit["overall_score"],
        "analysis": {
            "job": job_analysis,
            "profile_match": profile_result["match"],
            "fit": fit,
        },
        "metadata": {
            "provider": getattr(client, "provider", "unknown"),
            "model": settings.model,
            "model_parameters": {
                "reasoning_effort": settings.reasoning_effort,
                "max_output_tokens": settings.max_output_tokens,
            },
            "job_analysis_prompt_version": JOB_ANALYSIS_PROMPT_VERSION,
            "job_analysis_schema_version": JOB_ANALYSIS_SCHEMA_VERSION,
            "profile_match_prompt_version": PROFILE_MATCH_PROMPT_VERSION,
            "profile_match_schema_version": PROFILE_MATCH_SCHEMA_VERSION,
            "fit_score_version": FIT_SCORE_VERSION,
            "profile_version": profile.version,
            "analyzed_at": analyzed_at,
            "requests": {
                "job_analysis": analysis_metadata,
                "profile_match": match_metadata,
            },
        },
    }


def analysis_cache_key(job, profile_version, settings):
    """Hash every input that can change the persisted analysis result."""
    payload = {
        "job": {
            field: job.get(field)
            for field in (
                "title",
                "description_clean",
                "locations",
                "work_mode",
                "remote_percentage",
                "location_precheck",
            )
        },
        "model": settings.model,
        "reasoning_effort": settings.reasoning_effort,
        "max_output_tokens": settings.max_output_tokens,
        "job_analysis_prompt_version": JOB_ANALYSIS_PROMPT_VERSION,
        "job_analysis_schema_version": JOB_ANALYSIS_SCHEMA_VERSION,
        "profile_match_prompt_version": PROFILE_MATCH_PROMPT_VERSION,
        "profile_match_schema_version": PROFILE_MATCH_SCHEMA_VERSION,
        "profile_version": profile_version,
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def attach_result(job, record, status):
    """Attach one persisted LLM result to a serialized job row."""
    score_changed = record["metadata"].get("fit_score_version") != FIT_SCORE_VERSION
    if score_changed:
        analysis = record["analysis"]
        fit = score_two_stage_result(
            job,
            analysis["job"],
            analysis["profile_match"],
        )
        analysis["fit"] = fit
        record["score"] = fit["overall_score"]
        record["metadata"]["fit_score_version"] = FIT_SCORE_VERSION

    job["llm_status"] = status
    job["llm_score"] = record["score"]
    job["llm_result"] = compact_result(record)
    return score_changed


def compact_result(record):
    """Return only the LLM information needed for review and decisions."""
    job_analysis = record["analysis"]["job"]
    fit = record["analysis"]["fit"]
    return {
        "recommendation": fit["recommendation"],
        "confidence": fit["confidence"],
        "summary": fit["summary"],
        "tasks": job_analysis["tasks"][:3],
        "requirements": compact_requirements(job_analysis),
        "matching_evidence": fit["matching_evidence"],
        "gaps": fit["gaps"],
        "risks": fit["risks"],
    }


def compact_requirements(job_analysis, limit=5):
    """Collect the most relevant extracted requirements without evidence data."""
    requirements = []
    experience = job_analysis["experience_requirement"]
    if experience["expectation"] != "none_stated":
        requirements.append(experience["evidence_quote"])
    for group in job_analysis["technology_requirements"]:
        names = ", ".join(item["name"] for item in group["technologies"])
        requirements.append(names)
    requirements.extend(
        item["requirement"] for item in job_analysis["other_requirements"]
    )
    return list(dict.fromkeys(text for text in requirements if text))[:limit]


def sort_included_results(jobs):
    """Prefer LLM scores where available and keep deterministic tie breakers."""
    jobs.sort(
        key=lambda job: (
            job.get("llm_score") is None,
            -(job.get("llm_score") or job.get("match_percent", 0)),
            job.get("experience_rank", 0),
            job.get("title", "").casefold(),
        )
    )


def load_cache(path):
    """Load the current successful-analysis cache."""
    cache_path = Path(path)
    if not cache_path.exists():
        return {}
    document = json.loads(cache_path.read_text(encoding="utf-8"))
    if document.get("version") != CACHE_VERSION:
        raise ValueError("LLM-Cache verwendet eine unbekannte Version")
    return document.get("analyses", {})


def save_cache(cache, path):
    """Persist successful analyses via a temporary replacement file."""
    cache_path = Path(path)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = cache_path.with_suffix(cache_path.suffix + ".tmp")
    temporary_path.write_text(
        json.dumps(
            {"version": CACHE_VERSION, "analyses": cache},
            ensure_ascii=False,
            separators=(",", ":"),
        )
        + "\n",
        encoding="utf-8",
    )
    temporary_path.replace(cache_path)
