"""Deterministic matching rules for one normalized job posting."""

import re

from job_finder.config import LOCAL_SEARCH_RADIUS_KM
from job_finder.models import FilterStatus, Job
from job_finder.remote import detect_remote
from job_finder.profile import (
    BLOCKED_TITLE_WORDS,
    BODY_ENTRY_LEVEL_PHRASES,
    COMMUTER_LOCATIONS,
    ENTRY_LEVEL_WORDS,
    FIRST_EXPERIENCE_PHRASES,
    FOREIGN_ONLY_LOCATION_WORDS,
    GERMANY_LOCATION_WORDS,
    HIGH_TRAVEL_PHRASES,
    LOCAL_PLACES,
    MANDATORY_ADVANCED_DEGREE_PATTERNS,
    GENERAL_IT_ROLE,
    GENERAL_IT_TITLE_KEYWORDS,
    OPTIONAL_EXPERIENCE_PHRASES,
    PROFILE_DOMAIN_KEYWORDS,
    ROLE_GROUPS,
    SALARY_MINIMUM,
    SALARY_TARGET,
    SCORE_LIMITS,
    SKILL_GROUPS,
    STRONG_EXPERIENCE_PHRASES,
)
from job_finder.text import normalize_text


EXPERIENCE_TERM = (
    r"(?:berufserfahrung|arbeitserfahrung|entwicklungserfahrung|"
    r"praktische erfahrung|praxiserfahrung|"
    r"professional experience|practical experience|hands-on experience|"
    r"erfahrung(?:en)?|experience)"
)
YEAR_UNIT = r"(?:jahre?n?|years?|yrs?)"
MORE_THAN_QUALIFIERS = {
    "mehr als",
    "ueber",
    "more than",
    "over",
}
REQUIRED_EXPERIENCE_PATTERNS = [
    r"\b(?:du|sie)\s+(?:hast|haben|bringst|bringen|verfuegst|verfuegen)"
    r"[\s\S]{0,70}\berfahr(?:ung|ungen)\b",
    r"\b(?:erfahrung|erfahrungen)\s+(?:im|in|als|mit)\b",
    r"\b(?:hands-on|practical|previous|professional|relevant|solid)\s+experience\b",
    r"\bexperience\s+(?:in|with|using|working|building|developing)\b",
    r"\bexperienced\s+(?:in|with)\b",
]
def score_job(job: Job):
    """Return a fixed 0-100 match score and explain every decision."""
    title = normalize_text(job.title)
    location = normalize_text(job.location_text)
    description = strip_platform_boilerplate(normalize_text(job.description_clean))
    remote = normalize_text(job.remote_text)
    if job.remote_percentage is None:
        remote = normalize_text(
            detect_remote(title, location, description, structured_remote=remote)
        )
    salary_text = structured_salary_text(job)
    full_text = " ".join([title, location, remote, description, salary_text])

    role = find_role(title, description)
    allowed, filter_reason = passes_hard_filters(
        title=title,
        description=description,
        location=location,
        remote=remote,
        full_text=full_text,
        role=role,
    )
    if not allowed:
        return excluded_result(filter_reason)

    experience = analyze_experience(title, full_text)
    location_score = analyze_location(location, remote, description)
    skill_score, skill_labels = score_skills(f"{title} {description}")
    profile_score = score_profile_connection(full_text)

    score = role["points"] + skill_score + experience["points"]
    score += location_score["points"] + profile_score
    reasons = [
        f'+{role["points"]} Rolle: {role["label"]}',
        format_skill_reason(skill_score, skill_labels),
        f'+{experience["points"]} Erfahrung: {experience["label"]}',
        f'+{location_score["points"]} Standort: {location_score["label"]}',
    ]

    if profile_score:
        reasons.append(f"+{profile_score} Bezug zu bisherigen Praxisprojekten")

    penalties = score_preferences(full_text)
    for penalty in penalties:
        score -= penalty["points"]
        reasons.append(f'-{penalty["points"]} {penalty["label"]}')

    score = max(0, min(100, score))
    return {
        "filter_status": FilterStatus.INCLUDED.value,
        "raw_score": score,
        "match_percent": score,
        "experience_rank": experience["rank"],
        "experience_level": experience["label"],
        "role_group": role["id"],
        "location_precheck": location_score["label"],
        "reasons": reasons,
    }


