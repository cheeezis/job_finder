"""Central remote-work detection shared by all source adapters."""

import re

from job_finder.models import WorkMode
from job_finder.text import normalize_text

REMOTE_WORDS = [
    "remote",
    "homeoffice",
    "home office",
    "mobiles arbeiten",
    "hybrid",
    "telearbeit",
]

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

    if contains_any(text, FULL_REMOTE_PHRASES):
        return "100%"

    if contains_any(text, NO_REMOTE_PHRASES):
        return "0%"

    if structured == "homeoffice" or contains_any(text, REMOTE_WORDS):
        return "homeoffice"

    return "0%"


def assess_remote(title, description, location, structured, *, broad_portal_flag=False):
    """Resolve explicit limits before portal flags; expose uncertain remote scope."""
    text = normalize_text(f"{title}\n{description}")
    percentages = remote_percentages(text)
    warning = ""
    if percentages:
        remote = f"{min(percentages)}%"
        if structured not in {"0%", remote} and re.fullmatch(r"\d+%", structured):
            warning = (
                "Remote-Angaben widersprechen sich; konkreter Textumfang verwendet."
            )
    elif contains_any(text, NO_REMOTE_PHRASES):
        remote = "0%"
        if structured not in {"0%", ""}:
            warning = "Portal meldet Remote, Anzeigentext schließt Homeoffice aus."
    elif contains_any(text, FULL_REMOTE_PHRASES + ["remote-first", "remote first"]):
        remote = "100%"
    elif structured == "100%" and (
        broad_portal_flag
        or contains_any(text, ["hybrid", "home-office-option", "homeoffice-option"])
    ):
        remote = "homeoffice"
        warning = "Remote-Umfang unklar; Portalangabe bestätigt keine 100 % Remote."
    elif structured != "0%":
        remote = structured
    else:
        remote = detect_remote(
            title, location, description, structured_remote=structured
        )
    return remote, warning


def extract_remote_percent(text):
    """Find the highest percentage that is clearly connected to remote work."""
    return max(remote_percentages(text), default=0)


def remote_percentages(text):
    """Collect concrete percentages, including explicit zero and weekly days."""
    day_text = text
    for word, number in {
        "ein": "1",
        "eine": "1",
        "einen": "1",
        "zwei": "2",
        "drei": "3",
        "vier": "4",
        "fuenf": "5",
    }.items():
        day_text = re.sub(rf"\b{word}\b", number, day_text)
    patterns = [
        (
            r"(?:bis zu|up to)?\s*\b(100|[1-9]?\d)\s*%\s*"
            r"(?:remote|homeoffice|home[- ]office|mobiles arbeiten|hybrid)"
        ),
        (
            r"(?:remote|homeoffice|home[- ]office|mobiles arbeiten|hybrid)"
            r"[^\d%.!?\n]{0,25}\b(100|[1-9]?\d)\s*%"
        ),
    ]
    matches = []
    for pattern in patterns:
        matches.extend(int(match) for match in re.findall(pattern, text))

    remote_day_patterns = [
        r"(?:bis zu\s+)?([1-5])\s+(?:tage?n?\s+)?(?:pro|je)\s+woche\s+(?:im\s+)?(?:homeoffice|home office|remote|mobil)",
        r"(?:homeoffice|home office|remote|mobiles arbeiten)\s*[:(-]?\s*(?:bis zu\s+)?([1-5])\s+tage?n?(?:\s+(?:pro|je)\s+woche)?",
        r"(?:bis zu\s+)?([1-5])\s+tage?n?(?:\s+(?:pro|je)\s+woche)?\s+(?:im\s+)?(?:homeoffice|home office|remote|mobil)",
    ]
    for pattern in remote_day_patterns:
        matches.extend(int(days) * 20 for days in re.findall(pattern, day_text))

    presence_patterns = [
        r"([1-5])\s+praesenztage?n?\s+(?:pro|je)\s+woche",
        r"([1-5])\s+tage?n?\s+(?:pro|je)\s+woche\s+(?:vor ort|im buero|im office)",
        r"([1-5])\s+days?\s+(?:a|per)\s+week\s+(?:onsite|on-site|in (?:the )?office)",
    ]
    for pattern in presence_patterns:
        matches.extend((5 - int(days)) * 20 for days in re.findall(pattern, day_text))

    return matches


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


def contains_any(text, words):
    """Return whether text contains any configured phrase."""
    return any(word in text for word in words)
