"""Shared text helpers for matching and source adapters."""

from html.parser import HTMLParser


class TextExtractor(HTMLParser):
    """Small HTML-to-text parser for schema.org description fragments."""

    def __init__(self):
        super().__init__()
        self.parts = []

    def handle_data(self, data):
        text = data.strip()
        if text:
            self.parts.append(text)

    def text(self):
        return " ".join(self.parts)


def normalize_text(text):
    """Lowercase text and make German umlauts searchable with ASCII keywords."""
    replacements = {
        "\u00e4": "ae",
        "\u00f6": "oe",
        "\u00fc": "ue",
        "\u00df": "ss",
    }
    normalized = str(text or "").lower()
    for old, new in replacements.items():
        normalized = normalized.replace(old, new)
    return normalized


def html_to_text(html):
    parser = TextExtractor()
    parser.feed(str(html or ""))
    return parser.text()
