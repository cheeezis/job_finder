"""Experience requirements and entry-level scoring."""

import re

from job_finder.matching.matching_rules import (
    APPLICANT_SUBJECT_PHRASES,
    BODY_ENTRY_LEVEL_PHRASES,
    EMPLOYER_CONTEXT_WORDS,
    FIRST_EXPERIENCE_PHRASES,
    OPTIONAL_EXPERIENCE_PHRASES,
    PROFILE_HEADING_PHRASES,
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
YEAR_QUALIFIER = r"(?:(mehr als|ueber|more than|over|mindestens|mind\.?|at least|minimum of)\s*)?"
MORE_THAN_QUALIFIERS = {"mehr als", "ueber", "more than", "over"}
YEAR_RANGE_PATTERNS = [
    rf"(\d+)\s*(?:-|bis|to)\s*(\d+)\s*{YEAR_UNIT}[\s\S]{{0,80}}?{EXPERIENCE_TERM}",
    rf"{EXPERIENCE_TERM}[\s\S]{{0,80}}?(\d+)\s*(?:-|bis|to)\s*(\d+)\s*{YEAR_UNIT}",
]
SINGLE_YEAR_PATTERNS = [
    rf"{YEAR_QUALIFIER}(\d+)\s*\+?\s*{YEAR_UNIT}[\s\S]{{0,80}}?{EXPERIENCE_TERM}",
    rf"{EXPERIENCE_TERM}[\s\S]{{0,80}}?{YEAR_QUALIFIER}(\d+)\s*\+?\s*{YEAR_UNIT}",
]
# "5+ years in sales" is a requirement even without the word experience;
# company facts such as "seit 20 Jahren am Markt" carry no plus sign.
PLUS_YEARS_PATTERN = rf"(\d+)\s*\+\s*{YEAR_UNIT}"

REQUIRED_EXPERIENCE_PATTERNS = [
    r"\b(?:du|sie)\s+(?:hast|haben|bringst|bringen|verfuegst|verfuegen)"
    r"[\s\S]{0,70}\berfahr(?:ung|ungen)\b",
    r"\b(?:erfahrung|erfahrungen)\s+(?:im|in|als|mit)\b",
    r"\b(?:hands-on|practical|previous|professional|relevant|solid)\s+experience\b",
    r"\bexperience\s+(?:in|with|using|working|building|developing)\b",
    r"\bexperienced\s+(?:in|with)\b",
]


def analyze_experience(title, full_text, required_years=None, description=None):
    """Return experience rank, points and label for normalized job text.

    Call after hard_filter_reason: explicit requirements above three
    years must already be excluded. Numeric requirements take priority
    over entry-level signals; optional experience is weighted less
    strictly than required experience. Lower rank sorts first. Without a
    usable description only the title can signal an entry-level role.
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

    if (
        description is not None
        and description_is_missing(description)
        and not is_entry_level(title)
    ):
        return {"rank": 4, "points": 8, "label": "Beschreibung fehlt, Erfahrung unklar"}

    if contains_any(full_text, BODY_ENTRY_LEVEL_PHRASES):
        return {"rank": 0, "points": 25, "label": "klare Einstiegsstelle"}

    if contains_any(full_text, STRONG_EXPERIENCE_PHRASES):
        return {"rank": 5, "points": 6, "label": "mehrjaehrige/fundierte Erfahrung ohne Jahreszahl"}

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


def description_is_missing(description):
    """Return whether only an empty or cut-off teaser text is available."""
    text = description.strip()
    return not text or (len(text) < 400 and text.endswith(("...", "…")))


def extract_required_years(text):
    """Return the highest explicit experience requirement up to ten years."""
    years = []
    for match in required_matches(text, YEAR_RANGE_PATTERNS):
        lower, upper = match.groups()[-2:]
        years.extend([int(lower), int(upper)])
    for match in required_matches(text, SINGLE_YEAR_PATTERNS):
        qualifier, value = match.groups()[-2:]
        year = int(value)
        if qualifier and qualifier.strip() in MORE_THAN_QUALIFIERS:
            year += 1
        years.append(year)
    for match in required_matches(text, [PLUS_YEARS_PATTERN]):
        years.append(int(match.group(1)))
    plausible = [year for year in years if 0 < year <= 10]
    return max(plausible, default=0)


def experience_is_optional(text):
    """Detect an experience mention whose surrounding sentence is optional."""
    return any(match_is_optional(text, match) for match in re.finditer(EXPERIENCE_TERM, text))


def has_required_experience(text):
    """Return whether applicant experience is stated as a requirement."""
    return any(required_matches(text, REQUIRED_EXPERIENCE_PATTERNS))


def required_matches(text, patterns):
    """Yield pattern matches whose nearby clause does not make them optional."""
    for pattern in patterns:
        for match in re.finditer(pattern, text):
            if not match_is_optional(text, match):
                yield match


def strong_experience_is_required(title, description):
    """Reject vague seniority requirements.

    For entry-level titles only a requirement addressed to the applicant
    counts, so employer self-descriptions cannot exclude a junior role.
    """
    entry_level = is_entry_level(title, description)
    for phrase in STRONG_EXPERIENCE_PHRASES:
        match = re.search(re.escape(phrase), description)
        if match is None:
            continue
        context = description[max(0, match.start() - 55) : match.end() + 55]
        if contains_any(context, OPTIONAL_EXPERIENCE_PHRASES):
            continue
        if not entry_level or is_addressed_to_applicant(description, match):
            return True
    return False


def is_addressed_to_applicant(text, match):
    """Tell an applicant requirement from an employer self-description."""
    start, end = match_context(text, match, 80)
    clause = text[start:end]
    if contains_any(clause, EMPLOYER_CONTEXT_WORDS):
        return False
    heading_window = text[max(0, match.start() - 150) : match.start()]
    return contains_any(clause, APPLICANT_SUBJECT_PHRASES) or contains_any(
        heading_window, PROFILE_HEADING_PHRASES
    )


def match_is_optional(text, match):
    """Check whether optional wording belongs to a nearby requirement."""
    start, end = match_context(text, match, context_size=55)
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
