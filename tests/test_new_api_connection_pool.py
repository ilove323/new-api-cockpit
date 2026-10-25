"""Real loopback HTTP/1.1, no service, credentials, database or model calls."""

from collections import deque
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
import socket
import sys
import threading
import time
import unittest
from unittest.mock import Mock, patch

from new_api_cockpit.integrations.new_api import connection_pool, json_client
from new_api_cockpit.integrations.new_api.personal_tokens import TokenError


class CatalogueHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def setup(self):
        super().setup()
        self.connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        with self.server.lock:
            self.server.connections += 1
            self.number = self.server.connections

    def log_message(self, *_args):
        pass

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        self.do_GET()

    def do_GET(self):
        with self.server.lock:
            self.server.requests.append(
                (self.command, self.path, self.number, dict(self.headers))
            )
            action = self.server.actions.popleft() if self.server.actions else "ok"
            self.server.active += 1
            self.server.peak = max(self.server.active, self.server.peak)
            self.server.changed.set()
        try:
            if self.headers.get("Authorization") == "Bearer synthetic-slow":
                self.server.gate.wait(5)
            if action == "disconnect":
                self.close_connection = True
                return
            body = b"not-json" if action == "malformed" else b'{"data":[]}'
            self.send_response(429 if action == "limited" else 200)
            self.send_header("Content-Length", str(len(body)))
            if action == "limited":
                self.send_header("Retry-After", "1800")
            if action == "cookie":
                self.send_header("Set-Cookie", "synthetic=ignored; HttpOnly")
            if action == "close":
                self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(body)
            self.wfile.flush()
            if action == "stale":
                self.close_connection = True
        finally:
            with self.server.lock:
                self.server.active -= 1
                self.server.changed.set()


class CatalogueServer(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, *_args):
        if not isinstance(sys.exception(), (BrokenPipeError, ConnectionResetError)):
            self.errors.append(type(sys.exception()).__name__)

    def close_request(self, request):
        super().close_request(request)
        self.closed_connection.set()


