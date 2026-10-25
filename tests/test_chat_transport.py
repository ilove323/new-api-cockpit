"""Local socketserver, mocked DNS, killable-helper tests. Never a paid call."""

from contextlib import contextmanager
import errno
import json
import os
import socket
import socketserver
import subprocess
import threading
import time
import unittest
from unittest.mock import patch

from new_api_cockpit.features.chat import config, resolver, transport
from new_api_cockpit.features.chat.errors import ChatError

KEY = "fake-test-key-not-real"
BODY = {
    "model": "offline-example",
    "messages": [{"role": "user", "content": "你好"}],
    "max_tokens": 20,
}


def frame(delta=None, finish=None, *, choices=None, usage=None):
    value = {
        "choices": [{"index": 0, "delta": delta or {}, "finish_reason": finish}]
        if choices is None
        else choices
    }
    if usage is not None:
        value["usage"] = usage
    return (
        b"data: "
        + json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()
        + b"\n\n"
    )


NORMAL = frame({"content": "你好🌍"}) + frame(finish="stop") + b"data: [DONE]\n\n"


class _Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True
    block_on_close = False


class _Handler(socketserver.StreamRequestHandler):
    def handle(self):
        try:
            self.request.settimeout(2)
            first = self.rfile.readline().decode("ascii").strip()
            headers = {}
            while (line := self.rfile.readline()) not in (b"\r\n", b"\n", b""):
                key, value = line.decode("ascii").split(":", 1)
                headers[key.lower()] = value.strip()
            body = self.rfile.read(int(headers.get("content-length", "0")))
            self.server.requests.append((first, headers, body))
            self.server.received.set()
            self.server.script(self)
        except (OSError, ValueError):
            pass


@contextmanager
def upstream(script=None, body=NORMAL, status=200, content_type="text/event-stream"):
    server = _Server(("127.0.0.1", 0), _Handler)
    server.requests, server.received = [], threading.Event()
    if script is None:

        def script(handler):
            handler.wfile.write(
                f"HTTP/1.1 {status} Local\r\nContent-Type: {content_type}\r\nContent-Length: {len(body)}\r\n\r\n".encode()
                + body
            )
            handler.wfile.flush()

    server.script = script
    thread = threading.Thread(
        target=server.serve_forever, kwargs={"poll_interval": 0.01}
    )
    thread.start()
    port = server.server_address[1]
    address = (
        socket.AF_INET,
        socket.SOCK_STREAM,
        socket.IPPROTO_TCP,
        ("127.0.0.1", port),
    )
    try:
        with (
            patch.dict(
                os.environ,
                {
                    "NEW_API_INTERNAL_URL": f"http://fixed.invalid:{port}",
                    "HTTP_PROXY": "http://127.0.0.1:1",
                    "HTTPS_PROXY": "http://127.0.0.1:1",
                },
            ),
            patch.object(transport, "resolve_address", return_value=address),
        ):
            yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(1)


def collect(body=None, abort=None, seconds=2):
    return list(
        transport.stream_chat(
            KEY,
            body or BODY,
            time.monotonic() + seconds,
            abort or transport.AbortControl(),
        )
    )


