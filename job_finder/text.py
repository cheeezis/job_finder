"""Shared text helpers for matching and source adapters."""

import re
from html.parser import HTMLParser

_SEARCH_TRANSLATION = str.maketrans(
    {"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss", "\u00ad": None, "\u200b": None}
)


class _TextExtractor(HTMLParser):
    """Small HTML-to-text parser for schema.org description fragments."""

    def __init__(self):
        super().__init__()
        self.parts = []

    def handle_data(self, data):
        text = data.strip()
        if text:
            self.parts.append(text)


def normalize_text(text):
    """Lowercase text and make German umlauts searchable with ASCII keywords."""
    # Career pages sometimes insert invisible soft hyphens for line wrapping.
    # They must not split searchable words such as "Auszubildende".
    return str(text or "").lower().translate(_SEARCH_TRANSLATION)


def html_to_text(html):
    """Convert an HTML fragment to compact plain text."""
    parser = _TextExtractor()
    parser.feed(str(html or ""))
    return " ".join(parser.parts)


def compact_text(value):
    """Collapse arbitrary whitespace into single spaces."""
    return " ".join(str(value or "").split())


def text_is_mainly_english(value):
    """Recognize clearly English job text without treating isolated words as proof."""
    text = normalize_text(value)
    german_words = ["und", "wir", "du", "sie", "deine", "ihre", "aufgaben", "kenntnisse"]
    english_words = ["and", "we", "you", "your", "responsibilities", "requirements", "experience"]

    def count(words):
        return sum(len(re.findall(rf"(?<!\w){re.escape(word)}(?!\w)", text)) for word in words)

    german_count = count(german_words)
    english_count = count(english_words)
    return english_count >= 5 and english_count > german_count * 2
