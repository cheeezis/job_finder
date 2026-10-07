"""Shared HTTP client for source adapters: timeouts, bounded responses and fair retries.

Every source request goes through one HTTPX client, one request per host at a time. A request that fails briefly
(timeout, lost connection, HTTP 5xx) is retried at most twice with a growing pause.
HTTP 429 is retried only when the server names a short wait in Retry-After; 403 and
all other client errors are never retried, so the job finder does not work around a
block.
"""

import json
import threading
import time
from urllib.parse import urljoin

import httpx

DEFAULT_HEADERS = {"User-Agent": "job-finder/0.1"}
MAX_RESPONSE_BYTES = 20 * 1024 * 1024
MAX_REDIRECTS = 10
RETRY_DELAYS_SECONDS = (2, 4)
RETRY_STATUS_CODES = {500, 502, 503, 504}
MAX_RETRY_AFTER_SECONDS = 60

_client = None
_client_lock = threading.Lock()
# One lock per host: parallel sources never send two requests to the same host at once.
_host_locks = {}
_host_locks_lock = threading.Lock()


class HttpStatusError(OSError):
    """An HTTP error response; code and url name the status and the answering URL."""

    def __init__(self, code, url, retry_after=None):
        super().__init__(f"HTTP {code}")
        self.code = code
        self.url = url
        self.retry_after = retry_after


def client():
    """Return the process-wide client; it pools connections and is safe across threads."""
    global _client
    with _client_lock:
        if _client is None:
            _client = httpx.Client(headers=DEFAULT_HEADERS, timeout=httpx.Timeout(20, connect=10))
        return _client


def host_slot(url):
    """Return the lock that admits one request at a time to the URL's host."""
    host = httpx.URL(url).host
    with _host_locks_lock:
        return _host_locks.setdefault(host, threading.Lock())


def session():
    """Return a fresh client with its own cookies, for sources that keep a search session."""
    return httpx.Client(headers=DEFAULT_HEADERS, timeout=httpx.Timeout(20, connect=10), follow_redirects=True)


def response_text(response):
    """Return a session response's UTF-8 text, or raise HttpStatusError for an error status."""
    if response.status_code >= 400:
        raise HttpStatusError(response.status_code, str(response.url))
    return response.content.decode("utf-8")


def fetch_text(url, headers=None, timeout=20):
    """Fetch a URL and decode its response as UTF-8 text."""
    return fetch_text_with_final_url(url, headers, timeout)[1]


def fetch_json(url, headers=None):
    """Fetch a URL and parse its UTF-8 response as JSON."""
    return json.loads(fetch_text(url, headers=headers))


def fetch_text_with_final_url(
    url, headers=None, timeout=20, *, url_validator=None, max_bytes=MAX_RESPONSE_BYTES, retries=None
):
    """Fetch text, validating every redirect destination, and retry brief failures.

    retries limits the extra attempts (default: one per entry in RETRY_DELAYS_SECONDS).
    """
    delays = RETRY_DELAYS_SECONDS[: len(RETRY_DELAYS_SECONDS) if retries is None else retries]
    for attempt in range(len(delays) + 1):
        try:
            return _fetch_once(url, headers, timeout, url_validator, max_bytes)
        except (HttpStatusError, TimeoutError, ConnectionError) as error:
            pause = _retry_pause(error, delays, attempt)
            if pause is None:
                raise
            time.sleep(pause)
    raise AssertionError("unreachable")


def _retry_pause(error, delays, attempt):
    """Return how long to wait before the next attempt, or None when the error is final."""
    if attempt >= len(delays):
        return None
    if not isinstance(error, HttpStatusError) or error.code in RETRY_STATUS_CODES:
        return delays[attempt]
    if error.code == 429 and error.retry_after is not None and error.retry_after <= MAX_RETRY_AFTER_SECONDS:
        return error.retry_after
    return None


def _fetch_once(url, headers, timeout, url_validator, max_bytes):
    """Make one request, following redirects only after url_validator accepted them."""
    if url_validator is not None:
        url = url_validator(url)
    request_headers = {**DEFAULT_HEADERS, **(headers or {})}
    try:
        for _redirect in range(MAX_REDIRECTS + 1):
            with host_slot(url), client().stream("GET", url, headers=request_headers, timeout=timeout) as response:
                if response.is_redirect:
                    url = urljoin(str(response.url), response.headers["Location"])
                    if url_validator is not None:
                        url = url_validator(url)
                    continue
                final_url = str(response.url)
                if response.status_code >= 400:
                    raise HttpStatusError(response.status_code, final_url, _retry_after(response))
                if url_validator is not None:
                    final_url = url_validator(final_url)
                return final_url, _read_bounded(response, max_bytes).decode("utf-8")
    except httpx.TimeoutException as error:
        raise TimeoutError(type(error).__name__) from error
    except httpx.TransportError as error:
        raise ConnectionError(type(error).__name__) from error
    raise ConnectionError("Zu viele Weiterleitungen")


def _retry_after(response):
    """Return Retry-After in whole seconds when the server gives a number."""
    value = response.headers.get("Retry-After", "").strip()
    return int(value) if value.isdigit() else None


def _read_bounded(response, max_bytes):
    """Read one response with a hard limit to protect local memory."""
    content_length = response.headers.get("Content-Length")
    if content_length and int(content_length) > max_bytes:
        raise ValueError("HTTP-Antwort überschreitet das Größenlimit")
    content = bytearray()
    for chunk in response.iter_bytes():
        content.extend(chunk)
        if len(content) > max_bytes:
            raise ValueError("HTTP-Antwort überschreitet das Größenlimit")
    return bytes(content)
