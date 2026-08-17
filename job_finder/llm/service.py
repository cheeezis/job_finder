"""Productive two-stage LLM analysis with versioned local caching."""

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from job_finder.llm.config import DEFAULT_LLM_SETTINGS
from job_finder.llm.errors import LLMError
from job_finder.llm.fit_score import FIT_SCORE_VERSION, score_two_stage_result
from job_finder.llm.job_analysis import (
    JOB_ANALYSIS_PROMPT_VERSION,
    JOB_ANALYSIS_SCHEMA_VERSION,
    JobAnalysisValidationError,
    analyze_job,
    job_analysis_input,
)
from job_finder.llm.openai import OpenAIClient
from job_finder.llm.profile_loader import load_llm_profile
from job_finder.llm.profile_match import (
    PROFILE_MATCH_PROMPT_VERSION,
    PROFILE_MATCH_SCHEMA_VERSION,
    ProfileMatchValidationError,
    match_job_to_profile,
    profile_match_job_context,
)


CACHE_VERSION = 1
REVIEWED_WORKFLOW_STATUSES = {
    "interesting",
    "ignored",
    "applied",
    "response",
    "interview",
    "rejected",
    "no_response",
    "offer",
    "closed",
}


def analyze_results(
    results,
    settings=DEFAULT_LLM_SETTINGS,
    client=None,
    limit=None,
):
    """Analyze eligible result rows and attach cached or fresh LLM output."""
    profile = load_llm_profile()
    cache_state = load_cache(settings.cache_path)
    analyses = cache_state["analyses"]
    pending = cache_state["pending"]
    current_analysis_version = analysis_configuration_key(settings)
    cached_analysis_version = cache_state.get("analysis_version")
    analysis_changed = (
        cached_analysis_version != current_analysis_version
    )
    cached_profile_version = cache_state.get("profile_version")
    profile_changed = (
        cached_profile_version is not None
        and cached_profile_version != profile.version
    )
    cache_reset = profile_changed or analysis_changed
    cache_invalidated = cache_reset and bool(
        analyses
        or pending
        or cached_profile_version is not None
        or cached_analysis_version is not None
    )
    previous_profile_analyses = {}
    if profile_changed and not analysis_changed:
        previous_profile_analyses = dict(analyses)
    if cache_reset:
        analyses.clear()
        pending.clear()
    cache_state["profile_version"] = profile.version
    cache_state["analysis_version"] = current_analysis_version
    jobs_with_keys = [
        (job, analysis_cache_key(job, profile.version, settings))
        for job in results["included"]
    ]
    eligible = []
    cached_count = 0
    blocked_count = 0
    cache_changed = cache_reset
    for job, key in jobs_with_keys:
        cached = analyses.get(key)
        if (
            cached is None
            and previous_profile_analyses
            and is_reviewed_unchanged(job)
        ):
            previous_key = analysis_cache_key(
                job,
                cached_profile_version,
                settings,
            )
            cached = previous_profile_analyses.get(previous_key)
            if cached is not None:
                cached["cache_key"] = key
                cached.setdefault("metadata", {})[
                    "cache_compatibility"
                ] = "manual_review_preserved_profile_change"
                analyses[key] = cached
                cache_changed = True
        if cached is not None:
            cache_changed |= attach_result(job, cached, "cached")
            cache_changed |= pending.pop(key, None) is not None
            cached_count += 1
        else:
            pending_entry = pending.get(key)
            if (
                pending_failure_kind(pending_entry) == "validation"
                and not cache_invalidated
            ):
                job["llm_status"] = "failed"
                job["llm_error"] = pending_entry.get("error")
                if pending_entry.get("error_kind") != "validation":
                    pending_entry["error_kind"] = "validation"
                    cache_changed = True
                blocked_count += 1
                continue
            reason = analysis_reason(
                job,
                pending_entry,
                cache_invalidated=cache_invalidated,
            )
            eligible.append((job, key, reason))

    for job, key, _reason in eligible:
        if key not in pending:
            pending[key] = pending_record(job, "pending")
            cache_changed = True
    if limit is not None:
        eligible = eligible[:limit]
    reasons = {"cache_hit": cached_count}
    if blocked_count:
        reasons["validation_blocked"] = blocked_count
    stats = {
        "eligible": len(eligible),
        "analyzed": 0,
        "cached": cached_count,
        "failed": 0,
        "blocked": blocked_count,
        "reasons": reasons,
        "model_calls": model_call_stats(),
        "errors": [],
    }
    for _job, _key, reason in eligible:
        stats["reasons"][reason] = stats["reasons"].get(reason, 0) + 1

    if eligible and client is None:
        try:
            client = OpenAIClient(
                timeout=settings.timeout_seconds,
                reasoning_effort=settings.reasoning_effort,
                max_output_tokens=settings.max_output_tokens,
            )
        except LLMError as error:
            for job, key, _reason in eligible:
                job["llm_status"] = "failed"
                job["llm_error"] = str(error)
                pending[key] = pending_record(job, "failed", error)
            stats["failed"] = len(eligible)
            stats["errors"].append(
                llm_error_record(error, affected_jobs=len(eligible))
            )
            save_cache(cache_state, settings.cache_path)
            sort_included_results(results["included"])
            return stats

    request_log = []
    for job, key, _reason in eligible:
        try:
            record = analyze_job_record(
                job,
                profile,
                settings,
                client,
                key,
                request_log=request_log,
            )
        except (
            LLMError,
            JobAnalysisValidationError,
            ProfileMatchValidationError,
        ) as error:
            job["llm_status"] = "failed"
            job["llm_error"] = str(error)
            pending[key] = pending_record(job, "failed", error)
            cache_changed = True
            stats["failed"] += 1
            stats["errors"].append(llm_error_record(error))
            continue

        analyses[key] = record
        pending.pop(key, None)
        attach_result(job, record, "analyzed")
        cache_changed = True
        stats["analyzed"] += 1

    stats["model_calls"] = model_call_stats(request_log)
    if cache_changed:
        save_cache(cache_state, settings.cache_path)
    sort_included_results(results["included"])
    return stats


