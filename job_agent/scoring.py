"""Deterministic matching rules for one normalized job posting."""

import re

from job_agent.profile import (
    ADMIN_TITLE_WORDS,
    BLOCKED_FOCUS_KEYWORDS,
    BLOCKED_TITLE_WORDS,
    BODY_ENTRY_LEVEL_PHRASES,
    ENTRY_LEVEL_WORDS,
    EXPLICIT_FOCUS_PHRASES,
    FIRST_EXPERIENCE_PHRASES,
    FOREIGN_ONLY_LOCATION_WORDS,
    GERMANY_LOCATION_WORDS,
    HIGH_TRAVEL_PHRASES,
    LOCAL_PLACES,
    MANDATORY_ADVANCED_DEGREE_PATTERNS,
    MODERN_WORKPLACE_TITLE_WORDS,
    OPTIONAL_EXPERIENCE_PHRASES,
    PROFILE_DOMAIN_KEYWORDS,
    ROLE_GROUPS,
    SALARY_MINIMUM,
    SALARY_TARGET,
    SCORE_LIMITS,
    SKILL_GROUPS,
    STRONG_EXPERIENCE_PHRASES,
    SUPPORTED_TITLE_TECHNOLOGIES,
    UNSUPPORTED_TITLE_TECHNOLOGIES,
)
from job_agent.text import normalize_text


SAP_FOCUS_KEYWORDS = {"sap", "abap", "s/4hana", "s4hana"}

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
PROFESSIONAL_EXPERIENCE_PATTERNS = [
    r"\bberufserfahrung\b",
    r"\barbeitserfahrung\b",
    r"\bentwicklungserfahrung\b",
    r"\b(?:professional|commercial|previous)\s+experience\b",
    r"\bexperience[\s\S]{0,45}\bcommercial environment\b",
    r"\bworked in (?:a )?similar\b",
    r"\bmid-senior level\b",
    r"\bstarke erfahrung\b",
    r"\bstrong experience\b",
]


def score_job(job):
    """Return a fixed 0-100 match score and explain every decision."""
    title = normalize_text(job.get("title", ""))
    location = normalize_text(job.get("location", ""))
    remote = normalize_text(job.get("remote", ""))
    description = strip_platform_boilerplate(normalize_text(job.get("description", "")))
    full_text = " ".join([title, location, remote, description])

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
        "status": "included",
        "raw_score": score,
        "match_percent": score,
        "experience_rank": experience["rank"],
        "experience_level": experience["label"],
        "role_group": role["id"],
        "reasons": reasons,
    }


def excluded_result(reason):
    return {
        "status": "excluded",
        "raw_score": 0,
        "match_percent": 0,
        "experience_rank": 99,
        "experience_level": "ausgeschlossen",
        "reasons": [reason],
    }


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

    if contains_any(title, ADMIN_TITLE_WORDS) and not is_entry_level(
        title,
        full_text,
    ):
        return False, "Systemadministration ist keine Einstiegsrolle"

    if contains_any(title, MODERN_WORKPLACE_TITLE_WORDS) and not is_entry_level(
        title,
        full_text,
    ):
        return False, "Microsoft-Cloud/Modern-Workplace ist keine Einstiegsrolle"

    if not role:
        return False, "Titel passt zu keiner gesuchten Rollenfamilie"

    unsupported = find_unsupported_title_technology(title)
    junior_abap = role["id"] == "junior_sap" and unsupported == "abap"
    if unsupported and not junior_abap:
        return False, f"Nicht passender Technologie-Schwerpunkt im Titel: {unsupported}"

    blocked_focus = find_blocked_focus_keyword(title, description)
    junior_sap_focus = (
        role["id"] == "junior_sap" and blocked_focus in SAP_FOCUS_KEYWORDS
    )
    if blocked_focus and not junior_sap_focus:
        return False, f"Fachlicher Fokus passt nicht: {blocked_focus}"

    years = extract_required_years(full_text)
    if years > 3:
        return False, f"Mehr als 3 Jahre Erfahrung gefordert: {years} Jahre"

    if has_required_professional_experience(full_text) and not is_entry_level(
        title,
        full_text,
    ):
        return False, "Berufs- oder Rollenerfahrung wird vorausgesetzt"

    if any(re.search(pattern, full_text) for pattern in MANDATORY_ADVANCED_DEGREE_PATTERNS):
        return False, "Verpflichtender Master- oder Promotionsabschluss"

    if contains_any(full_text, HIGH_TRAVEL_PHRASES):
        return False, "Hohe oder deutschlandweite Reisetatigkeit gefordert"

    salary = extract_annual_salary(full_text)
    if salary and salary[1] < SALARY_MINIMUM:
        return False, f"Gehaltsobergrenze liegt unter {format_euros(SALARY_MINIMUM)}"

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

    return None


