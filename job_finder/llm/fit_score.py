"""Deterministic scoring for validated two-stage LLM results."""

from job_finder.llm.contract import (
    recommendation_for_analysis,
)


DIMENSION_POINTS = {
    "entry_fit": {
        "excellent": 50,
        "good": 35,
        "partial": 15,
        "weak": 5,
        "conflict": 0,
    },
    "working_conditions_fit": {
        "excellent": 30,
        "good": 24,
        "partial": 12,
        "weak": 5,
        "conflict": 0,
    },
    "direction_fit": {
        "excellent": 15,
        "good": 12,
        "partial": 8,
        "weak": 3,
        "conflict": 0,
    },
    "technology_head_start": {
        "excellent": 5,
        "good": 4,
        "partial": 2,
        "weak": 1,
        "conflict": 0,
    },
}
HARD_CONFLICT_CATEGORIES = {"education", "employment", "travel"}
FIT_SCORE_VERSION = 8


def score_two_stage_result(job, job_analysis, profile_match):
    """Turn extracted facts and evidence matches into a fixed 0-100 result."""
    matches = {
        item["requirement_id"]: item for item in profile_match["matches"]
    }
    hard_conflicts = find_hard_conflicts(job, job_analysis, matches)
    assessment = profile_match["assessment"]
    ratings = adjusted_ratings(
        job,
        job_analysis,
        matches,
        assessment["dimension_ratings"],
    )
    scores = {
        name: DIMENSION_POINTS[name][rating]
        for name, rating in ratings.items()
    }
    overall_score = capped_score(
        sum(scores.values()),
        ratings,
        assessment["information_quality"],
    )
    if hard_conflicts:
        overall_score = min(overall_score, 39)
    recommendation = recommendation_for_analysis(
        overall_score,
        hard_conflicts,
    )

    uncertainties = unique_texts(
        job_analysis["uncertainties"] + profile_match["uncertainties"],
        limit=3,
    )
    matching_evidence = unique_texts(
        [
            item["explanation"]
            for item in profile_match["matches"]
            if item["status"] in {"met", "partially_met"}
        ],
        limit=4,
    )
    gaps = unique_texts(
        [
            gap
            for item in profile_match["matches"]
            if item["status"] in {"partially_met", "not_met", "unknown"}
            for gap in item["missing_or_uncertain"]
        ],
        limit=4,
    )
    unknown_count = sum(
        item["status"] == "unknown" for item in profile_match["matches"]
    )
    confidence = confidence_for_result(unknown_count, uncertainties)

    return {
        "overall_score": overall_score,
        "recommendation": recommendation,
        "confidence": confidence,
        "summary": truncate(
            f"{job_analysis['role_summary']} Persoenlicher Fit: "
            f"{overall_score}/100 ({recommendation}).",
            600,
        ),
        "dimension_scores": scores,
        "dimension_ratings": ratings,
        "key_tasks": [truncate(task, 300) for task in job_analysis["tasks"][:3]],
        "matching_evidence": matching_evidence,
        "gaps": gaps,
        "risks": unique_texts(hard_conflicts + gaps, limit=3),
        "uncertainties": uncertainties,
        "hard_conflicts": unique_texts(hard_conflicts, limit=3),
    }


def capped_score(score, ratings, information_quality):
    """Enforce the priority hierarchy without interpreting job language in Python."""
    entry_cap = {
        "excellent": 100,
        "good": 79,
        "partial": 59,
        "weak": 39,
        "conflict": 39,
    }[ratings["entry_fit"]]
    cap = entry_cap
    if information_quality == "vague":
        cap = min(cap, 59)
    if ratings["working_conditions_fit"] == "conflict":
        cap = min(cap, 39)
    if (
        ratings["entry_fit"] == "partial"
        and ratings["direction_fit"] in {"partial", "weak", "conflict"}
        and information_quality != "clear"
    ):
        cap = min(cap, 39)
    return min(score, cap)


def adjusted_ratings(job, job_analysis, matches, ratings):
    """Apply objective semantics to the LLM's priority ratings."""
    adjusted = dict(ratings)
    experience = job_analysis["experience_requirement"]
    clear_entry_role = (
        job_analysis["seniority"] == "junior_entry"
        and experience["expectation"]
        in {"none_stated", "first_exposure", "practical_experience"}
        and experience["minimum_years"] is None
    )
    if clear_entry_role:
        # A clearly labelled entry role must not be downgraded merely because
        # its later tasks have not yet been performed as regular employment.
        adjusted["entry_fit"] = "excellent"
    adjusted["working_conditions_fit"] = {
        "excellent": "excellent",
        "good": "good",
        "weak": "partial",
        "conflict": "conflict",
    }[score_location(job)]
    for group_index, group in enumerate(job_analysis["technology_requirements"]):
        if group["priority"] == "preferred" or group["minimum_count"] <= 1:
            continue
        covered = sum(
            {
                "met": 1.0,
                "partially_met": 0.5,
                "unknown": 0.0,
                "not_met": 0.0,
            }[matches[f"technology:{group_index}:{index}"]["status"]]
            for index in range(len(group["technologies"]))
        )
        if (
            not clear_entry_role
            and covered < group["minimum_count"]
            and adjusted["entry_fit"] in {
            "excellent",
            "good",
            }
        ):
            adjusted["entry_fit"] = "partial"
    return adjusted


def score_location(job):
    """Map the deterministic location precheck to the fixed location dimension."""
    precheck = str(job.get("location_precheck") or "").casefold()
    work_mode = str(job.get("work_mode") or "").casefold()
    if "passt nicht" in precheck or "konflikt" in precheck:
        return "conflict"
    if "100% remote" in precheck or work_mode == "remote":
        return "excellent"
    if "-km-radius" in precheck or "lokal" in precheck or "pendelort" in precheck:
        return "good"
    return "weak"


def find_hard_conflicts(job, job_analysis, matches):
    """Return verified blockers that override an otherwise high score band."""
    conflicts = []
    experience = job_analysis["experience_requirement"]
    if job_analysis["seniority"] == "senior":
        conflicts.append("Seniorniveau ist ausdruecklich gefordert")
    if experience["expectation"] == "senior_expertise":
        conflicts.append("Senior-Expertise ist ausdruecklich gefordert")
    if experience["minimum_years"] is not None and experience["minimum_years"] > 3:
        conflicts.append(
            f"Mehr als drei Jahre Erfahrung gefordert: {experience['minimum_years']}"
        )
    if score_location(job) == "conflict":
        conflicts.append("Standort oder Remote-Regel passt nicht")

    for index, requirement in enumerate(job_analysis["other_requirements"]):
        match = matches[f"other:{index}"]
        if (
            requirement["priority"] == "explicit_requirement"
            and requirement["category"] in HARD_CONFLICT_CATEGORIES
            and match["status"] == "not_met"
        ):
            conflicts.append(requirement["requirement"])
    return conflicts


def confidence_for_result(unknown_count, uncertainties):
    """Derive a compact confidence label from unresolved structured facts."""
    if unknown_count >= 3 or len(uncertainties) >= 3:
        return "low"
    if unknown_count or uncertainties:
        return "medium"
    return "high"


def unique_texts(values, limit):
    """Return unique, bounded texts while preserving source order."""
    result = []
    for value in values:
        text = truncate(value, 300)
        if text and text not in result:
            result.append(text)
        if len(result) == limit:
            break
    return result


def truncate(value, maximum):
    """Keep generated report fields inside their JSON-schema limits."""
    return str(value or "").strip()[:maximum]
