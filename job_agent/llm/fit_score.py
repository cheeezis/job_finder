"""Deterministic scoring for validated two-stage LLM results."""

from job_agent.llm.contract import (
    RATING_POINTS,
    recommendation_for_analysis,
)
from job_agent.profile import ROLE_FAMILY_FIT_RATINGS


STATUS_VALUES = {
    "met": 1.0,
    "partially_met": 0.5,
    "unknown": 0.25,
    "not_met": 0.0,
}
HARD_CONFLICT_CATEGORIES = {"education", "employment", "travel"}
FIT_SCORE_VERSION = 2


def score_two_stage_result(job, job_analysis, profile_match):
    """Turn extracted facts and evidence matches into a fixed 0-100 result."""
    matches = {
        item["requirement_id"]: item for item in profile_match["matches"]
    }
    hard_conflicts = find_hard_conflicts(job, job_analysis, matches)
    ratings = {
        "role_fit": score_role_family(job_analysis),
        "technology_fit": score_technologies(job_analysis, matches),
        "experience_fit": score_experience(job_analysis, matches),
        "location_fit": score_location(job),
        "task_fit": score_role_family(job_analysis),
    }
    scores = {name: RATING_POINTS[rating] for name, rating in ratings.items()}
    overall_score = sum(scores.values())
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
            f"{job_analysis['role_summary']} Deterministischer Fit: "
            f"{overall_score}/100 ({recommendation}).",
            600,
        ),
        "dimension_scores": scores,
        "dimension_ratings": ratings,
        "key_tasks": [truncate(task, 300) for task in job_analysis["tasks"][:3]],
        "key_requirements": [],
        "matching_evidence": matching_evidence,
        "gaps": gaps,
        "risks": unique_texts(hard_conflicts + gaps, limit=3),
        "uncertainties": uncertainties,
        "hard_conflicts": unique_texts(hard_conflicts, limit=3),
    }


def score_role_family(job_analysis):
    """Score the best relevant role family against personal priorities."""
    families = [job_analysis["primary_role_family"]]
    families.extend(job_analysis["secondary_role_families"])
    return max(
        (ROLE_FAMILY_FIT_RATINGS[family] for family in families),
        key=RATING_POINTS.__getitem__,
    )


def score_technologies(job_analysis, matches):
    """Aggregate technology groups according to their minimum-count semantics."""
    groups = [
        (index, group)
        for index, group in enumerate(job_analysis["technology_requirements"])
        if group["priority"] != "preferred"
    ]
    only_preferred = not groups
    if only_preferred:
        groups = list(enumerate(job_analysis["technology_requirements"]))
    if not groups:
        return "good"

    group_values = []
    all_levels_unclear = True
    for group_index, group in groups:
        values = [
            STATUS_VALUES[matches[f"technology:{group_index}:{index}"]["status"]]
            for index in range(len(group["technologies"]))
        ]
        required_count = group["minimum_count"]
        group_values.append(
            sum(sorted(values, reverse=True)[:required_count]) / required_count
        )
        if any(
            technology["expected_level"] != "unclear"
            for technology in group["technologies"]
        ):
            all_levels_unclear = False

    ratio = sum(group_values) / len(group_values)
    if ratio >= 0.9:
        rating = "excellent"
    elif ratio >= 0.65:
        rating = "good"
    elif ratio >= 0.35:
        rating = "partial"
    elif ratio > 0:
        rating = "weak"
    else:
        rating = "partial" if only_preferred else "conflict"

    if all_levels_unclear and rating == "excellent":
        return "good"
    return rating


def score_experience(job_analysis, matches):
    """Score entry suitability without treating projects as employment."""
    seniority = job_analysis["seniority"]
    requirement = job_analysis["experience_requirement"]
    expectation = requirement["expectation"]
    minimum_years = requirement["minimum_years"]
    status = matches["experience"]["status"]

    if (
        seniority == "senior"
        or expectation == "senior_expertise"
        or minimum_years is not None
        and minimum_years > 3
    ):
        return "conflict"
    if seniority == "mid" or expectation == "several_years":
        return "weak"
    if expectation == "none_stated":
        return "excellent" if seniority != "mixed" else "good"
    if expectation == "first_exposure":
        return "excellent" if status in {"met", "partially_met"} else "good"
    if expectation == "practical_experience":
        if requirement["priority"] == "preferred":
            return "good" if status in {"met", "partially_met"} else "partial"
        return "good" if status == "met" else "weak"
    if expectation == "professional_experience":
        return "partial" if requirement["priority"] == "preferred" else "weak"
    return "partial"


def score_location(job):
    """Map the deterministic location precheck to the fixed location dimension."""
    precheck = str(job.get("location_precheck") or "").casefold()
    work_mode = str(job.get("work_mode") or "").casefold()
    if "passt nicht" in precheck or "konflikt" in precheck:
        return "conflict"
    if "100% remote" in precheck or work_mode == "remote":
        return "excellent"
    if "30-km-radius" in precheck or "lokal" in precheck:
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
