"""Helpers for extracting schema.org job data from HTML pages."""

import json
import re
from html import unescape

_JSON_LD_PATTERN = re.compile(
    r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.DOTALL | re.IGNORECASE,
)


def extract_script_json(html, script_id):
    """Parse the JSON state a page embeds in <script id=... type="application/json">."""
    match = re.search(
        rf'<script id="{re.escape(script_id)}" type="application/json">(.*?)</script>',
        html,
        re.DOTALL,
    )
    if not match:
        raise ValueError(f"{script_id} JSON nicht gefunden")
    return json.loads(unescape(match.group(1)))


def extract_json_ld_job_posting(html):
    """Return the first valid JobPosting from JSON-LD scripts, if present."""
    for script in _JSON_LD_PATTERN.findall(html):
        try:
            data = json.loads(script.strip())
        except json.JSONDecodeError:
            # Some publishers escape the entire JSON document. Decode only as
            # a fallback: &quot; inside a valid JSON string must not break it.
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

        children = data.values()
    elif isinstance(data, list):
        children = data
    else:
        return None

    for child in children:
        posting = find_job_posting(child)
        if posting:
            return posting

    return None
