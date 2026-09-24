"""Deterministic matching rules for one normalized job posting."""

import re
from datetime import date, timedelta

from job_finder.matching import location_rules
from job_finder.matching.config import LOCAL_SEARCH_RADIUS_KM
from job_finder.matching.experience import (
    analyze_experience,
    extract_required_years,
    strong_experience_is_required,
)
from job_finder.matching.location_rules import is_hybrid, remote_possible_from_germany
from job_finder.matching.matching_rules import (
    BLOCKED_TITLE_WORDS,
    ENTRY_LEVEL_TITLE_EXCEPTIONS,
    GENERAL_IT_ROLE,
    GENERAL_IT_TITLE_KEYWORDS,
    HIGH_TRAVEL_PHRASES,
    MANDATORY_ADVANCED_DEGREE_PATTERNS,
    ROLE_GROUPS,
)
from job_finder.matching.matching_text import (
    contains_any,
    contains_keyword,
    is_entry_level,
    matches_pattern,
)
from job_finder.matching.ranking_weights import ROLE_POINTS, SCORE_LIMITS, SKILL_GROUPS
from job_finder.matching.remote import detect_remote
from job_finder.matching.salary import extract_annual_salary
from job_finder.matching.user_settings import USER_SETTINGS
from job_finder.models import FilterStatus, Job
from job_finder.text import normalize_text, text_is_mainly_english

MAX_JOB_AGE_DAYS = 60
MATCHING_SETTINGS = USER_SETTINGS["matching"]
LOCAL_PLACES = MATCHING_SETTINGS["local_places"]
COMMUTER_LOCATIONS = MATCHING_SETTINGS.get("commuter_locations", [])
PROFILE_DOMAIN_KEYWORDS = MATCHING_SETTINGS["profile_domain_keywords"]
SALARY_TARGET = MATCHING_SETTINGS["salary_target_eur"]
SALARY_MINIMUM = MATCHING_SETTINGS["salary_minimum_eur"]


def score_job(job: Job, today=None):
    """Return a filter decision, match score and reasons for one Job.

    Read the configured profile without changing the job or performing
    I/O. today optionally supplies the reference date for the age check.
    Missing publication dates do not cause exclusion by themselves.

    Every result contains filter_status, match_percent (0-100),
    experience_rank, experience_level and reasons. Included results
    also contain role_group and location_precheck. Excluded results
    have score 0, rank 99 and the first blocking reason. The score is a
    rule-based sorting aid, not a probability of personal suitability.
    """
    reference_date = today or date.today()
    cutoff = reference_date - timedelta(days=MAX_JOB_AGE_DAYS)
    if job.published_at is not None and job.published_at < cutoff:
        return excluded_result(
            f"Veröffentlichung älter als {MAX_JOB_AGE_DAYS} Tage: "
            f"{job.published_at.strftime('%d.%m.%Y')}"
        )

    title = normalize_text(job.title)
    location = normalize_text(job.location_text)
    description = strip_platform_boilerplate(normalize_text(job.description_clean))
    remote = normalize_text(job.remote_text)
    if job.remote_percentage is None:
        remote = normalize_text(
            detect_remote(title, location, description, structured_remote=remote)
        )
    salary_text = structured_salary_text(job)
    employment = normalize_text(job.employment_type or "")
    # Structured employment data must influence preferences even when portals
    # omit words such as "Teilzeit" from title and description.
    full_text = " ".join([title, location, remote, employment, description, salary_text])

    role = find_role(title, description)
    required_years = extract_required_years(full_text)
    filter_reason = hard_filter_reason(
        title=title,
        description=description,
        full_text=full_text,
        role=role,
        career_levels=job.career_levels,
        required_years=required_years,
    )
    if filter_reason:
        return excluded_result(filter_reason)

    location_score = analyze_location_for_role(title, location, remote, description)
    if not location_score["allowed"]:
        return excluded_result(location_score["label"])

    experience = analyze_experience(title, full_text, required_years, description)
    skill_score, skill_labels = score_skills(f"{title} {description}")
    profile_score = score_profile_connection(full_text)

    role_points = ROLE_POINTS[role["id"]]
    score = role_points + skill_score + experience["points"]
    score += location_score["points"] + profile_score
    reasons = [
        f"+{role_points} Rolle: {role['label']}",
        format_skill_reason(skill_score, skill_labels),
        f"+{experience['points']} Erfahrung: {experience['label']}",
        f"+{location_score['points']} Standort: {location_score['label']}",
    ]

    if profile_score:
        reasons.append(f"+{profile_score} Bezug zu Projekten oder Weiterbildungen")

    penalties = score_preferences(full_text)
    for penalty in penalties:
        score -= penalty["points"]
        reasons.append(f"-{penalty['points']} {penalty['label']}")

    score = max(0, min(100, score))
    return {
        "filter_status": FilterStatus.INCLUDED.value,
        "match_percent": score,
        "experience_rank": experience["rank"],
        "experience_level": experience["label"],
        "role_group": role["id"],
        "location_precheck": location_score["label"],
        "reasons": reasons,
    }


