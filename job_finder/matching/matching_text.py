"""Normalized keyword matching shared by scoring analyses."""

import re

from job_finder.matching.matching_rules import (
    BODY_ENTRY_LEVEL_PHRASES,
    ENTRY_LEVEL_WORDS,
)
from job_finder.text import normalize_text


def contains_any(text, words):
    """Match any keyword against text already processed by normalize_text."""
    return any(contains_keyword(text, word) for word in words)


def is_entry_level(title, description=""):
    """Return whether this specific vacancy explicitly welcomes beginners."""
    return contains_any(title, ENTRY_LEVEL_WORDS) or contains_any(
        description,
        BODY_ENTRY_LEVEL_PHRASES,
    )


def contains_keyword(text, keyword):
    """Match a normalized keyword, using word boundaries for plain words."""
    return re.search(keyword_pattern(keyword), text) is not None


def keyword_pattern(keyword):
    """Escape a keyword and add boundaries for letters, digits or underscores."""
    normalized = normalize_text(keyword)
    escaped = re.escape(normalized)
    if re.fullmatch(r"[a-z0-9_]+", normalized):
        return rf"(?<!\w){escaped}(?!\w)"
    return escaped


def matches_pattern(text, pattern):
    """Require every keyword in a role pattern to match normalized text."""
    return all(contains_keyword(text, part) for part in pattern)
