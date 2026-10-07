"""HTTP helpers and the Discord client against a local test server."""

import json
import threading
import unittest
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

from job_finder.http import HttpStatusError, fetch_json, fetch_text, fetch_text_with_final_url
from job_finder.workflow.notifications import DiscordWebhookClient, NotificationError


class QuietServer(ThreadingHTTPServer):
    def handle_error(self, request, client_address):
        """Ignore clients that hang up early, as the timeout test does on purpose."""


@contextmanager
def local_server(routes):
    """Serve path -> (status, headers, body[, delay]), or a list answered in turn, and record every request."""
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.respond()

        def do_POST(self):
            self.respond()

        def respond(self):
            length = int(self.headers.get("Content-Length") or 0)
            requests.append(
                {
                    "method": self.command,
                    "path": self.path,
                    "headers": dict(self.headers),
                    "body": self.rfile.read(length),
                }
            )
            route = routes[self.path.split("?")[0]]
            status, headers, body, *delay = route.pop(0) if isinstance(route, list) else route
            if delay:
                # Not time.sleep: the retry tests patch it to skip their pauses.
                threading.Event().wait(delay[0])
            self.send_response(status)
            for key, value in headers.items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            """Keep test output free of access logs."""

    server = QuietServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", requests
    finally:
        server.shutdown()
        server.server_close()


def rejecting(blocked):
    """Return a URL validator that refuses one path and records every URL it saw."""
    seen = []

    def validate(url):
        seen.append(url)
        if blocked in url:
            raise ValueError("Ziel nicht erlaubt")
        return url

    validate.seen = seen
    return validate


class HttpHelperTests(unittest.TestCase):
    def test_text_and_json_use_default_and_extra_headers(self):
        routes = {
            "/text": (200, {}, "Grüße".encode()),
            "/json": (200, {"Content-Type": "application/json"}, b'{"jobs": [1, 2]}'),
        }
        with local_server(routes) as (base, requests):
            text = fetch_text(f"{base}/text", headers={"Accept": "text/html"})
            data = fetch_json(f"{base}/json")
            agent = fetch_text(f"{base}/text", headers={"User-Agent": "custom/1"})

        self.assertEqual(text, "Grüße")
        self.assertEqual(data, {"jobs": [1, 2]})
        self.assertEqual(agent, "Grüße")
        self.assertEqual(requests[0]["headers"]["User-Agent"], "job-finder/0.1")
        self.assertEqual(requests[0]["headers"]["Accept"], "text/html")
        self.assertEqual(requests[1]["headers"]["User-Agent"], "job-finder/0.1")
        self.assertEqual(requests[2]["headers"]["User-Agent"], "custom/1")

    def test_announced_size_above_the_default_limit_is_rejected(self):
        routes = {"/huge": (200, {"Content-Length": str(30 * 1024 * 1024)}, b"x")}
        with local_server(routes) as (base, _requests), self.assertRaisesRegex(ValueError, "Größenlimit"):
            fetch_text(f"{base}/huge")

    def test_size_limit_checks_header_and_actual_body(self):
        routes = {
            "/announced": (200, {"Content-Length": "11"}, b"x" * 11),
            "/unannounced": (200, {}, b"x" * 11),
            "/exact": (200, {}, b"x" * 10),
        }
        with local_server(routes) as (base, _requests):
            for path in ("/announced", "/unannounced"):
                with self.subTest(path), self.assertRaisesRegex(ValueError, "Größenlimit"):
                    fetch_text_with_final_url(f"{base}{path}", max_bytes=10)
            self.assertEqual(fetch_text_with_final_url(f"{base}/exact", max_bytes=10), (f"{base}/exact", "x" * 10))

    def test_redirects_are_validated_and_report_the_final_url(self):
        routes = {
            "/start": (302, {"Location": "/final"}, b""),
            "/final": (200, {}, b"ok"),
            "/to-blocked": (302, {"Location": "/blocked"}, b""),
            "/blocked": (200, {}, b"secret"),
        }
        with local_server(routes) as (base, requests):
            validator = rejecting("/blocked")
            final = fetch_text_with_final_url(f"{base}/start", url_validator=validator)
            plain = fetch_text_with_final_url(f"{base}/start")
            with self.assertRaisesRegex(ValueError, "nicht erlaubt"):
                fetch_text_with_final_url(f"{base}/to-blocked", url_validator=validator)

        self.assertEqual(final, (f"{base}/final", "ok"))
        self.assertEqual(plain, (f"{base}/final", "ok"))
        self.assertEqual(validator.seen[:3], [f"{base}/start", f"{base}/final", f"{base}/final"])
        self.assertNotIn("/blocked", [request["path"] for request in requests])

    def test_http_errors_propagate_with_their_status(self):
        with (
            local_server({"/missing": (404, {}, b"")}) as (base, requests),
            self.assertRaises(HttpStatusError) as caught,
        ):
            fetch_text(f"{base}/missing")
        self.assertEqual(caught.exception.code, 404)
        self.assertEqual(caught.exception.url, f"{base}/missing")
        self.assertEqual(len(requests), 1)