class NewApiConnectionPoolTest(unittest.TestCase):
    def setUp(self):
        self.server = CatalogueServer(("127.0.0.1", 0), CatalogueHandler)
        self.server.lock = threading.Lock()
        self.server.actions, self.server.requests, self.server.errors = deque(), [], []
        self.server.connections, self.server.active, self.server.peak = 0, 0, 0
        self.server.gate, self.server.changed, self.server.closed_connection = (
            threading.Event(),
            threading.Event(),
            threading.Event(),
        )
        self.server.gate.set()
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.environment = patch.dict(
            os.environ,
            {
                "NEW_API_INTERNAL_URL": f"http://127.0.0.1:{self.server.server_port}",
                "COCKPIT_PROCESS_ROLE": "web",
            },
        )
        self.environment.start()
        self.pool = connection_pool.ConnectionPool(maximum=3, per_scope=2)

    def tearDown(self):
        self.server.gate.set()
        self.pool.close()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(5)
        self.environment.stop()
        self.assertEqual(self.server.errors, [])

    def read(
        self, path="/v1/models", credential="synthetic-catalogue", user_id=42, **kwargs
    ):
        return json_client.upstream(path, credential, user_id, pool=self.pool, **kwargs)

    def test_reuses_connections_not_requests_and_maintains_fresh_headers(self):
        for _ in range(8):
            self.assertEqual(self.read("/api/token/7"), {"data": []})
        self.assertEqual(len(self.server.requests), 8)
        self.assertEqual(self.server.connections, 1)
        for _, _, _, headers in self.server.requests:
            self.assertEqual(headers["Authorization"], "Bearer synthetic-catalogue")
            self.assertEqual(headers["New-Api-User"], "42")
            self.assertNotIn("Cookie", headers)
            self.assertNotIn("X-Forwarded-For", headers)
        self.assertNotIn("synthetic-catalogue", repr(self.pool._entries))

    def test_credentials_users_roles_and_model_keys_have_separate_connections(self):
        self.read()
        self.read(credential="synthetic-other")
        self.read(user_id=43)
        with patch.dict(os.environ, {"COCKPIT_PROCESS_ROLE": "chat"}):
            self.read()
        self.read(model_key=True)
        self.assertEqual(self.server.connections, 5)
        self.assertNotIn("New-Api-User", self.server.requests[-1][3])
        self.assertLessEqual(len(self.pool._entries), 3)

    def test_origin_prefix_and_credentials_are_not_taken_from_ambient_proxy_settings(
        self,
    ):
        self.read()
        with patch.dict(
            os.environ,
            {
                "NEW_API_INTERNAL_URL": f"http://127.0.0.1:{self.server.server_port}/alternate",
                "HTTP_PROXY": "http://127.0.0.1:1",
            },
        ):
            self.read()
        self.assertEqual(self.server.connections, 2)
        self.assertEqual(self.server.requests[-1][1], "/alternate/v1/models")
        with patch.dict(
            os.environ,
            {
                "NEW_API_INTERNAL_URL": f"http://synthetic:blocked@127.0.0.1:{self.server.server_port}"
            },
        ):
            with self.assertRaises(TokenError):
                self.read()
        self.assertEqual(len(self.server.requests), 2)

    def test_cookies_close_responses_and_invalid_json_are_never_pooled(self):
        for action in ("cookie", "close", "malformed", "limited"):
            with self.subTest(action=action):
                self.pool.close()
                self.pool = connection_pool.ConnectionPool(maximum=3, per_scope=2)
                self.server.actions.append(action)
                before = self.server.connections
                if action in {"limited", "malformed"}:
                    with self.assertRaises(TokenError) as caught:
                        self.read()
                    if action == "limited":
                        self.assertEqual(caught.exception.status, 429)
                        self.assertEqual(caught.exception.retry_after, "1800")
                else:
                    self.read()
                self.assertEqual(len(self.pool._entries), 0)
                self.read()
                self.assertEqual(self.server.connections - before, 2)
                self.assertNotIn("Cookie", self.server.requests[-1][3])

    def test_lost_key_reveal_response_is_sent_once_without_retry(self):
        self.server.actions.append("disconnect")
        with self.assertRaises(TokenError) as caught:
            self.read("/api/token/7/key", body={})
        self.assertEqual(caught.exception.status, 503)
        self.assertEqual(
            [(row[0], row[1]) for row in self.server.requests],
            [("POST", "/api/token/7/key")],
        )
        self.assertEqual(len(self.pool._entries), 0)

    def test_stale_idle_connection_fails_once_then_next_explicit_read_can_connect(self):
        self.server.actions.append("stale")
        self.read()
        self.assertTrue(self.server.closed_connection.wait(2))
        with self.assertRaises(TokenError):
            self.read()
        self.assertEqual(
            self.server.connections, 1, "failure must not reconnect and resend"
        )
        self.assertEqual(len(self.pool._entries), 0)
        self.read()
        self.assertEqual(self.server.connections, 2)

    def test_per_scope_bounds_do_not_hold_a_lock_across_other_accounts_network_io(self):
        self.server.gate.clear()
        with ThreadPoolExecutor(max_workers=4) as executor:
            slow = [
                executor.submit(self.read, credential="synthetic-slow")
                for _ in range(3)
            ]
            try:
                deadline = time.monotonic() + 3
                while time.monotonic() < deadline:
                    with self.server.lock:
                        active = self.server.active
                    if active == 2:
                        break
                    self.server.changed.wait(0.05)
                    self.server.changed.clear()
                self.assertEqual(active, 2)
                self.assertEqual(len(self.server.requests), 2)
                other = executor.submit(
                    self.read, credential="synthetic-independent", user_id=99
                )
                self.assertEqual(other.result(2), {"data": []})
                self.assertFalse(any(job.done() for job in slow))
                self.assertLessEqual(len(self.pool._entries), 3)
            finally:
                self.server.gate.set()
            self.assertTrue(all(job.result(3) == {"data": []} for job in slow))
        self.assertEqual(len(self.server.requests), 4)


class PoolLifecycleTest(unittest.TestCase):
    def test_waiter_deadline_does_not_start_an_extra_request_or_consume_a_slot(self):
        pool = connection_pool.ConnectionPool(maximum=1, per_scope=1)
        self.addCleanup(pool.close)
        factory = Mock(side_effect=lambda timeout: Mock())
        with pool.connection(("active",), factory, time.monotonic() + 2):
            with self.assertRaises(TimeoutError):
                with pool.connection(("waiter",), factory, time.monotonic() + 0.01):
                    self.fail("occupied pool issued an extra request")
            self.assertEqual(factory.call_count, 1)
            self.assertEqual(len(pool._entries), 1)
        self.assertEqual(len(pool._entries), 0)

    def test_expiry_fork_reset_and_capacity_timeout_do_not_resend_or_leak_slots(self):
        now = [0.0]
        pool = connection_pool.ConnectionPool(
            maximum=1, per_scope=1, clock=lambda: now[0]
        )
        self.addCleanup(pool.close)
        factory = Mock(side_effect=lambda timeout: Mock())
        with pool.connection(("scope",), factory, 5) as lease:
            first = lease.connection
            lease.reusable = True
            with self.assertRaises(TimeoutError):
                with pool.connection(("other",), factory, 0):
                    self.fail("expired waiter acquired another connection")
        self.assertEqual(factory.call_count, 1)
        now[0] = 31
        with pool.connection(("scope",), factory, 36) as lease:
            self.assertIsNot(lease.connection, first)
            lease.reusable = True
        first.close.assert_called_once()
        inherited = next(iter(pool._entries)).connection
        pid = os.getpid()
        with patch.object(connection_pool.os, "getpid", return_value=pid + 1):
            with pool.connection(("scope",), factory, 36) as lease:
                self.assertIsNot(lease.connection, inherited)
        inherited.close.assert_called_once()
        self.assertEqual(len(pool._entries), 0)
