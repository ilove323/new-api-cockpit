"""MCP wire/auth/ownership regression. No real credential or model calls."""

import io
import json
import os
import unittest
import uuid
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from urllib.error import HTTPError, URLError
from unittest.mock import MagicMock, patch

import psycopg

from new_api_cockpit.app import app
from new_api_cockpit.auth import COOKIE_NAME, LoginRequired
from new_api_cockpit.features.account_mcp import http, service, upstream
from new_api_cockpit.report import TZ

PAT = "nap_fixture_private_account_token"
USER = {
    "id": 12,
    "username": "member",
    "role": 1,
    "status": 1,
    "quota": 1250000,
    "used_quota": 500000,
    "request_count": 3,
}
HEADERS = {
    "Authorization": "Bearer " + PAT,
    "Accept": "application/json, text/event-stream",
}


def rpc(method="tools/list", params=None, **changes):
    return {
        "jsonrpc": "2.0",
        "id": 1,
        "method": method,
        **({"params": params} if params is not None else {}),
        **changes,
    }


class AccountMcpHttpTest(unittest.TestCase):
    def test_default_access_log_omits_queries_and_authentication_headers(self):
        from new_api_cockpit.gunicorn_conf import access_log_format

        self.assertIn("%(U)s", access_log_format)
        for atom in ("%(r)s", "%(q)s", "authorization", "cookie"):
            self.assertNotIn(atom, access_log_format.lower())

    def setUp(self):
        self.client = app.test_client()
        self.env = patch.dict(
            os.environ, {"COCKPIT_PROCESS_ROLE": "web", "COCKPIT_MCP_ENABLED": "true"}
        )
        self.env.start()
        self.addCleanup(self.env.stop)
        self.identity = patch.object(upstream, "authenticate", return_value=USER)
        self.auth = self.identity.start()
        self.addCleanup(self.identity.stop)

    def post(self, body=None, **changes):
        return self.client.post(
            "/cockpit/mcp",
            json=body if body is not None else rpc(),
            headers={**HEADERS, **changes},
        )

    def test_initialize_negotiate_and_stateless_reconnect(self):
        for version in http.VERSIONS:
            result = self.post(
                rpc(
                    "initialize",
                    {
                        "protocolVersion": version,
                        "clientInfo": {"name": "fixture", "version": "1"},
                        "capabilities": {},
                    },
                )
            )
            self.assertEqual(result.status_code, 200)
            self.assertEqual(result.json["result"]["protocolVersion"], version)
            self.assertEqual(result.headers["MCP-Protocol-Version"], version)
            self.assertNotIn("Mcp-Session-Id", result.headers)
            self.assertEqual(
                result.json["result"]["capabilities"], {"tools": {"listChanged": False}}
            )
        result = self.post(
            rpc("server/discover"), **{"MCP-Protocol-Version": "2026-07-28"}
        )
        self.assertEqual(result.json["error"]["code"], -32601)
        result = self.post(rpc("notifications/initialized", id=None))
        self.assertEqual(result.status_code, 400)
        body = rpc("notifications/initialized")
        del body["id"]
        result = self.post(body)
        self.assertEqual(result.status_code, 202)
        self.assertEqual(result.data, b"")
        # No sticky session or cached authenticated actor is needed.
        self.assertEqual(
            self.client.post("/cockpit/mcp", json=rpc(), headers=HEADERS).status_code,
            200,
        )

    def test_only_own_account_tools_are_exposed_with_correct_write_hints(self):
        result = self.post().json["result"]
        self.assertEqual(
            [t["name"] for t in result["tools"]],
            [
                "get_balance",
                "get_usage",
                "list_keys",
                "list_key_groups",
                "create_key",
                "delete_key",
                "change_key_group",
                "reveal_key",
            ],
        )
        for tool in result["tools"]:
            write = tool["name"] in {"create_key", "delete_key", "change_key_group"}
            self.assertEqual(tool["annotations"]["readOnlyHint"], not write)
            self.assertEqual(tool["annotations"]["destructiveHint"], write)
            self.assertEqual(tool["annotations"]["idempotentHint"], not write)
            self.assertFalse(tool["inputSchema"]["additionalProperties"])
            self.assertNotIn("user_id", tool["inputSchema"]["properties"])

    def test_get_balance_filters_private_profile_and_preserves_exact_decimal(self):
        self.auth.return_value = {
            **USER,
            "password": "password-private",
            "email": "private@example.test",
            "access_token": PAT,
        }
        result = self.post(rpc("tools/call", {"name": "get_balance", "arguments": {}}))
        self.assertEqual(result.status_code, 200)
        data = result.json["result"]["structuredContent"]
        self.assertEqual(data["remaining_amount"], "2.5")
        self.assertEqual(data["remaining_quota"], 1250000)
        self.assertEqual(data["user"], {"id": 12, "username": "member"})
        for private in (PAT, "password-private", "private@example.test"):
            self.assertNotIn(private, result.text)
        self.assertEqual(json.loads(result.json["result"]["content"][0]["text"]), data)
        self.assertEqual(result.headers["Cache-Control"], "no-store")
        self.assertNotIn("Set-Cookie", result.headers)

    def test_cookie_jwt_model_key_and_query_credentials_cannot_authenticate(self):
        self.client.set_cookie(COOKIE_NAME, "fixture.valid.jwt", path="/cockpit")
        for value in (
            "",
            "Basic anything",
            "Bearer sk-fixture-private-model-key",
            "Bearer fixture.valid.jwt",
            "Bearer " + "A" * 257,
            "Bearer " + PAT + " ",
            "Bearer 含中文的无效用户访问令牌",
        ):
            with self.subTest(value=value):
                self.assertEqual(self.post(Authorization=value).status_code, 401)
        self.auth.assert_not_called()
        result = self.client.post(
            "/cockpit/mcp?access_token=" + PAT, json=rpc(), headers=HEADERS
        )
        self.assertEqual(result.status_code, 400)
        self.assertNotIn(PAT, result.text)
        self.auth.assert_not_called()

    def test_foreign_parameters_and_unknown_tools_never_query_database(self):
        with patch.object(service.quota, "connect") as connect:
            for name, arguments in (
                ("get_balance", {"user_id": 99}),
                ("get_usage", {"username": "other"}),
                ("get_usage", {"url": "http://internal/"}),
                ("get_usage", {"api_key": "sk-other"}),
                ("get_usage", []),
            ):
                result = self.post(
                    rpc("tools/call", {"name": name, "arguments": arguments})
                )
                self.assertEqual(result.json["error"]["code"], -32602)
            for name in ("set_quota", [], {}, None):
                result = self.post(rpc("tools/call", {"name": name}))
                self.assertEqual(result.json["error"]["code"], -32602)
            connect.assert_not_called()

    def test_revocation_scope_failures_and_upstream_outages_fail_closed(self):
        self.post()
        for status in (401, 403, 503):
            self.auth.side_effect = upstream.AccountError("fixture denial", status)
            result = self.post()
            self.assertEqual(result.status_code, status)
            if status == 401:
                self.assertIn("Bearer", result.headers["WWW-Authenticate"])
            self.assertNotIn(PAT, result.text)
        self.assertEqual(self.auth.call_count, 4)

    def test_usage_scope_denial_cannot_fall_back_to_source_db(self):
        with (
            patch.object(
                upstream,
                "read",
                side_effect=upstream.AccountError("usage scope denied", 403),
            ),
            patch.object(service.quota, "connect") as connect,
        ):
            result = self.post(rpc("tools/call", {"name": "get_usage"}))
            self.assertEqual(result.status_code, 403)
            connect.assert_not_called()

    def test_protocol_transport_payload_and_origin_validation(self):
        for origin in (
            "https://elsewhere.test",
            "null",
            "http://localhost@evil.test",
            "http://localhost?x=1",
        ):
            self.assertEqual(self.post(Origin=origin).status_code, 403)
        self.assertEqual(self.post(**{"Sec-Fetch-Site": "cross-site"}).status_code, 403)
        with patch.dict(os.environ, {"COCKPIT_COOKIE_SECURE": "true"}):
            self.assertEqual(self.post(Origin="http://localhost").status_code, 403)
            self.assertEqual(self.post(Origin="https://localhost").status_code, 200)
        self.assertEqual(self.post(Accept="text/html").status_code, 406)
        self.assertEqual(self.post(**{"MCP-Protocol-Version": "nope"}).status_code, 400)
        self.assertEqual(
            self.client.post(
                "/cockpit/mcp",
                data="{",
                content_type="application/json",
                headers=HEADERS,
            ).status_code,
            400,
        )
        self.assertEqual(
            self.client.post(
                "/cockpit/mcp", data="x", content_type="text/plain", headers=HEADERS
            ).status_code,
            415,
        )
        self.assertEqual(
            self.client.post(
                "/cockpit/mcp",
                data='{"x":"' + "a" * 17000 + '"}',
                content_type="application/json",
                headers=HEADERS,
            ).status_code,
            413,
        )
        for bad in (
            [],
            [rpc()],
            {},
            rpc(id=True),
            rpc(id={}),
            rpc(params=[]),
            rpc(jsonrpc="1.0"),
            rpc(method=[]),
        ):
            self.assertEqual(self.post(bad).status_code, 400)
        self.assertEqual(
            self.post(rpc("sampling/createMessage")).json["error"]["code"], -32601
        )
        for method in ("GET", "DELETE"):
            result = self.client.open("/cockpit/mcp", method=method, headers=HEADERS)
            self.assertEqual(result.status_code, 405)
            self.assertEqual(result.headers["Allow"], "POST")

    def test_nonweb_pools_disabled_feature_and_admin_api_separation(self):
        for role in ("chat", "image"):
            with patch.dict(os.environ, {"COCKPIT_PROCESS_ROLE": role}):
                self.assertEqual(self.post().status_code, 404)
        with patch.dict(os.environ, {"COCKPIT_MCP_ENABLED": "false"}):
            self.assertEqual(self.post().status_code, 404)
        self.auth.assert_not_called()
        with patch(
            "new_api_cockpit.app.verify_pat",
            side_effect=LoginRequired("not an admin PAT"),
        ):
            self.assertEqual(
                self.client.get(
                    "/cockpit/api/statistics/balance", headers=HEADERS
                ).status_code,
                401,
            )


