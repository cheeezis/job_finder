"""Small HTTP helpers used by source adapters."""

import json
from urllib.request import Request, urlopen

DEFAULT_HEADERS = {
    "User-Agent": "job-finder/0.1",
}


def fetch_text(url, headers=None, timeout=20):
    """Fetch a URL and decode its response as UTF-8 text."""
    request = Request(url, headers=_build_headers(headers))
    with urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8")


def fetch_json(url, headers=None, timeout=20):
    """Fetch a URL and parse its UTF-8 response as JSON."""
    return json.loads(fetch_text(url, headers=headers, timeout=timeout))


def fetch_text_with_final_url(url, headers=None, timeout=20):
    """Fetch a URL and return both its redirected final URL and HTML text."""
    request = Request(url, headers=_build_headers(headers))
    with urlopen(request, timeout=timeout) as response:
        return response.url, response.read().decode("utf-8")


def _build_headers(headers=None):
    """Merge optional request headers with the Job Finder defaults."""
    merged = dict(DEFAULT_HEADERS)
    merged.update(headers or {})
    return merged
