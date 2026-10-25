"""Retry-After projection and real MCP error transport; no upstream connection."""

from datetime import datetime, timezone
import io
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from flask import Flask

from new_api_cockpit.features.account_mcp import http, service, upstream
from new_api_cockpit.integrations.new_api.rate_limit import retry_after


class RateLimitTest(unittest.TestCase):
    def test_header_seconds_dates_and_untrusted_values(self):
        now = datetime(2026, 10, 25, tzinfo=timezone.utc)
        self.assertEqual(retry_after("001800"), "1800")
        self.assertEqual(retry_after("Sun, 25 Oct 2026 00:30:00 GMT", now=now), "1800")
        for value in (None, "", "-1", "1\r\nInjected: yes", "9" * 129, "secret"):
            self.assertIsNone(retry_after(value))

    def test_upstream_429_reaches_mcp_and_keeps_retry_after(self):
        app = Flask(__name__)
        app.register_blueprint(http.create_blueprint())
        error = HTTPError(
            "https://fixture.invalid",
            429,
            "private",
            {"Retry-After": "1800"},
            io.BytesIO(b"private"),
        )
        with patch.object(upstream.request, "build_opener") as opener:
            opener.return_value.open.side_effect = error
            response = app.test_client().post(
                "/cockpit/mcp",
                json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                headers={"Authorization": "Bearer synthetic_pat_placeholder"},
            )
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.headers["Retry-After"], "1800")
        self.assertNotIn("private", response.get_data(as_text=True))
        opener.return_value.open.assert_called_once()

    def test_usage_rate_limit_is_not_hidden_in_http_200(self):
        app = Flask(__name__)
        app.register_blueprint(http.create_blueprint())
        with (
            patch.object(upstream, "authenticate", return_value={"id": 12}),
            patch.object(
                service,
                "usage",
                side_effect=upstream.AccountError("请求过频", 429, retry_after="2000"),
            ),
        ):
            response = app.test_client().post(
                "/cockpit/mcp",
                json={
                    "jsonrpc": "2.0",
                    "id": 7,
                    "method": "tools/call",
                    "params": {"name": "get_usage", "arguments": {}},
                },
                headers={
                    "Authorization": "Bearer synthetic_pat_placeholder",
                    "Accept": "application/json, text/event-stream",
                },
            )
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.json["id"], 7)
        self.assertEqual(response.headers["Retry-After"], "2000")