class AccountMcpUpstreamTest(unittest.TestCase):
    def response(self, data):
        response = MagicMock()
        response.status = 200
        response.read.return_value = json.dumps(
            {"success": True, "data": data}
        ).encode()
        return response

    def test_fixed_origin_get_with_only_pat_no_proxy_redirect_or_client_identity(self):
        with (
            patch.dict(
                os.environ, {"NEW_API_INTERNAL_URL": "https://new-api.test/prefix"}
            ),
            patch.object(upstream.request, "build_opener") as build,
        ):
            build.return_value.open.return_value.__enter__.return_value = self.response(
                USER
            )
            user = upstream.authenticate(PAT)
            req = build.return_value.open.call_args.args[0]
            self.assertEqual(req.full_url, "https://new-api.test/prefix/api/user/self")
            self.assertEqual(req.get_method(), "GET")
            self.assertEqual(req.get_header("Authorization"), "Bearer " + PAT)
            self.assertIsNone(req.data)
            for name in ("Cookie", "New-api-user", "X-forwarded-for"):
                self.assertIsNone(req.get_header(name))
            self.assertEqual(build.call_args.args[0].proxies, {})
            self.assertIsInstance(build.call_args.args[1], upstream.NoRedirect)
            self.assertEqual(
                set(user), {"id", "username", "quota", "used_quota", "request_count"}
            )

    def test_disabled_expired_limited_malformed_and_network_responses_are_safe(self):
        for status in (401, 403, 302, 500):
            with patch.object(upstream.request, "build_opener") as build:
                build.return_value.open.side_effect = HTTPError(
                    "http://private/", status, PAT, {}, io.BytesIO(PAT.encode())
                )
                with self.assertRaises(upstream.AccountError) as caught:
                    upstream.authenticate(PAT)
                self.assertEqual(
                    caught.exception.status, status if status in (401, 403) else 503
                )
                self.assertNotIn(PAT, str(caught.exception))
        for change in (
            {"id": True},
            {"id": 0},
            {"role": 0},
            {"status": 2},
            {"username": ""},
        ):
            with (
                patch.object(upstream, "read", return_value={**USER, **change}),
                self.assertRaises(upstream.AccountError),
            ):
                upstream.authenticate(PAT)
        for raw in (
            b"x" * 65537,
            b"null",
            b'{"success":false}',
            b'{"success":true,"data":[]}',
        ):
            with patch.object(upstream.request, "build_opener") as build:
                build.return_value.open.return_value.__enter__.return_value.read.return_value = raw
                with self.assertRaises(upstream.AccountError):
                    upstream.authenticate(PAT)
        with patch.object(upstream.request, "build_opener") as build:
            build.return_value.open.side_effect = URLError(PAT)
            with self.assertRaises(upstream.AccountError) as caught:
                upstream.authenticate(PAT)
            self.assertNotIn(PAT, str(caught.exception))

    def test_unapproved_destinations_are_never_contacted(self):
        for base in (
            "ftp://new-api",
            "http://u:p@new-api",
            "http://new-api?token=x",
            "http://new-api#x",
            "//new-api",
        ):
            with (
                patch.dict(os.environ, {"NEW_API_INTERNAL_URL": base}),
                patch.object(upstream.request, "build_opener") as build,
                self.assertRaises(upstream.AccountError),
            ):
                upstream.authenticate(PAT)
            build.assert_not_called()
        for path in (
            "/api/user/99",
            "/api/log",
            "/v1/responses",
            "https://elsewhere.test/api/user/self",
        ):
            with (
                patch.object(upstream.request, "build_opener") as build,
                self.assertRaises(upstream.AccountError),
            ):
                upstream.read(path, PAT)
            build.assert_not_called()


