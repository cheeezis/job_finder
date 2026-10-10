"""Source downloads keep credentials and private targets outside redirect boundaries."""

import socket
import unittest
from contextlib import contextmanager, nullcontext
from unittest.mock import patch

import httpx

from job_finder import http
from job_finder.sources import jumo, manual

PUBLIC_ADDRESS = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]


@contextmanager
def transport(handler):
    """Fake both DNS and HTTP; these tests never contact a real source."""
    with (
        httpx.Client(transport=httpx.MockTransport(handler)) as client,
        patch.object(http, "client", return_value=client),
        patch("socket.getaddrinfo", return_value=PUBLIC_ADDRESS),
        patch.object(http.time, "sleep"),
    ):
        yield client


class RedirectCredentialsTests(unittest.TestCase):
    def test_same_origin_preserves_explicit_credentials(self):
        requests = []

        def respond(request):
            requests.append(request)
            if request.url.path == "/start":
                return httpx.Response(302, headers={"Location": "https://source.example:443/final"})
            return httpx.Response(200, text="ok")

        with transport(respond):
            self.assertEqual(
                http.fetch_text("https://source.example/start", headers={"Authorization": "test-only"}), "ok"
            )
        self.assertEqual(requests[1].headers["authorization"], "test-only")

    def test_origin_change_strips_all_sensitive_headers_and_does_not_restore_them(self):
        for start, destination in (
            ("https://source.example/start", "https://other.example/final"),
            ("https://source.example/start", "https://source.example:80/final"),
            ("http://source.example/start", "https://source.example/final"),
        ):
            requests = []

            def respond(request):
                requests.append(request)
                if len(requests) == 1:
                    return httpx.Response(302, headers={"Location": destination})
                if len(requests) == 2:
                    return httpx.Response(302, headers={"Location": "https://source.example/back"})
                return httpx.Response(200, text="ok")

            with self.subTest(destination=destination), transport(respond):
                http.fetch_text(
                    start,
                    headers={
                        "aUtHoRiZaTiOn": "test-only",
                        "PROXY-AUTHORIZATION": "test-only",
                        "cookie": "test=only",
                        "Accept": "text/html",
                    },
                )
            for request in requests[1:]:
                self.assertFalse({"authorization", "proxy-authorization", "cookie"}.intersection(request.headers))
                self.assertEqual(request.headers["accept"], "text/html")

    def test_authenticated_https_downgrade_is_rejected_before_a_request(self):
        for header in ("Authorization", "Proxy-Authorization", "Cookie"):
            requests = []

            def respond(request):
                requests.append(request)
                return httpx.Response(302, headers={"Location": "http://source.example/final"})

            with self.subTest(header=header), transport(respond), self.assertRaises(ValueError):
                http.fetch_text("https://source.example/start", headers={header: "test-only"})
            self.assertEqual(len(requests), 1)

    def test_public_downgrade_still_works(self):
        requests = []

        def respond(request):
            requests.append(request)
            return (
                httpx.Response(302, headers={"Location": "http://source.example/final"})
                if len(requests) == 1
                else httpx.Response(200, text="ok")
            )

        with transport(respond):
            self.assertEqual(http.fetch_text("https://source.example/start"), "ok")
        self.assertEqual(len(requests), 2)

    def test_retry_starts_with_credentials_but_strips_them_at_the_next_origin(self):
        requests = []

        def respond(request):
            requests.append(request)
            if request.url.host == "source.example":
                return httpx.Response(302, headers={"Location": "https://other.example/final"})
            return httpx.Response(503 if len(requests) == 2 else 200, text="ok")

        with transport(respond):
            self.assertEqual(
                http.fetch_text("https://source.example/start", headers={"Authorization": "test-only"}), "ok"
            )
        self.assertEqual(
            [request.headers.get("authorization") for request in requests], ["test-only", None, "test-only", None]
        )


class PublicTargetTests(unittest.TestCase):
    def test_default_policy_rejects_private_start_and_redirect_targets(self):
        for target in (
            "http://127.0.0.1/private",
            "http://[::1]/private",
            "http://localhost/private",
            "http://169.254.169.254/private",
        ):
            for redirect in (False, True):
                requests = []

                def respond(request):
                    requests.append(request)
                    return (
                        httpx.Response(302, headers={"Location": target})
                        if len(requests) == 1
                        else httpx.Response(200, text="private")
                    )

                with self.subTest(target=target, redirect=redirect), transport(respond), self.assertRaises(ValueError):
                    http.fetch_text_with_final_url("https://source.example/apply" if redirect else target)
                self.assertEqual(len(requests), int(redirect))

    def test_dns_with_any_private_address_is_rejected(self):
        addresses = [*PUBLIC_ADDRESS, (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("::1", 443, 0, 0))]
        with (
            patch("socket.getaddrinfo", return_value=addresses),
            patch.object(http, "client") as client,
            self.assertRaises(ValueError),
        ):
            http.fetch_text("https://source.example/job")
        client.assert_not_called()

    def test_invalid_scheme_userinfo_and_ports_are_rejected(self):
        for url in (
            "file:///private",
            "https://user:pass@source.example/job",
            "https://source.example:8080/job",
            "https://source.example:bad/job",
        ):
            with (
                self.subTest(url=url),
                patch("socket.getaddrinfo", return_value=PUBLIC_ADDRESS),
                self.assertRaises(ValueError),
            ):
                manual.validate_public_url(url)

    def test_public_query_is_preserved_and_fragment_removed(self):
        with patch("socket.getaddrinfo", return_value=PUBLIC_ADDRESS):
            self.assertEqual(
                manual.validate_public_url("https://source.example/job?id=one#details"),
                "https://source.example/job?id=one",
            )