def excluded_result(reason):
    """Build the common excluded-result fields with one blocking reason."""
    return {
        "filter_status": FilterStatus.EXCLUDED.value,
        "match_percent": 0,
        "experience_rank": 99,
        "experience_level": "ausgeschlossen",
        "reasons": [reason],
    }


def structured_salary_text(job):
    """Expose structured annual salary data to the existing scoring rules."""
    minimum = job.salary_min_eur
    maximum = job.salary_max_eur
    if minimum is not None and maximum is not None:
        return f"jahresgehalt {minimum} - {maximum} EUR"
    if maximum is not None:
        return f"jahresgehalt {maximum} EUR"
    return ""


def strip_platform_boilerplate(description):
    """Remove portal text that would otherwise look like job requirements."""
    markers = [
        "bei dieser jobboerse erstellen wir fuer stellen",
        "mithilfe von kuenstlicher intelligenz (ki) automatisch generierte zusammenfassungen",
    ]
    positions = [description.find(marker) for marker in markers if marker in description]
    if positions:
        return description[: min(positions)].strip()
    return description


def hard_filter_reason(title, description, full_text, role, career_levels, required_years=None):
    """Return the first blocking job requirement, or an empty string.

    Inputs use normalize_text; role is a matching profile dictionary
    or None. required_years is extracted once for filtering and scoring.
    score_job checks age before these rules and location after them.
    """
    blocked_word = find_blocked_title_word(title)
    if blocked_word:
        return f"Titel enthaelt Ausschlusswort: {blocked_word}"

    if not role:
        return "Titel ist keine erkennbare IT-Rolle"

    advanced_level = structured_advanced_level(career_levels)
    if advanced_level and not is_entry_level(title, description):
        return f"Portal-Karrierestufe ist nicht fuer den Einstieg: {advanced_level}"

    if required_years is None:
        required_years = extract_required_years(full_text)
    if required_years > 3:
        return f"Mehr als 3 Jahre Erfahrung gefordert: {required_years} Jahre"

    if strong_experience_is_required(title, description):
        return "Mehrjaehrige oder fundierte Erfahrung gefordert"

    if any(re.search(pattern, full_text) for pattern in MANDATORY_ADVANCED_DEGREE_PATTERNS):
        return "Verpflichtender Master- oder Promotionsabschluss"

    if contains_any(full_text, HIGH_TRAVEL_PHRASES):
        return "Hohe oder deutschlandweite Reisetatigkeit gefordert"

    return ""


def find_role(title, description):
    """Require an allowed role in the title; body keywords alone never suffice."""
    full_text = f"{title} {description}"
    for role in ROLE_GROUPS:
        if not any(matches_pattern(title, pattern) for pattern in role["patterns"]):
            continue

        if role.get("entry_only") and not is_entry_level(title, description):
            continue

        context_keywords = role.get("context_keywords", [])
        if context_keywords and not contains_any(full_text, context_keywords):
            continue

        excluded_context = role.get("excluded_context_keywords", [])
        if excluded_context and contains_any(full_text, excluded_context):
            continue

        if role["id"] == "testing" and contains_any(title, ["qa", "quality assurance"]):
            testing_context = [
                "software",
                "test",
                "automation",
                "automatisierung",
                "playwright",
                "jest",
            ]
            if not contains_any(full_text, testing_context):
                continue

        if (
            role["id"] == "testing"
            and contains_keyword(title, "verification")
            and not contains_any(full_text, ["software", "test", "automation"])
        ):
            continue

        return role

    if contains_any(title, GENERAL_IT_TITLE_KEYWORDS):
        return GENERAL_IT_ROLE
    return None