@patch("job_finder.http.time.sleep")
class RetryTests(unittest.TestCase):
    def test_server_errors_are_retried_twice_with_growing_pauses(self, sleep):
        routes = {"/flaky": [(503, {}, b""), (502, {}, b""), (200, {}, b"ok")]}
        with local_server(routes) as (base, requests):
            self.assertEqual(fetch_text(f"{base}/flaky"), "ok")
        self.assertEqual(len(requests), 3)
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [2, 4])

    def test_a_third_server_error_is_final(self, sleep):
        routes = {"/down": [(500, {}, b"")] * 3}
        with local_server(routes) as (base, requests), self.assertRaises(HttpStatusError) as caught:
            fetch_text(f"{base}/down")
        self.assertEqual(caught.exception.code, 500)
        self.assertEqual(len(requests), 3)

    def test_rate_limit_waits_as_long_as_the_server_asks(self, sleep):
        routes = {"/limited": [(429, {"Retry-After": "7"}, b""), (200, {}, b"ok")]}
        with local_server(routes) as (base, requests):
            self.assertEqual(fetch_text(f"{base}/limited"), "ok")
        self.assertEqual(len(requests), 2)
        sleep.assert_called_once_with(7)

    def test_blocks_and_open_ended_rate_limits_are_not_retried(self, sleep):
        routes = {
            "/forbidden": (403, {}, b""),
            "/limited": (429, {}, b""),
            "/long": (429, {"Retry-After": "3600"}, b""),
        }
        with local_server(routes) as (base, requests):
            for path, code in (("/forbidden", 403), ("/limited", 429), ("/long", 429)):
                with self.subTest(path), self.assertRaises(HttpStatusError) as caught:
                    fetch_text(f"{base}{path}")
                self.assertEqual(caught.exception.code, code)
        self.assertEqual(len(requests), 3)
        sleep.assert_not_called()

    def test_timeouts_are_retried_and_then_raised_as_timeout_error(self, sleep):
        routes = {"/slow": (200, {}, b"late", 1.0)}
        with local_server(routes) as (base, requests), self.assertRaises(TimeoutError):
            fetch_text(f"{base}/slow", timeout=0.1)
        self.assertEqual(len(requests), 3)

    def test_retries_can_be_switched_off(self, sleep):
        with local_server({"/down": (503, {}, b"")}) as (base, requests), self.assertRaises(HttpStatusError):
            fetch_text_with_final_url(f"{base}/down", retries=0)
        self.assertEqual(len(requests), 1)
        sleep.assert_not_called()


class DiscordWebhookClientTests(unittest.TestCase):
    def test_successful_delivery_posts_utf8_json_and_waits_for_confirmation(self):
        payload = {"content": "Präsenz prüfen", "embeds": []}
        for status in (200, 204):
            with self.subTest(status=status):
                with local_server({"/webhook": (status, {}, b"")}) as (base, requests):
                    DiscordWebhookClient(f"{base}/webhook?thread_id=7").send(payload)

                request = requests[0]
                self.assertEqual(request["method"], "POST")
                self.assertEqual(request["path"], "/webhook?thread_id=7&wait=true")
                self.assertEqual(request["headers"]["Content-Type"], "application/json")
                self.assertEqual(request["headers"]["User-Agent"], "job-finder/1.0")
                self.assertEqual(request["body"], json.dumps(payload, ensure_ascii=False).encode())

    def test_unexpected_status_and_http_errors_become_notification_errors(self):
        for status in (202, 500):
            with (
                self.subTest(status=status),
                local_server({"/webhook": (status, {}, b"")}) as (base, _requests),
                self.assertRaisesRegex(NotificationError, f"HTTP {status}"),
            ):
                DiscordWebhookClient(f"{base}/webhook").send({"content": "x"})

    def test_timeout_and_unreachable_server_become_notification_errors(self):
        with local_server({"/slow": (200, {}, b"", 1.0)}) as (base, _requests):
            slow = DiscordWebhookClient(f"{base}/slow", timeout=0.1)
            with self.assertRaisesRegex(NotificationError, "nicht erreichbar"):
                slow.send({"content": "x"})
        with self.assertRaisesRegex(NotificationError, "nicht erreichbar"):
            DiscordWebhookClient(f"{base}/closed", timeout=1).send({"content": "x"})
