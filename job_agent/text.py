"""Shared text helpers for matching and source adapters."""

from html.parser import HTMLParser


class _TextExtractor(HTMLParser):
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
    # Career pages sometimes insert invisible soft hyphens for line wrapping.
    # They must not split searchable words such as "Auszubildende".
    normalized = normalized.replace("\u00ad", "").replace("\u200b", "")
    for old, new in replacements.items():
        normalized = normalized.replace(old, new)
    return normalized


def html_to_text(html):
    """Convert an HTML fragment to compact plain text."""
    parser = _TextExtractor()
    parser.feed(str(html or ""))
    return parser.text()