class AccountMcpServiceTest(unittest.TestCase):
    def test_interval_boundaries_and_foreign_arguments(self):
        now = datetime(2026, 10, 24, 12, 0, tzinfo=TZ)
        start, end = service.period({}, now=now)
        self.assertEqual(start.hour, 0)
        self.assertEqual(end, now)
        start, end = service.period(
            {"start": "2026-10-23", "end": "2026-10-24"}, now=now
        )
        self.assertEqual(int((end - start).total_seconds()), 86400)
        self.assertEqual(service.boundary("2026-10-23T16:00:00Z"), end)
        for args in (
            {"start": "2026-10-23"},
            {"start": "2026-10-24", "end": "2026-10-23"},
            {"start": "2026-08-01", "end": "2026-10-01"},
            {"start": "2026-10-23", "end": "2026-11-01"},
            {"user_id": 99},
            {"start": True, "end": "2026-10-24"},
            {"start": "2026-10-23T12:00:00", "end": "2026-10-24"},
        ):
            with self.assertRaises(ValueError):
                service.period(args, now=now)

    def test_usage_reuses_reporting_math_with_owner_predicate_before_aggregation(self):
        values = {field: 2 for field in service.COUNT_FIELDS}
        rows = [
            {
                "is_total": 1,
                "model_name": None,
                **values,
                "amount": Decimal("0.001234"),
            },
            {
                "is_total": 0,
                "model_name": "fixture-model",
                **values,
                "amount": Decimal("0.001234"),
            },
        ]
        with (
            patch.object(upstream, "read") as read,
            patch.object(service.quota, "connect") as connect,
        ):
            connection = connect.return_value.__enter__.return_value
            connection.execute.return_value.fetchall.return_value = rows
            result = service.usage(
                USER, PAT, {"start": "2026-10-01", "end": "2026-10-02"}
            )
            sql, params = connection.execute.call_args.args
            self.assertEqual(params["user_id"], 12)
            self.assertIn("AND user_id = %(user_id)s", sql)
            self.assertNotIn(PAT, sql)
            self.assertLess(
                sql.index("AND user_id = %(user_id)s"),
                sql.index("parts AS MATERIALIZED"),
            )
            self.assertEqual(params["end"] - params["start"], 86400)
            self.assertIn(
                "end_timestamp=" + str(params["end"] - 1), read.call_args.args[0]
            )
            self.assertTrue(read.call_args.args[0].startswith("/api/log/self/stat?"))
            self.assertEqual(result["totals"]["amount"], "0.001234")
            self.assertEqual(result["models"][0]["model"], "fixture-model")
            self.assertEqual(result["user"]["id"], 12)
        with (
            patch.object(upstream, "read"),
            patch.object(
                service.quota, "connect", side_effect=psycopg.OperationalError(PAT)
            ),
            self.assertRaises(upstream.AccountError) as caught,
        ):
            service.usage(USER, PAT, {})
        self.assertNotIn(PAT, str(caught.exception))