class SessionTests(unittest.TestCase):
    def test_session_cookie_cannot_follow_an_https_downgrade(self):
        requests = []

        def respond(request):
            requests.append(request)
            return httpx.Response(302, headers={"Location": "http://source.example/search"})

        with transport(respond) as client, self.assertRaises(ValueError):
            client.cookies.set("session", "test-only", domain="source.example")
            http.session_text(client, "https://source.example/search")
        self.assertEqual(len(requests), 1)

    def test_session_stops_streaming_as_soon_as_the_body_limit_is_exceeded(self):
        class Chunks(httpx.SyncByteStream):
            consumed = 0
            closed = False

            def __iter__(self):
                for _index in range(3):
                    self.consumed += 1
                    yield b"x" * 6

            def close(self):
                self.closed = True

        stream = Chunks()
        with transport(lambda request: httpx.Response(200, stream=stream)) as client, self.assertRaises(ValueError):
            http.session_text(client, "https://source.example/search", {"page": "next"}, max_bytes=10)
        self.assertEqual(stream.consumed, 2)
        self.assertTrue(stream.closed)

    def test_jumo_cookie_and_csrf_handshake_still_collects_detail_links(self):
        requests = []

        def respond(request):
            requests.append(request)
            if request.method == "GET":
                return httpx.Response(
                    200,
                    headers={"Set-Cookie": "session=test-only; Path=/"},
                    text='<input name="_csrf" value="test-only">',
                )
            self.assertIn("session=test-only", request.headers["cookie"])
            self.assertIn(b"_csrf=test-only", request.content)
            if b"hasNextJobOffers" in request.content:
                return httpx.Response(200, text="false")
            return httpx.Response(200, text="jobOfferId=abc123")

        with transport(respond) as client, patch.object(jumo, "session", return_value=nullcontext(client)):
            links = jumo.collect_links()
        self.assertEqual(len(links), 1)
        self.assertIn("jobOfferId=abc123", links[0])
        self.assertEqual([request.method for request in requests], ["GET", "POST", "POST", "POST"])

    def test_session_size_limit_covers_get_and_post_and_closes_response(self):
        for form in (None, {"page": "next"}):
            for headers in ({"Content-Length": "11"}, {}):
                responses = []

                def respond(request):
                    response = httpx.Response(200, headers=headers, content=b"x" * 11)
                    responses.append(response)
                    return response

                with (
                    self.subTest(form=form, headers=headers),
                    transport(respond) as client,
                    self.assertRaisesRegex(ValueError, "Größenlimit"),
                ):
                    http.session_text(client, "https://source.example/search", form, max_bytes=10)
                self.assertTrue(responses[0].is_closed)

    def test_session_redirect_to_private_target_is_not_followed(self):
        requests = []

        def respond(request):
            requests.append(request)
            return httpx.Response(302, headers={"Location": "http://127.0.0.1/private"})

        with transport(respond) as client, self.assertRaises(ValueError):
            http.session_text(client, "https://source.example/search")
        self.assertEqual(len(requests), 1)

    def test_session_maps_timeouts_without_replaying_post(self):
        requests = []

        def respond(request):
            requests.append(request)
            raise httpx.ReadTimeout("test-only", request=request)

        with transport(respond) as client, self.assertRaises(TimeoutError):
            http.session_text(client, "https://source.example/search", {"page": "next"})
        self.assertEqual(len(requests), 1)

    def test_session_http_errors_are_not_retried(self):
        for status in (403, 429, 503):
            requests = []

            def respond(request):
                requests.append(request)
                return httpx.Response(status, headers={"Retry-After": "1"})

            with (
                self.subTest(status=status),
                transport(respond) as client,
                self.assertRaises(http.HttpStatusError) as caught,
            ):
                http.session_text(client, "https://source.example/search", {"page": "next"})
            self.assertEqual(caught.exception.code, status)
            self.assertEqual(len(requests), 1)

    def test_redirect_cannot_replay_a_post(self):
        requests = []

        def respond(request):
            requests.append(request)
            return httpx.Response(307, headers={"Location": "/again"})

        with transport(respond) as client, self.assertRaises(ValueError):
            http.session_text(client, "https://source.example/search", {"page": "next"})
        self.assertEqual(len(requests), 1)

    def test_post_303_redirect_changes_to_get(self):
        requests = []

        def respond(request):
            requests.append(request)
            return (
                httpx.Response(303, headers={"Location": "/done"})
                if len(requests) == 1
                else httpx.Response(200, text="ok")
            )

        with transport(respond) as client:
            self.assertEqual(http.session_text(client, "https://source.example/search", {"page": "next"}), "ok")
        self.assertEqual([request.method for request in requests], ["POST", "GET"])
        self.assertEqual(requests[1].content, b"")
