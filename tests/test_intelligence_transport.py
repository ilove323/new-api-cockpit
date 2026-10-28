"""Real HTTP framing regressions; loopback fixtures only, no paid requests."""

from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import threading
import time
import unittest
from unittest.mock import patch

from new_api_cockpit import intelligence as intel

KEY = "fixture-not-a-real-provider-key"
RESULT = {
    "choices": [
        {
            "message": {"content": "<html><body><svg></svg></body></html>"},
            "finish_reason": "stop",
        }
    ]
}
BODY = json.dumps(RESULT).encode()


def chunked(body):
    split = len(body) // 2
    return (
        b"".join(
            f"{len(part):x}\r\n".encode() + part + b"\r\n"
            for part in (body[:split], body[split:])
        )
        + b"0\r\n\r\n"
    )


def respond(
    body,
    framing="length",
    *,
    close=True,
    status=200,
    declared=None,
    content_type="application/json",
):
    def reply(handler):
        handler.send_response(status)
        handler.send_header("Content-Type", content_type)
        if framing == "length":
            handler.send_header(
                "Content-Length", str(len(body) if declared is None else declared)
            )
        elif framing == "chunked":
            handler.send_header("Transfer-Encoding", "chunked")
        if close:
            handler.send_header("Connection", "close")
        handler.end_headers()
        handler.wfile.write(body)
        handler.wfile.flush()

    return reply


@contextmanager
def upstream(reply, protocol="HTTP/1.1"):
    attempts = []

    class Handler(BaseHTTPRequestHandler):
        protocol_version = protocol

        def do_GET(self):
            self.serve()

        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            self.serve()

        def serve(self):
            attempts.append((self.command, self.path))
            try:
                reply(self)
            except OSError:
                pass  # The client deliberately closes oversized/slow responses.

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(
        target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True
    )
    thread.start()
    try:
        with patch.dict(
            os.environ,
            {"NEW_API_INTERNAL_URL": f"http://127.0.0.1:{server.server_port}"},
        ):
            yield attempts
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def request(*, generation=True):
    return intel.upstream(
        "/v1/chat/completions" if generation else "/v1/models",
        KEY,
        1,
        body={"model": "fixture", "stream": False} if generation else None,
        generation=generation,
    )


class IntelligenceTransportTest(unittest.TestCase):
    def test_complete_response_with_connection_close_for_all_framings(self):
        for size in (0, 65537):
            body = BODY + b" " * size
            for framing in ("length", "chunked", "eof"):
                for generation in (False, True):
                    wire = chunked(body) if framing == "chunked" else body
                    with (
                        self.subTest(size=size, framing=framing, generation=generation),
                        upstream(respond(wire, framing)) as attempts,
                    ):
                        self.assertEqual(request(generation=generation), RESULT)
                        self.assertEqual(len(attempts), 1)

    def test_http10_implicit_close_and_http11_keepalive_still_succeed(self):
        for protocol in ("HTTP/1.0", "HTTP/1.1"):
            for framing in ("length", "chunked"):
                if protocol == "HTTP/1.0" and framing == "chunked":
                    continue
                wire = chunked(BODY) if framing == "chunked" else BODY
                with (
                    self.subTest(protocol=protocol, framing=framing),
                    upstream(respond(wire, framing, close=False), protocol) as attempts,
                ):
                    self.assertEqual(request(), RESULT)
                    self.assertEqual(len(attempts), 1)

    def test_incomplete_declared_length_is_not_accepted_even_with_valid_json(self):
        for generation, error in (
            (True, intel.GenerationError),
            (False, intel.Unavailable),
        ):
            with (
                self.subTest(generation=generation),
                upstream(respond(BODY, declared=len(BODY) + 10)) as attempts,
            ):
                with self.assertRaises(error):
                    request(generation=generation)
                self.assertEqual(len(attempts), 1)

    def test_broken_chunk_framing_and_bad_json_are_not_retried_or_exposed(self):
        complete_chunk = f"{len(BODY):x}\r\n".encode() + BODY + b"\r\n"
        for wire, framing in (
            (complete_chunk, "chunked"),  # Missing the terminal zero-size chunk.
            (b"invalid-size\r\nfixture-private-response\r\n", "chunked"),
            (b"fixture-private-response", "length"),
        ):
            with (
                self.subTest(framing=framing, size=len(wire)),
                upstream(respond(wire, framing)) as attempts,
            ):
                with self.assertRaises(intel.GenerationError) as caught:
                    request()
                self.assertNotIn("fixture-private-response", str(caught.exception))
                self.assertNotIn(KEY, str(caught.exception))
                self.assertEqual(len(attempts), 1)

    def test_response_size_boundary_for_all_framings(self):
        for framing in ("length", "chunked", "eof"):
            for extra in (0, 1):
                body = BODY + b" " * (intel.MAX_RESPONSE_BYTES - len(BODY) + extra)
                wire = chunked(body) if framing == "chunked" else body
                with (
                    self.subTest(framing=framing, extra=extra),
                    upstream(respond(wire, framing)) as attempts,
                ):
                    if extra:
                        with self.assertRaisesRegex(intel.GenerationError, "响应过大"):
                            request()
                    else:
                        self.assertEqual(request(), RESULT)
                    self.assertEqual(len(attempts), 1)

    def test_total_deadline_still_bounds_a_dripping_response_without_retry(self):
        def drip(handler):
            handler.send_response(200)
            handler.send_header("Content-Length", str(len(BODY)))
            handler.send_header("Connection", "close")
            handler.end_headers()
            for byte in BODY:
                handler.wfile.write(bytes([byte]))
                handler.wfile.flush()
                time.sleep(0.01)

        with (
            upstream(drip) as attempts,
            patch.object(intel, "GENERATION_TIMEOUT", 0.15),
        ):
            started = time.monotonic()
            with self.assertRaises(intel.GenerationTimeout):
                request()
            self.assertLess(time.monotonic() - started, 2)
            self.assertEqual(len(attempts), 1)

    def test_http_errors_still_do_not_retry(self):
        for status in (302, 429, 500):
            with (
                self.subTest(status=status),
                upstream(
                    respond(b"fixture-private-response", status=status)
                ) as attempts,
            ):
                with self.assertRaises(intel.GenerationError) as caught:
                    request()
                self.assertIn(f"HTTP {status}", str(caught.exception))
                self.assertNotIn("fixture-private-response", str(caught.exception))
                self.assertNotIn(KEY, str(caught.exception))
                self.assertEqual(len(attempts), 1)