@unittest.skipUnless(
    os.environ.get("TEST_SCHEDULE_DATABASE_URL"),
    "requires an explicitly isolated PostgreSQL test database",
)
class AccountMcpPostgresTest(unittest.TestCase):
    def test_real_aggregation_is_owned_deduplicated_and_time_bounded(self):
        from psycopg import sql
        from psycopg.rows import dict_row

        dsn = os.environ["TEST_SCHEDULE_DATABASE_URL"]
        schema = "mcp_test_" + uuid.uuid4().hex
        with psycopg.connect(dsn, autocommit=True) as setup:
            setup.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
            try:
                setup.execute(
                    sql.SQL("SET search_path TO {}").format(sql.Identifier(schema))
                )
                setup.execute(
                    Path(__file__)
                    .with_name("fixtures")
                    .joinpath("account_mcp.sql")
                    .read_text()
                )

                def connect():
                    return psycopg.connect(
                        dsn,
                        row_factory=dict_row,
                        options=f"-c search_path={schema} -c default_transaction_read_only=on -c statement_timeout=10000",
                    )

                with (
                    patch.object(upstream, "read"),
                    patch.object(service.quota, "connect", side_effect=connect),
                ):
                    result = service.usage(
                        USER, PAT, {"start": "2026-10-01", "end": "2026-10-02"}
                    )
                self.assertEqual(
                    result["totals"],
                    {
                        "request_count": 2,
                        "input_tokens": 100,
                        "output_tokens": 30,
                        "cache_read_tokens": 50,
                        "cache_write_tokens": 15,
                        "total_tokens": 195,
                        "amount": "0.30000000000000000000",
                    },
                )
                self.assertEqual(
                    {row["model"] for row in result["models"]},
                    {"gpt-fixture", "claude-fixture"},
                )
            finally:
                setup.execute(
                    sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema))
                )


if __name__ == "__main__":
    unittest.main()
