"""Helpers for extracting schema.org job data from HTML pages."""

import json
import re
from html import unescape


_JSON_LD_PATTERN = re.compile(
    r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.DOTALL | re.IGNORECASE,
)


def extract_json_ld_job_posting(html):
    """Return the first valid JobPosting from JSON-LD scripts, if present."""
    for script in _JSON_LD_PATTERN.findall(html):
        try:
            data = json.loads(unescape(script.strip()))
        except json.JSONDecodeError:
            continue

        posting = find_job_posting(data)
        if posting:
            return posting

    return None


def find_job_posting(data):
    """Recursively find a JobPosting in a JSON-compatible structure."""
    if isinstance(data, dict):
        if data.get("@type") == "JobPosting":
            return data

        for value in data.values():
            posting = find_job_posting(value)
            if posting:
                return posting

    if isinstance(data, list):
        for item in data:
            posting = find_job_posting(item)
            if posting:
                return posting

    return None
