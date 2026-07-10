"""Small HTTP helpers used by source adapters."""

import json
from urllib.request import Request, urlopen

DEFAULT_HEADERS = {
    "User-Agent": "job-agent/0.1",
}


def fetch_text(url, headers=None, timeout=20):
    request = Request(url, headers=build_headers(headers))
    with urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8")


def fetch_json(url, headers=None, timeout=20):
    return json.loads(fetch_text(url, headers=headers, timeout=timeout))


def build_headers(headers=None):
    merged = dict(DEFAULT_HEADERS)
    merged.update(headers or {})
    return merged