def excluded_result(reason):
    return {
        "filter_status": FilterStatus.EXCLUDED.value,
        "raw_score": 0,
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


def passes_hard_filters(title, description, location, remote, full_text, role):
    blocked_word = find_blocked_title_word(title)
    if blocked_word:
        return False, f"Titel enthaelt Ausschlusswort: {blocked_word}"

    if not role:
        return False, "Titel ist keine erkennbare IT-Rolle"

    years = extract_required_years(full_text)
    if years > 3:
        return False, f"Mehr als 3 Jahre Erfahrung gefordert: {years} Jahre"

    if strong_experience_is_required(title, description):
        return False, "Mehrjaehrige oder fundierte Erfahrung gefordert"

    if any(re.search(pattern, full_text) for pattern in MANDATORY_ADVANCED_DEGREE_PATTERNS):
        return False, "Verpflichtender Master- oder Promotionsabschluss"

    if contains_any(full_text, HIGH_TRAVEL_PHRASES):
        return False, "Hohe oder deutschlandweite Reisetatigkeit gefordert"

    location_score = analyze_location(location, remote, description)
    if not location_score["allowed"]:
        return False, location_score["label"]

    return True, ""


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

        if role["id"] == "testing" and contains_keyword(title, "verification"):
            if not contains_any(full_text, ["software", "test", "automation"]):
                continue

        return role

    if contains_any(title, GENERAL_IT_TITLE_KEYWORDS):
        return GENERAL_IT_ROLE
    return None


def find_blocked_title_word(title):
    for word in BLOCKED_TITLE_WORDS:
        # Explicit entry signals win over generic experience labels such as
        # Junior/Senior or Junior IT Project Manager.
        if word in {
            "senior",
            "experte",
            "expert",
            "lead",
            "principal",
            "head",
            "leitung",
            "leiter",
            "projektleiter",
            "projektmanager",
            "teamleiter",
            "abteilungsleiter",
            "manager",
            "testmanager",
            "test manager",
        } and is_entry_level(title, title):
            continue
        if contains_keyword(title, word):
            return word
    return None


def analyze_experience(title, full_text):
    years = extract_required_years(full_text)

    if years:
        points = {1: 14, 2: 8, 3: 3}[years]
        return {
            "rank": years + 1,
            "points": points,
            "label": f"{years} Jahr(e) gefordert",
        }

    if contains_any(full_text, BODY_ENTRY_LEVEL_PHRASES):
        return {"rank": 0, "points": 25, "label": "klare Einstiegsstelle"}

    if contains_any(full_text, STRONG_EXPERIENCE_PHRASES):
        return {
            "rank": 5,
            "points": 6,
            "label": "mehrjaehrige/fundierte Erfahrung ohne Jahreszahl",
        }

    if is_entry_level(title, full_text):
        return {"rank": 0, "points": 25, "label": "klare Einstiegsstelle"}

    if contains_any(full_text, FIRST_EXPERIENCE_PHRASES):
        return {"rank": 0, "points": 25, "label": "erste Erfahrung reicht aus"}

    if has_required_experience(full_text):
        return {
            "rank": 4,
            "points": 8,
            "label": "praktische Vorerfahrung mit Technologien vorausgesetzt",
        }

    if experience_is_optional(full_text):
        return {"rank": 1, "points": 18, "label": "Erfahrung nur wuenschenswert"}

    return {"rank": 1, "points": 20, "label": "keine klare Jahresanforderung"}


def extract_required_years(text):
    """Return the highest explicit experience requirement up to ten years."""
    range_patterns = [
        rf"(\d+)\s*(?:-|bis|to)\s*(\d+)\s*{YEAR_UNIT}"
        rf"[\s\S]{{0,80}}?{EXPERIENCE_TERM}",
        rf"{EXPERIENCE_TERM}[\s\S]{{0,80}}?"
        rf"(\d+)\s*(?:-|bis|to)\s*(\d+)\s*{YEAR_UNIT}",
    ]
    years = []
    for pattern in range_patterns:
        for match in re.finditer(pattern, text):
            if match_is_optional(text, match):
                continue
            lower, upper = match.groups()[-2:]
            years.extend([int(lower), int(upper)])

    single_patterns = [
        rf"(?:(mehr als|ueber|more than|over|mindestens|mind\.?|at least|"
        rf"minimum of)\s*)?(\d+)\s*\+?\s*{YEAR_UNIT}"
        rf"[\s\S]{{0,80}}?{EXPERIENCE_TERM}",
        rf"{EXPERIENCE_TERM}[\s\S]{{0,80}}?"
        rf"(?:(mehr als|ueber|more than|over|mindestens|mind\.?|at least|"
        rf"minimum of)\s*)?(\d+)\s*\+?\s*{YEAR_UNIT}",
    ]
    for pattern in single_patterns:
        for match in re.finditer(pattern, text):
            if match_is_optional(text, match):
                continue
            qualifier, value = match.groups()[-2:]
            year = int(value)
            if qualifier and qualifier.strip() in MORE_THAN_QUALIFIERS:
                year += 1
            years.append(year)

    plausible = [year for year in years if 0 < year <= 10]
    return max(plausible, default=0)


def experience_is_optional(text):
    experience_pattern = re.compile(EXPERIENCE_TERM)
    for match in experience_pattern.finditer(text):
        if match_is_optional(text, match):
            return True
    return False


def has_required_experience(text):
    """Return whether applicant experience is stated as a requirement."""
    for pattern in REQUIRED_EXPERIENCE_PATTERNS:
        for match in re.finditer(pattern, text):
            if not match_is_optional(text, match):
                return True
    return False


def strong_experience_is_required(title, description):
    """Reject vague seniority requirements unless the vacancy is entry-level."""
    if is_entry_level(title, description):
        return False
    for phrase in STRONG_EXPERIENCE_PHRASES:
        position = description.find(phrase)
        if position < 0:
            continue
        context = description[max(0, position - 55) : position + len(phrase) + 55]
        if not contains_any(context, OPTIONAL_EXPERIENCE_PHRASES):
            return True
    return False


def match_is_optional(text, match, context_size=55):
    """Check whether optional wording belongs to a nearby requirement."""
    start, end = match_context(text, match, context_size)
    return contains_any(text[start:end], OPTIONAL_EXPERIENCE_PHRASES)


def match_context(text, match, context_size):
    """Return a nearby clause without crossing clear punctuation boundaries."""
    start = max(0, match.start() - context_size)
    end = min(len(text), match.end() + context_size)

    for separator in ".!?;\n":
        left_boundary = text.rfind(separator, start, match.start())
        if left_boundary >= 0:
            start = max(start, left_boundary + 1)

        right_boundary = text.find(separator, match.end(), end)
        if right_boundary >= 0:
            end = min(end, right_boundary)

    return start, end


def score_skills(text):
    matched = [group for group in SKILL_GROUPS if contains_any(text, group["keywords"])]
    points = min(SCORE_LIMITS["skills"], sum(group["points"] for group in matched))
    return points, [group["label"] for group in matched]


def format_skill_reason(points, labels):
    if not labels:
        return "+0 Technologien: keine direkte Profilueberschneidung"
    return f'+{points} Technologien: {", ".join(labels)}'


def score_profile_connection(text):
    return SCORE_LIMITS["profile"] if contains_any(text, PROFILE_DOMAIN_KEYWORDS) else 0


def analyze_location(location, remote, description):
    full_remote = is_full_remote(location, remote)
    if full_remote and not remote_possible_from_germany(location, description):
        return {
            "allowed": False,
            "points": 0,
            "label": "Remote-Stelle ist nicht aus Deutschland ausuebbar",
        }

    if is_local_area(location):
        radius_label = f"{LOCAL_SEARCH_RADIUS_KM}-km-Radius"
        if full_remote:
            return {"allowed": True, "points": 15, "label": "lokal und 100% Remote"}
        if is_hybrid(remote):
            return {
                "allowed": True,
                "points": 13,
                "label": f"{radius_label} und Hybrid",
            }
        return {"allowed": True, "points": 10, "label": f"im {radius_label}"}

    if full_remote:
        return {"allowed": True, "points": 15, "label": "100% Remote aus Deutschland"}

    commuter_location = find_commuter_location(location)
    if commuter_location:
        minimum = commuter_location["minimum_remote_percentage"]
        percentage = remote_percent(remote)
        if percentage >= minimum:
            return {
                "allowed": True,
                "points": 8,
                "label": (
                    f"Pendelort {commuter_location['search_location']} mit "
                    f"{percentage}% Remote"
                ),
            }

    return {"allowed": False, "points": 0, "label": "Ort/Remote passt nicht"}


def is_local_area(location):
    return contains_any(location, LOCAL_PLACES)


def find_commuter_location(location):
    for item in COMMUTER_LOCATIONS:
        excluded_aliases = [
            normalize_text(alias) for alias in item.get("excluded_aliases", [])
        ]
        if contains_any(location, excluded_aliases):
            continue
        aliases = [normalize_text(alias) for alias in item["aliases"]]
        if contains_any(location, aliases):
            return item
    return None


def is_full_remote(location, remote):
    if remote_percent(remote) >= 100:
        return True
    if remote in ["remote", "fully remote", "full remote"]:
        return True

    # A structured location explicitly labelled remote is stronger evidence
    # than a generic "Homeoffice possible" phrase.
    return contains_keyword(location, "remote")


def remote_possible_from_germany(location, description):
    combined = f"{location} {description[:1200]}"
    if contains_any(combined, GERMANY_LOCATION_WORDS):
        return True
    if "emea" in location:
        return False
    return not contains_any(location, FOREIGN_ONLY_LOCATION_WORDS)


def is_hybrid(remote):
    return contains_any(remote, ["hybrid", "homeoffice", "home office"]) or 0 < remote_percent(remote) < 100


def remote_percent(remote):
    match = re.search(r"(\d+)\s*%", remote)
    return int(match.group(1)) if match else 0


def score_preferences(full_text):
    penalties = []

    if "teilzeit" in full_text and "vollzeit" not in full_text:
        penalties.append({"points": 4, "label": "reine Teilzeitstelle"})

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


def extract_annual_salary(text):
    """Extract explicit annual salary ranges without guessing from unrelated numbers."""
    number = r"(?:\d{2,3}(?:[.\s]\d{3})|\d{5,6}|\d{2,3}\s*k)"
    range_pattern = rf"({number})\s*(?:-|\u2013|bis|to)\s*({number})\s*(?:eur|euro|\u20ac)"
    ranges = re.findall(range_pattern, text)
    if ranges:
        values = [(salary_number(low), salary_number(high)) for low, high in ranges]
        plausible = [(low, high) for low, high in values if valid_salary(low) and valid_salary(high)]
        if plausible:
            return max(plausible, key=lambda item: item[1])

    salary_context_patterns = [
        rf"(?:jahresgehalt|gehalt|salary|verguetung)[^.!\n]{{0,40}}({number})\s*(?:eur|euro|\u20ac)?",
        rf"({number})\s*(?:eur|euro|\u20ac)\s*(?:brutto\s*)?(?:pro jahr|im jahr|jaehrlich|p\.a\.)",
    ]
    values = []
    for pattern in salary_context_patterns:
        values.extend(salary_number(value) for value in re.findall(pattern, text))

    plausible = [value for value in values if valid_salary(value)]
    if plausible:
        value = max(plausible)
        return value, value
    return None


def salary_number(value):
    cleaned = str(value).lower().replace(".", "").replace(" ", "")
    if cleaned.endswith("k"):
        return int(cleaned[:-1]) * 1000
    return int(cleaned)


def valid_salary(value):
    return 20_000 <= value <= 200_000


def text_is_mainly_english(text):
    german_words = ["und", "wir", "du", "sie", "deine", "ihre", "aufgaben", "kenntnisse"]
    english_words = ["and", "we", "you", "your", "responsibilities", "requirements", "experience"]
    german_count = sum(len(re.findall(keyword_pattern(word), text)) for word in german_words)
    english_count = sum(len(re.findall(keyword_pattern(word), text)) for word in english_words)
    return english_count >= 5 and english_count > german_count * 2


def contains_any(text, words):
    return any(contains_keyword(text, word) for word in words)


def is_entry_level(title, description=""):
    """Return whether this specific vacancy explicitly welcomes beginners."""
    return contains_any(title, ENTRY_LEVEL_WORDS) or contains_any(
        description,
        BODY_ENTRY_LEVEL_PHRASES,
    )


def contains_keyword(text, keyword):
    return re.search(keyword_pattern(keyword), text) is not None


def keyword_pattern(keyword):
    normalized = normalize_text(keyword)
    escaped = re.escape(normalized)
    if re.fullmatch(r"[a-z0-9_]+", normalized):
        return rf"(?<!\w){escaped}(?!\w)"
    return escaped


def matches_pattern(text, pattern):
    return all(contains_keyword(text, part) for part in pattern)
