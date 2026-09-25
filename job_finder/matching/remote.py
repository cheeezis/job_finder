"""Central remote-work detection shared by all source adapters."""

import re

from job_finder.models import WorkMode
from job_finder.text import normalize_text

REMOTE_WORDS = ["remote", "homeoffice", "home office", "mobiles arbeiten", "hybrid", "telearbeit"]

FULL_REMOTE_PHRASES = [
    "100% remote",
    "100 % remote",
    "100% homeoffice",
    "100 % homeoffice",
    "100% home office",
    "100 % home office",
    "fully remote",
    "full remote",
    "komplett remote",
    "vollstaendig remote",
    "ausschliesslich remote",
]

NO_REMOTE_PHRASES = [
    "kein remote",
    "nicht remote",
    "ohne remote",
    "kein homeoffice",
    "homeoffice nicht moeglich",
    "remote: 0%",
    "remote 0%",
    "0% remote",
    "0 % remote",
]


def detect_remote(*text_parts, structured_remote=""):
    """Return 100%, an explicit percentage, homeoffice, or 0%."""
    text = normalize_text(" ".join(str(part or "") for part in text_parts))
    structured = normalize_text(structured_remote)

    percent = extract_remote_percent(text)
    if percent:
        return f"{percent}%"

    if structured in ["100%", "100", "full", "fully remote", "remote"]:
        return "100%"

    if contains_any_substring(text, FULL_REMOTE_PHRASES):
        return "100%"

    if contains_any_substring(text, NO_REMOTE_PHRASES):
        return "0%"

    if structured == "homeoffice" or contains_any_substring(text, REMOTE_WORDS):
        return "homeoffice"

    return "0%"


def extract_remote_percent(text):
    """Find the highest percentage that is clearly connected to remote work."""
    # Day counts may be spelled out: ein/eine/einen = 1, zwei to fuenf = 2 to 5.
    day_text = re.sub(r"\bein(?:e|en)?\b", "1", text)
    for number, word in enumerate(("zwei", "drei", "vier", "fuenf"), start=2):
        day_text = re.sub(rf"\b{word}\b", str(number), day_text)
    patterns = [
        (
            r"(?:bis zu|up to)?\s*(100|[1-9]\d)\s*%\s*"
            r"(?:remote|homeoffice|home office|mobiles arbeiten|hybrid)"
        ),
        (
            r"(?:remote|homeoffice|home office|mobiles arbeiten|hybrid)"
            r"[^\d%]{0,40}(100|[1-9]\d)\s*%"
        ),
    ]
    matches = []
    for pattern in patterns:
        matches.extend(int(match) for match in re.findall(pattern, text))

    remote_day_patterns = [
        r"(?:bis zu\s+)?([1-5])\s+(?:tage?n?\s+)?(?:pro|je)\s+woche\s+(?:im\s+)?(?:homeoffice|home office|remote|mobil)",
        r"(?:homeoffice|home office|remote|mobiles arbeiten)[^.!?\n]{0,35}(?:bis zu\s+)?([1-5])\s+tage?n?(?:\s+(?:pro|je)\s+woche)?",
        r"(?:bis zu\s+)?([1-5])\s+tage?n?(?:\s+(?:pro|je)\s+woche)?\s+(?:im\s+)?(?:homeoffice|home office|remote|mobil)",
    ]
    for pattern in remote_day_patterns:
        matches.extend(int(days) * 20 for days in re.findall(pattern, day_text))

    presence_patterns = [
        r"([1-5])\s+praesenztage?n?(?:\s+(?:pro|je)\s+woche)?",
        r"([1-5])\s+tage?n?(?:\s+(?:pro|je)\s+woche)?\s+(?:vor ort|im buero|im office)",
    ]
    for pattern in presence_patterns:
        matches.extend((5 - int(days)) * 20 for days in re.findall(pattern, day_text))

    return max(matches, default=0)


def classify_remote(remote):
    """Convert detected remote text into structured model fields."""
    normalized = normalize_text(remote)
    match = re.fullmatch(r"(100|[1-9]?\d)%", normalized)
    if match:
        percentage = int(match.group(1))
        if percentage == 100:
            return WorkMode.REMOTE, percentage
        if percentage > 0:
            return WorkMode.HYBRID, percentage
        return WorkMode.ONSITE, 0

    if normalized == "homeoffice":
        return WorkMode.HYBRID, None
    return WorkMode.UNKNOWN, None


def contains_any_substring(text, words):
    """Return whether text contains any phrase, also inside longer words.

    Unlike matching_text.contains_any, plain words need no word boundaries.
    """
    return any(word in text for word in words)