def model_call_stats(request_log=()):
    """Summarize model calls made during the current run only."""
    stats = {
        "calls": 0,
        "failed": 0,
        "validation_repairs": 0,
        "usage_missing": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "cached_input_tokens": 0,
        "reasoning_tokens": 0,
    }
    token_fields = {
        "prompt_eval_count": "input_tokens",
        "eval_count": "output_tokens",
        "cached_input_tokens": "cached_input_tokens",
        "reasoning_tokens": "reasoning_tokens",
    }
    for request in request_log:
        stats["calls"] += 1
        stats["failed"] += not request.get("success")
        if request.get("validation_repair"):
            stats["validation_repairs"] += 1

        metadata = request.get("metadata") or {}
        if (
            token_count(metadata.get("prompt_eval_count")) is None
            or token_count(metadata.get("eval_count")) is None
        ):
            stats["usage_missing"] += 1
        for source_field, target_field in token_fields.items():
            stats[target_field] += token_count(metadata.get(source_field)) or 0
    return stats


def token_count(value):
    """Return a valid usage counter or None."""
    return value if type(value) is int and value >= 0 else None


def llm_error_record(error, affected_jobs=1):
    """Translate internal LLM failures into concise user-facing diagnostics."""
    lowered = str(error).casefold()
    if isinstance(error, JobAnalysisValidationError):
        category = "Stellenanalyse"
        if "wortgetreu" in lowered or "beleg kommt nicht" in lowered:
            message = "Ein KI-Beleg steht nicht wortgetreu in der Anzeige."
        else:
            message = "Die KI-Antwort widerspricht den extrahierten Stellendaten."
    elif isinstance(error, ProfileMatchValidationError):
        category = "Profilabgleich"
        if "unbekannte profilbelege" in lowered:
            message = "Die KI verweist auf einen nicht vorhandenen Profilbeleg."
        else:
            message = "Die KI-Antwort ordnet Anforderungen oder Profilbelege falsch zu."
    else:
        category = "OpenAI"
        if "openai_api_key" in lowered or "client konnte nicht initialisiert" in lowered:
            message = "Der OpenAI-Client kann ohne gültigen API-Key nicht starten."
        elif any(
            signal in lowered
            for signal in (
                "credit_balance_exhausted",
                "insufficient_quota",
                "no credits",
                "billing quota",
            )
        ):
            message = "Das OpenAI-Guthaben ist aufgebraucht."
        elif "429" in lowered or "rate_limit" in lowered:
            message = "OpenAI begrenzt die Anfragen vorübergehend."
        elif (
            "timeout" in lowered
            or "timed out" in lowered
            or "zeitüberschreitung" in lowered
        ):
            message = "Die OpenAI-Anfrage hat zu lange gedauert."
        else:
            message = "OpenAI lieferte keine nutzbare strukturierte Antwort."

    if affected_jobs > 1:
        message = f"{affected_jobs} Stellen nicht analysiert: {message}"
    return {
        "category": category,
        "message": message,
    }


