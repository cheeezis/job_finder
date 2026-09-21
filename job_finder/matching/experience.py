"""Experience requirements and entry-level scoring."""

import re

from job_finder.matching.matching_rules import (
    BODY_ENTRY_LEVEL_PHRASES,
    FIRST_EXPERIENCE_PHRASES,
    OPTIONAL_EXPERIENCE_PHRASES,
    STRONG_EXPERIENCE_PHRASES,
)
from job_finder.matching.matching_text import contains_any, is_entry_level

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


def analyze_experience(title, full_text, required_years=None):
    """Return experience rank, points and label for normalized job text.

    Call after hard_filter_reason: explicit requirements above three
    years must already be excluded. Numeric requirements take priority
    over entry-level signals; optional experience is weighted less
    strictly than required experience. Lower rank sorts first.
    """
    if required_years is None:
        required_years = extract_required_years(full_text)

    if required_years:
        points = {1: 14, 2: 8, 3: 3}[required_years]
        return {
            "rank": required_years + 1,
            "points": points,
            "label": f"{required_years} Jahr(e) gefordert",
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
    """Detect an experience mention whose surrounding sentence is optional."""
    return any(
        match_is_optional(text, match) for match in re.finditer(EXPERIENCE_TERM, text)
    )


def has_required_experience(text):
    """Return whether applicant experience is stated as a requirement."""
    return any(
        not match_is_optional(text, match)
        for pattern in REQUIRED_EXPERIENCE_PATTERNS
        for match in re.finditer(pattern, text)
    )


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
