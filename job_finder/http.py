"""Small HTTP helpers used by source adapters."""

import json
from urllib.request import HTTPRedirectHandler, Request, build_opener, urlopen

DEFAULT_HEADERS = {
    "User-Agent": "job-finder/0.1",
}
MAX_RESPONSE_BYTES = 20 * 1024 * 1024


def fetch_text(url, headers=None, timeout=20, max_bytes=MAX_RESPONSE_BYTES):
    """Fetch a URL and decode its response as UTF-8 text."""
    request = Request(url, headers=_build_headers(headers))
    with urlopen(request, timeout=timeout) as response:
        return _read_bounded(response, max_bytes).decode("utf-8")


def fetch_json(url, headers=None, timeout=20):
    """Fetch a URL and parse its UTF-8 response as JSON."""
    return json.loads(fetch_text(url, headers=headers, timeout=timeout))


def fetch_text_with_final_url(
    url,
    headers=None,
    timeout=20,
    *,
    url_validator=None,
    max_bytes=MAX_RESPONSE_BYTES,
):
    """Fetch text while optionally validating every redirect destination."""
    if url_validator is not None:
        url = url_validator(url)
    request = Request(url, headers=_build_headers(headers))
    opener = build_opener(ValidatingRedirectHandler(url_validator))
    with opener.open(request, timeout=timeout) as response:
        final_url = response.url
        if url_validator is not None:
            final_url = url_validator(final_url)
        return final_url, _read_bounded(response, max_bytes).decode("utf-8")


class ValidatingRedirectHandler(HTTPRedirectHandler):
    """Reject a redirect before urllib connects to an untrusted destination."""

    def __init__(self, validator):
        super().__init__()
        self.validator = validator

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if self.validator is not None:
            newurl = self.validator(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _read_bounded(response, max_bytes):
    """Read one response with a hard limit to protect local memory."""
    content_length = response.headers.get("Content-Length")
    if content_length and int(content_length) > max_bytes:
        raise ValueError("HTTP-Antwort überschreitet das Größenlimit")
    content = response.read(max_bytes + 1)
    if len(content) > max_bytes:
        raise ValueError("HTTP-Antwort überschreitet das Größenlimit")
    return content


def _build_headers(headers=None):
    """Merge optional request headers with the Job Finder defaults."""
    merged = dict(DEFAULT_HEADERS)
    merged.update(headers or {})
    return merged