def analysis_reason(
    job,
    pending_entry,
    *,
    cache_invalidated,
):
    """Classify why a cache miss is eligible for a paid LLM analysis."""
    pending_status = (pending_entry or {}).get("status")
    if pending_status == "failed" and not cache_invalidated:
        return "retry_after_failed"
    if job.get("is_new"):
        return "new_job"
    if cache_invalidated:
        return "profile_or_config_changed"
    if job.get("content_changed"):
        return "content_or_input_changed"
    if pending_status == "pending":
        return "deferred"
    return "uncached_known_job"


def analyze_job_record(
    job,
    profile,
    settings,
    client,
    cache_key,
    request_log=None,
):
    """Run both LLM stages and build one auditable cache record."""
    job_analysis, analysis_metadata = analyze_job(
        job,
        settings.model,
        client,
        request_log=request_log,
    )
    profile_result, match_metadata = match_job_to_profile(
        job_analysis,
        profile,
        settings.model,
        client,
        job_context=job,
        request_log=request_log,
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
        "job_analysis_input": job_analysis_input(job),
        "profile_match_job_context": profile_match_job_context(job),
        "model": settings.model,
        "reasoning_effort": settings.reasoning_effort,
        "max_output_tokens": settings.max_output_tokens,
        "job_analysis_prompt_version": JOB_ANALYSIS_PROMPT_VERSION,
        "job_analysis_schema_version": JOB_ANALYSIS_SCHEMA_VERSION,
        "profile_match_prompt_version": PROFILE_MATCH_PROMPT_VERSION,
        "profile_match_schema_version": PROFILE_MATCH_SCHEMA_VERSION,
        "profile_version": profile_version,
    }
    return hash_payload(payload)


def is_reviewed_unchanged(job):
    """Preserve results only after a final manual review decision."""
    return (
        job.get("workflow_status") in REVIEWED_WORKFLOW_STATUSES
        and not job.get("is_new")
        and not job.get("content_changed")
    )


def analysis_configuration_key(settings):
    """Identify LLM settings that require reanalyzing cached job facts."""
    payload = {
        "model": settings.model,
        "reasoning_effort": settings.reasoning_effort,
        "max_output_tokens": settings.max_output_tokens,
        "job_analysis_prompt_version": JOB_ANALYSIS_PROMPT_VERSION,
        "job_analysis_schema_version": JOB_ANALYSIS_SCHEMA_VERSION,
        "profile_match_prompt_version": PROFILE_MATCH_PROMPT_VERSION,
        "profile_match_schema_version": PROFILE_MATCH_SCHEMA_VERSION,
    }
    return hash_payload(payload)


def hash_payload(payload):
    """Return the stable SHA-256 key used by the local LLM cache."""
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
    previous_score = record["score"]
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
    job["llm_score_changed"] = score_changed and record["score"] != previous_score
    job["llm_result"] = compact_result(record)
    return score_changed