class TransportTest(unittest.TestCase):
    def test_one_post_fixed_endpoint_no_proxy_cookie_or_retry(self):
        with upstream() as server:
            self.assertEqual(
                collect(),
                [
                    {"type": "text", "text": "你好🌍"},
                    {"type": "finish", "reason": "stop"},
                ],
            )
            self.assertEqual(len(server.requests), 1)
            first, headers, raw = server.requests[0]
            self.assertEqual(first, "POST /v1/chat/completions HTTP/1.1")
            self.assertEqual(headers["authorization"], "Bearer " + KEY)
            self.assertNotIn("cookie", headers)
            self.assertNotIn("x-forwarded-for", headers)
            self.assertTrue(json.loads(raw)["stream"])
        for status in (302, 400, 429, 500):
            with upstream(status=status, body=b"credentials-must-not-leak") as server:
                with self.assertRaises(ChatError) as error:
                    collect()
                self.assertEqual(len(server.requests), 1)
                self.assertTrue(error.exception.uncertain)
                self.assertNotIn("credentials", str(error.exception))

    def test_fragmented_utf8_crlf_comments_multiline_data_and_usage_only_tail(self):
        # Newlines inside JSON whitespace are valid, so SSE multiline data must join with LF.
        multi = (
            b'data: {"choices": [\r\ndata: {"index":0,"delta":{"content":"'
            + "界🌍".encode()
            + b'"},"finish_reason":null}]}\r\n\r\n'
        )
        usage = frame(
            choices=[],
            usage={"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12},
        )
        raw = (
            b"\xef\xbb\xbf: heartbeat\r\n\r\n"
            + multi
            + frame(finish="stop")
            + usage
            + b"data: [DONE]\n\n"
        )

        def script(handler):
            handler.wfile.write(
                b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nTransfer-Encoding: chunked\r\n\r\n"
            )
            for byte in raw:
                handler.wfile.write(b"1\r\n" + bytes([byte]) + b"\r\n")
            handler.wfile.write(b"0\r\n\r\n")
            handler.wfile.flush()

        with upstream(script):
            events = collect()
        self.assertEqual(
            events,
            [
                {"type": "text", "text": "界🌍"},
                {"type": "finish", "reason": "stop"},
                {
                    "type": "usage",
                    "prompt_tokens": 10,
                    "completion_tokens": 2,
                    "total_tokens": 12,
                },
            ],
        )

    def test_tool_fragments_accumulate_validate_and_emit_exact_optional_fields(self):
        parts = [
            frame(
                {
                    "tool_calls": [
                        {
                            "index": 0,
                            "type": "function",
                            "id": "call_1",
                            "function": {"name": "write_file", "arguments": '{"name":'},
                        }
                    ]
                }
            ),
            frame(
                {
                    "tool_calls": [
                        {
                            "index": 0,
                            "function": {"arguments": '"out.md","content":"x"}'},
                        }
                    ]
                }
            ),
            frame(finish="tool_calls"),
            b"data: [DONE]\n\n",
        ]
        with upstream(body=b"".join(parts)):
            events = collect(
                dict(BODY, tools=[{"type": "function"}], tool_choice="auto")
            )
        self.assertEqual(
            events[0],
            {
                "type": "tool_delta",
                "index": 0,
                "id": "call_1",
                "name": "write_file",
                "arguments": '{"name":',
            },
        )
        self.assertEqual(
            events[1],
            {"type": "tool_delta", "index": 0, "arguments": '"out.md","content":"x"}'},
        )
        self.assertEqual(events[-1], {"type": "finish", "reason": "tool_calls"})

    def test_protocol_failures_are_not_success_or_retry(self):
        variants = [
            frame({"content": "partial"}),  # EOF before finish and DONE.
            frame(finish="stop"),  # EOF after finish, still no DONE.
            b"data: [DONE]\n\n",
            b"data: not-json\n\n",
            b'data: {"choices":[],"choices":[]}\n\n',
            b"data: \xff\n\n",
            b"data: \xe4\xb8",  # Invalid/incomplete UTF-8.
            frame(choices=[{"index": 1, "delta": {}}]),
            frame(choices=[{"index": 0, "delta": {}}, {"index": 1, "delta": {}}]),
            frame(choices=[{"index": False, "delta": {}}]),
            frame({"content": ["not-string"]}),
            frame({"role": "system"}),
            frame({"function_call": {"name": "legacy"}}),
            frame(finish="unknown"),
            frame(finish="stop") + frame({"content": "late"}),
            frame(
                choices=[],
                usage={
                    "prompt_tokens": True,
                    "completion_tokens": 1,
                    "total_tokens": 2,
                },
            ),
            frame(
                choices=[],
                usage={"prompt_tokens": -1, "completion_tokens": 1, "total_tokens": 0},
            ),
            frame(choices=[], usage={"prompt_tokens": 1}),
            frame({"content": "\\ud800"}).replace(b"\\\\ud800", b"\\ud800"),
        ]
        for raw in variants:
            with self.subTest(raw=raw), upstream(body=raw) as server:
                with self.assertRaises(ChatError) as error:
                    collect()
                self.assertEqual(len(server.requests), 1)
                self.assertTrue(error.exception.uncertain)
                self.assertNotIn(KEY, str(error.exception))

    def test_incomplete_duplicate_and_illegal_tool_calls_fail_closed(self):
        variants = [
            [{"index": -1}],
            [{"index": 4}],
            [{"index": True}],
            [{"index": 1}],
            [
                {
                    "index": 0,
                    "id": "c",
                    "function": {"name": "read_file", "arguments": "{"},
                }
            ],
            [
                {
                    "index": 0,
                    "id": "c",
                    "function": {"name": "read_file", "arguments": "[]"},
                }
            ],
            [
                {
                    "index": 0,
                    "id": "c",
                    "function": {"name": "read_file", "arguments": '{"n":1e999}'},
                }
            ],
            [{"index": 0, "id": "c", "function": {"arguments": "{}"}}],
            [
                {
                    "index": 0,
                    "id": "c",
                    "function": {"name": "read_file", "arguments": "{}"},
                },
                {
                    "index": 1,
                    "id": "c",
                    "function": {"name": "read_file", "arguments": "{}"},
                },
            ],
        ]
        for calls in variants:
            with (
                self.subTest(calls=calls),
                upstream(
                    body=frame({"tool_calls": calls})
                    + frame(finish="tool_calls")
                    + b"data: [DONE]\n\n"
                ),
            ):
                with self.assertRaises(ChatError):
                    collect(dict(BODY, tools=[{"type": "function"}]))
        with upstream(body=frame({"tool_calls": [{"index": 0}]})):
            with self.assertRaises(ChatError):
                collect()  # Normal chat must not execute unsolicited tools.

    def test_frame_text_argument_and_total_limits(self):
        cases = [
            ("SSE_FRAME_BYTES", 50, b":" + b"x" * 60 + b"\n\n"),
            ("RUN_TEXT_BYTES", 3, NORMAL),
            ("UPSTREAM_BYTES_PER_REQUEST", 150, NORMAL),
            (
                "TOOL_ARGUMENT_BYTES",
                4,
                frame(
                    {
                        "tool_calls": [
                            {
                                "index": 0,
                                "id": "c",
                                "function": {
                                    "name": "read_file",
                                    "arguments": '{"a":1}',
                                },
                            }
                        ]
                    }
                ),
            ),
        ]
        for name, limit, raw in cases:
            with (
                self.subTest(name=name),
                patch.object(config, name, limit),
                upstream(body=raw),
            ):
                with self.assertRaises(ChatError):
                    collect(dict(BODY, tools=[{"type": "function"}]))

    def test_content_type_and_encoding_are_strict(self):
        for content_type in ("application/json", "text/event-stream; charset=latin-1"):
            with upstream(content_type=content_type), self.assertRaises(ChatError):
                collect()

    def test_heartbeats_do_not_extend_idle_or_total_deadline_including_headers(self):
        def stream(handler):
            handler.wfile.write(
                b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\n\r\n"
            )
            for _ in range(30):
                handler.wfile.write(b": hb\n\n")
                handler.wfile.flush()
                time.sleep(0.015)

        with upstream(stream), patch.object(config, "UPSTREAM_IDLE_SECONDS", 0.08):
            started = time.monotonic()
            with self.assertRaises(ChatError) as error:
                collect()
            self.assertLess(time.monotonic() - started, 0.5)
            self.assertEqual(error.exception.code, "CHAT_UPSTREAM_INTERRUPTED")
        with upstream(stream), patch.object(config, "UPSTREAM_IDLE_SECONDS", 10):
            with self.assertRaises(ChatError) as error:
                collect(seconds=0.08)
            self.assertEqual(error.exception.code, "CHAT_DEADLINE")

        def trickle_headers(handler):
            for byte in b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\n\r\n":
                handler.wfile.write(bytes([byte]))
                handler.wfile.flush()
                time.sleep(0.01)

        with upstream(trickle_headers), self.assertRaises(ChatError) as error:
            collect(seconds=0.08)
        self.assertEqual(error.exception.code, "CHAT_DEADLINE")

    def test_sticky_abort_during_read_and_after_dns_never_sends_new_request(self):
        def blocking(handler):
            handler.wfile.write(
                b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\n\r\n"
            )
            handler.wfile.flush()
            time.sleep(0.3)

        abort = transport.AbortControl()
        errors = []
        with upstream(blocking) as server:

            def consume():
                try:
                    collect(abort=abort)
                except ChatError as exc:
                    errors.append(exc)

            worker = threading.Thread(target=consume)
            worker.start()
            self.assertTrue(server.received.wait(1))
            abort.abort()
            worker.join(1)
            self.assertFalse(worker.is_alive())
            self.assertEqual(errors[0].code, "CHAT_CANCELLED")
            self.assertTrue(errors[0].uncertain)
        with upstream() as server:
            address = (
                socket.AF_INET,
                socket.SOCK_STREAM,
                socket.IPPROTO_TCP,
                server.server_address,
            )
            new_abort = transport.AbortControl()

            def resolve(*args):
                new_abort.abort()
                return address

            with (
                patch.object(transport, "resolve_address", side_effect=resolve),
                self.assertRaises(ChatError),
            ):
                collect(abort=new_abort)
            self.assertEqual(server.requests, [])

    def test_abort_registration_and_send_gate_thread_safety(self):
        abort = transport.AbortControl()
        pairs = [socket.socketpair() for _ in range(20)]
        threads = [
            threading.Thread(
                target=lambda sock=sock: self._register_or_cancel(abort, sock)
            )
            for sock, peer in pairs
        ]
        for thread in threads:
            thread.start()
        abort.abort("CHAT_DEADLINE")
        abort.abort()  # First reason is sticky.
        for thread in threads:
            thread.join(1)
        for sock, peer in pairs:
            self.assertEqual(sock.fileno(), -1)
            peer.close()
        left, right = socket.socketpair()
        try:
            with self.assertRaises(ChatError) as error:
                abort.register(left)
            self.assertEqual(error.exception.code, "CHAT_DEADLINE")
            with self.assertRaises(ChatError):
                abort.sendall(left, b"must-not-send", time.monotonic() + 1)
            self.assertEqual(right.recv(20), b"")
        finally:
            left.close()
            right.close()

    @staticmethod
    def _register_or_cancel(abort, sock):
        try:
            abort.register(sock)
        except ChatError:
            pass

    def test_invalid_deadline_and_requests_never_open_a_socket(self):
        for deadline in (None, True, float("nan"), float("inf"), "10"):
            with (
                self.subTest(deadline=deadline),
                patch.object(transport, "resolve_address") as lookup,
            ):
                with self.assertRaises(ChatError):
                    list(
                        transport.stream_chat(
                            KEY, BODY, deadline, transport.AbortControl()
                        )
                    )
                lookup.assert_not_called()
        variants = [
            dict(BODY, url="https://other.invalid"),
            dict(BODY, n=2),
            dict(BODY, stream=False),
            dict(BODY, max_tokens=True),
            dict(BODY, max_tokens=2049),
            dict(BODY, max_completion_tokens=2),
            dict(BODY, stream_options={"include_usage": 1}),
        ]
        for body in variants:
            with (
                self.subTest(body=body),
                patch.object(transport, "resolve_address") as lookup,
            ):
                with self.assertRaises(ChatError):
                    collect(body)
                lookup.assert_not_called()

    def test_empty_tool_fragments_cannot_renew_idle_timer(self):
        first = frame(
            {
                "tool_calls": [
                    {
                        "index": 0,
                        "id": "c",
                        "function": {"name": "read_file", "arguments": "{"},
                    }
                ]
            }
        )
        empty = frame({"tool_calls": [{"index": 0, "function": {"arguments": ""}}]})

        def script(handler):
            handler.wfile.write(
                b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\n\r\n" + first
            )
            for _ in range(30):
                handler.wfile.write(empty)
                handler.wfile.flush()
                time.sleep(0.015)

        with upstream(script), patch.object(config, "UPSTREAM_IDLE_SECONDS", 0.08):
            started = time.monotonic()
            with self.assertRaises(ChatError):
                collect(dict(BODY, tools=[{"type": "function"}]))
            self.assertLess(time.monotonic() - started, 0.5)

    def test_connect_wait_is_cancellable_and_has_its_own_bound(self):
        fake = unittest.mock.Mock()
        fake.connect_ex.return_value = errno.EINPROGRESS
        address = (socket.AF_INET, socket.SOCK_STREAM, 6, ("127.0.0.1", 1))

        def not_ready(*args):
            time.sleep(0.005)
            return [], [], []

        for cancel in (False, True):
            abort = transport.AbortControl()
            timer = threading.Timer(0.03, abort.abort) if cancel else None
            if timer:
                timer.start()
            started = time.monotonic()
            with (
                patch.object(transport, "resolve_address", return_value=address),
                patch.object(transport.socket, "socket", return_value=fake),
                patch.object(transport.select, "select", side_effect=not_ready),
                patch.object(config, "CONNECT_TIMEOUT_SECONDS", 0.07),
                self.assertRaises(ChatError) as error,
            ):
                collect(abort=abort)
            if timer:
                timer.join()
            self.assertEqual(
                error.exception.code,
                "CHAT_CANCELLED" if cancel else "CHAT_UPSTREAM_ERROR",
            )
            self.assertFalse(error.exception.uncertain)
            self.assertLess(time.monotonic() - started, 0.5)
        fake.send.assert_not_called()

    def test_closing_iterator_closes_connection_without_a_retry(self):
        def script(handler):
            handler.wfile.write(
                b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\n\r\n"
                + frame({"content": "first"})
            )
            handler.wfile.flush()
            time.sleep(0.2)

        abort = transport.AbortControl()
        with upstream(script) as server:
            iterator = transport.stream_chat(KEY, BODY, time.monotonic() + 1, abort)
            self.assertEqual(next(iterator), {"type": "text", "text": "first"})
            iterator.close()
            self.assertEqual(abort._sockets, set())
            self.assertEqual(len(server.requests), 1)

    def test_operator_url_restrictions_before_dns(self):
        for url in (
            "https://u:p@fixed.invalid",
            "https://fixed.invalid?q=1",
            "file:///etc/passwd",
            "http://fixed.invalid:0",
            "http://fixed.invalid\n/",
            "http://fixed.invalid/#x",
        ):
            with (
                self.subTest(url=url),
                patch.dict(os.environ, {"NEW_API_INTERNAL_URL": url}),
                patch.object(transport, "resolve_address") as lookup,
            ):
                with self.assertRaises(ChatError):
                    collect()
                lookup.assert_not_called()


class ResolverTest(unittest.TestCase):
    def test_helper_has_empty_environment_and_no_key_or_user_request(self):
        fake = unittest.mock.Mock()
        fake.communicate.return_value = (
            json.dumps(
                [socket.AF_INET, socket.SOCK_STREAM, 6, ["127.0.0.1", 3000]]
            ).encode(),
            b"",
        )
        fake.returncode = 0
        fake.poll.return_value = 0
        with (
            patch.dict(os.environ, {"OPENAI_API_KEY": "secret-never-inherit"}),
            patch.object(resolver.subprocess, "Popen", return_value=fake) as popen,
        ):
            result = resolver.resolve_address(
                "fixed.invalid", 3000, time.monotonic() + 1, transport.AbortControl()
            )
        self.assertEqual(result[3], ("127.0.0.1", 3000))
        args, kwargs = popen.call_args
        self.assertEqual(kwargs["env"], {})
        self.assertNotIn("secret-never-inherit", repr(args))
        self.assertEqual(args[0][-2:], ["fixed.invalid", "3000"])

    def test_blocked_dns_helper_is_killed_and_reaped_on_deadline_and_abort(self):
        for cancel in (False, True):
            children = []
            original = subprocess.Popen

            def record(*args, **kwargs):
                process = original(*args, **kwargs)
                children.append(process)
                return process

            abort = transport.AbortControl()
            timer = threading.Timer(0.05, abort.abort) if cancel else None
            if timer:
                timer.start()
            started = time.monotonic()
            with (
                patch.object(resolver, "_RESOLVER_CODE", "import time; time.sleep(30)"),
                patch.object(resolver.subprocess, "Popen", side_effect=record),
                self.assertRaises(ChatError),
            ):
                resolver.resolve_address(
                    "fixed.invalid", 3000, started + (1 if cancel else 0.08), abort
                )
            if timer:
                timer.join()
            self.assertLess(time.monotonic() - started, 0.7)
            self.assertEqual(len(children), 1)
            self.assertIsNotNone(children[0].poll())
            self.assertTrue(children[0].stdout.closed)


if __name__ == "__main__":
    unittest.main()