def find_blocked_title_word(title):
    for word in BLOCKED_TITLE_WORDS:
        # Mixed level ads such as "Junior/Senior" remain reviewable.
        if word == "senior" and contains_keyword(title, "junior"):
            continue
        if contains_keyword(title, word):
            return word
    return None


def find_unsupported_title_technology(title):
    if contains_any(title, SUPPORTED_TITLE_TECHNOLOGIES):
        return None
    for technology in UNSUPPORTED_TITLE_TECHNOLOGIES:
        if contains_keyword(title, technology):
            return technology
    return None


def find_blocked_focus_keyword(title, description):
    for keyword in BLOCKED_FOCUS_KEYWORDS:
        if contains_keyword(title, keyword):
            return keyword

        # Body mentions only become hard blockers when an explicit focus phrase
        # names the technology. Repeated incidental mentions are not enough.
        for phrase in EXPLICIT_FOCUS_PHRASES:
            if contains_keyword(description[:1800], phrase.format(keyword=keyword)):
                return keyword
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

    if experience_is_optional(full_text):
        return {"rank": 1, "points": 18, "label": "Erfahrung nur wuenschenswert"}

    if has_required_experience(full_text):
        return {
            "rank": 4,
            "points": 8,
            "label": "praktische Vorerfahrung mit Technologien vorausgesetzt",
        }

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


def has_required_professional_experience(text):
    """Detect mandatory professional or prior-role experience."""
    if extract_required_years(text):
        return True

    patterns = list(PROFESSIONAL_EXPERIENCE_PATTERNS)
    patterns.extend(keyword_pattern(phrase) for phrase in STRONG_EXPERIENCE_PHRASES)
    for pattern in patterns:
        for match in re.finditer(pattern, text):
            if match_is_optional(text, match):
                continue
            if match_is_first_experience(text, match):
                continue
            if match_is_employer_context(text, match):
                continue
            return True
    return False


def match_is_optional(text, match, context_size=55):
    """Check whether optional wording belongs to a nearby requirement."""
    start, end = match_context(text, match, context_size)
    return contains_any(text[start:end], OPTIONAL_EXPERIENCE_PHRASES)


def match_is_first_experience(text, match, context_size=45):
    """Check whether a requirement explicitly asks only for first experience."""
    start, end = match_context(text, match, context_size)
    return contains_any(text[start:end], FIRST_EXPERIENCE_PHRASES)


def match_is_employer_context(text, match, context_size=100):
    """Ignore company history and team-composition experience statements."""
    start = max(0, match.start() - context_size)
    prefix = text[start : match.start()]
    return contains_any(
        prefix,
        [
            "unser team umfasst",
            "unsere teammitglieder",
            "unsere experten",
            "expert:innen mit",
            "experten mit",
            "als unternehmen",
        ],
    )


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
        if full_remote:
            return {"allowed": True, "points": 15, "label": "lokal und 100% Remote"}
        if is_hybrid(remote):
            return {"allowed": True, "points": 13, "label": "30-km-Radius und Hybrid"}
        return {"allowed": True, "points": 10, "label": "im 30-km-Radius"}

    if full_remote:
        return {"allowed": True, "points": 15, "label": "100% Remote aus Deutschland"}

    if is_frankfurt(location) and remote_percent(remote) >= 80:
        return {"allowed": True, "points": 8, "label": "Frankfurt mit mindestens 80% Remote"}

    return {"allowed": False, "points": 0, "label": "Ort/Remote passt nicht"}


def is_local_area(location):
    return contains_any(location, LOCAL_PLACES)


def is_frankfurt(location):
    return contains_any(location, ["frankfurt", "ffm"])


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
    if salary and SALARY_MINIMUM <= salary[1] < SALARY_TARGET:
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


def format_euros(value):
    return f"{value:,.0f} EUR".replace(",", ".")


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