def find_blocked_title_word(title):
    """Return a blocked title term, respecting explicit entry exceptions."""
    entry_level = is_entry_level(title, title)
    for word in BLOCKED_TITLE_WORDS:
        # Explicit entry signals win over generic experience labels such as
        # Junior/Senior or Junior IT Project Manager.
        if entry_level and word in ENTRY_LEVEL_TITLE_EXCEPTIONS:
            continue
        if contains_keyword(title, word):
            return word
    return None


def structured_advanced_level(career_levels):
    """Return the first explicitly advanced portal seniority label."""
    advanced_words = (
        "senior",
        "sr",
        "staff",
        "lead",
        "principal",
        "manager",
        "director",
        "executive",
    )
    for level in career_levels or []:
        normalized = normalize_text(str(level))
        for word in advanced_words:
            if contains_keyword(normalized, word):
                return str(level).strip()
    return None


def score_skills(text):
    """Return capped skill points and labels matched in normalized text."""
    matched = [group for group in SKILL_GROUPS if contains_any(text, group["keywords"])]
    points = min(SCORE_LIMITS["skills"], sum(group["points"] for group in matched))
    return points, [group["label"] for group in matched]


def format_skill_reason(points, labels):
    """Format skill points and matched labels for the visible score reasons."""
    if not labels:
        return "+0 Technologien: keine direkte Profilueberschneidung"
    return f"+{points} Technologien: {', '.join(labels)}"


def score_profile_connection(text):
    """Award profile points when normalized text matches a domain keyword."""
    return SCORE_LIMITS["profile"] if contains_any(text, PROFILE_DOMAIN_KEYWORDS) else 0


def analyze_location_for_role(title, location, remote, description):
    """Allow explicit entry roles with hybrid work to reach manual review."""
    result = analyze_location(location, remote, description)
    if result["allowed"]:
        return result
    if (
        is_entry_level(title, title)
        and is_hybrid(remote)
        and remote_possible_from_germany(location, description)
    ):
        return {
            "allowed": True,
            "points": 0,
            "label": "Junior-Hybrid außerhalb des Suchgebiets; Präsenzumfang prüfen",
        }
    return result


def score_preferences(full_text):
    """Return point deductions and labels for normalized preference conflicts."""
    penalties = []

    if "teilzeit" in full_text and "vollzeit" not in full_text:
        penalties.append({"points": 4, "label": "reine Teilzeitstelle"})

    if contains_any(full_text, ["freelance", "freelancer", "freiberuflich"]):
        penalties.append({"points": 8, "label": "freiberufliche Beschäftigung"})

    if contains_any(full_text, ["werkstudent", "working student"]):
        penalties.append({"points": 8, "label": "Werkstudentenstelle"})

    if contains_any(
        full_text,
        [
            "praktikum",
            "praktikant",
            "internship",
            "ausbildung",
            "auszubildende",
            "auszubildender",
            "auszubildenden",
            "azubi",
            "duales studium",
            "dual study",
            "abschlussarbeit",
            "bachelorarbeit",
            "thesis",
        ],
    ):
        penalties.append({"points": 12, "label": "Ausbildungs-/Studienformat"})

    if contains_any(full_text, ["arbeitnehmerueberlassung", "zeitarbeit", "personaldienstleister"]):
        penalties.append({"points": 3, "label": "Arbeitnehmerueberlassung/Zeitarbeit"})

    if text_is_mainly_english(full_text):
        penalties.append({"points": 2, "label": "ueberwiegend englischsprachige Stelle"})

    salary = extract_annual_salary(full_text)
    if salary and SALARY_MINIMUM is not None and salary[1] < SALARY_MINIMUM:
        penalties.append({"points": 5, "label": "Gehalt unter persoenlichem Minimum"})
    elif salary and SALARY_TARGET is not None and salary[1] < SALARY_TARGET:
        penalties.append({"points": 3, "label": "Gehalt unter Wunschgehalt"})

    return penalties


def analyze_location(location, remote, description):
    """Analyze location using the currently configured local and commuter rules."""
    return location_rules.analyze_location(
        location,
        remote,
        description,
        local_places=LOCAL_PLACES,
        commuter_locations=COMMUTER_LOCATIONS,
        radius=LOCAL_SEARCH_RADIUS_KM,
    )