def compact_result(record):
    """Return only the LLM information needed for review and decisions."""
    job_analysis = record["analysis"]["job"]
    fit = record["analysis"]["fit"]
    return {
        "recommendation": fit["recommendation"],
        "confidence": fit["confidence"],
        "summary": job_analysis["role_summary"],
        "seniority": job_analysis.get("seniority", "unspecified"),
        "tasks": job_analysis["tasks"][:3],
        "requirements": compact_requirements(job_analysis),
        "matching_evidence": fit["matching_evidence"][:3],
        "gaps": fit["gaps"],
        "risks": [risk for risk in fit["risks"] if risk not in fit["gaps"]],
    }


def compact_requirements(job_analysis, limit=5):
    """Collect the most relevant extracted requirements without evidence data."""
    requirements = []
    experience = job_analysis["experience_requirement"]
    technology_names = []
    for group in job_analysis["technology_requirements"]:
        names = ", ".join(item["name"] for item in group["technologies"])
        technology_names.extend(item["name"] for item in group["technologies"])
        requirements.append(names)
    experience_text = experience["evidence_quote"]
    if (
        experience["expectation"] != "none_stated"
        and not any(
            name.casefold() in experience_text.casefold()
            for name in technology_names
        )
    ):
        requirements.insert(0, experience_text)
    requirements.extend(
        item["requirement"] for item in job_analysis["other_requirements"]
    )
    return list(dict.fromkeys(text for text in requirements if text))[:limit]


def pending_record(job, status, error=None):
    """Return compact retry state for an unfinished analysis."""
    return {
        "job_id": job["id"],
        "title": job["title"],
        "status": status,
        "error": str(error) if error else None,
        "error_kind": failure_kind(error),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


def failure_kind(error):
    """Classify failures by whether an unchanged input should be retried."""
    if isinstance(
        error,
        (JobAnalysisValidationError, ProfileMatchValidationError),
    ):
        return "validation"
    return "provider" if error is not None else None


def pending_failure_kind(entry):
    """Read current and unambiguous legacy failure classifications."""
    if not entry or entry.get("status") != "failed":
        return None
    if entry.get("error_kind") in {"validation", "provider"}:
        return entry["error_kind"]
    error = str(entry.get("error") or "").casefold()
    if any(
        signal in error
        for signal in (
            "beleg kommt nicht wortgetreu",
            "unbekannte profilbelege",
        )
    ):
        return "validation"
    return "provider"


def sort_included_results(jobs):
    """Prefer LLM scores where available and keep deterministic tie breakers."""
    jobs.sort(
        key=lambda job: (
            job.get("llm_score") is None,
            -(
                job["llm_score"]
                if job.get("llm_score") is not None
                else job.get("match_percent", 0)
            ),
            job.get("experience_rank", 0),
            job.get("title", "").casefold(),
        )
    )


def load_cache(path):
    """Load successful analyses and pending retries."""
    cache_path = Path(path)
    if not cache_path.exists():
        return {
            "profile_version": None,
            "analysis_version": None,
            "analyses": {},
            "pending": {},
        }
    document = json.loads(cache_path.read_text(encoding="utf-8"))
    if document.get("version") != CACHE_VERSION:
        raise ValueError("LLM-Cache verwendet eine unbekannte Version")
    analyses = document.get("analyses", {})
    profile_version = document.get("profile_version")
    if profile_version is None and analyses:
        profile_version = next(iter(analyses.values())).get("metadata", {}).get(
            "profile_version"
        )
    return {
        "profile_version": profile_version,
        "analysis_version": document.get("analysis_version"),
        "analyses": analyses,
        "pending": document.get("pending", {}),
    }


def save_cache(cache_state, path):
    """Persist analyses and retry state via a temporary replacement file."""
    cache_path = Path(path)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = cache_path.with_suffix(cache_path.suffix + ".tmp")
    temporary_path.write_text(
        json.dumps(
            {"version": CACHE_VERSION, **cache_state},
            ensure_ascii=False,
            separators=(",", ":"),
        )
        + "\n",
        encoding="utf-8",
    )
    temporary_path.replace(cache_path)
