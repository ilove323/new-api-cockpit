"""MCP owner isolation, official PUT, request budgets and write readback."""

import copy
import io
import json
import os
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from new_api_cockpit.app import app
from new_api_cockpit.features.account_mcp import upstream
from new_api_cockpit.integrations.new_api import account

PAT = "nap_fixture_mcp_keys_not_a_real_secret"
USER = {"id": 12, "username": "fixture"}
KEY = "sk-fixture_model_key_only_1234567890"
ROW = {
    "id": 10,
    "user_id": 12,
    "name": "coding",
    "key": KEY,
    "group": "a",
    "status": 1,
    "expired_time": -1,
    "remain_quota": 975310,
    "unlimited_quota": False,
    "model_limits_enabled": True,
    "model_limits": "model-a,model-b",
    "allow_ips": "127.0.0.1",
    "allow_user_quota_query": False,
    "cross_group_retry": False,
    "auto_groups": None,
    "used_quota": 24690,
}


class McpKeysTest(unittest.TestCase):
    def setUp(self):
        self.rows = [copy.deepcopy(ROW)]
        self.calls = []
        self.lose_reply = False
        self.extra_create = False
        self.failure = None
        for context in (
            patch.dict(
                os.environ,
                {"COCKPIT_PROCESS_ROLE": "web", "COCKPIT_MCP_ENABLED": "true"},
            ),
            patch.object(upstream, "authenticate", return_value=USER),
            patch.object(account, "own_keys", side_effect=self.api),
        ):
            context.start()
            self.addCleanup(context.stop)
        self.client = app.test_client()

    def api(self, path, credential, *, method="GET", body=None):
        self.assertEqual(credential, PAT)
        self.calls.append((method, path, copy.deepcopy(body)))
        if path == "/api/user/self/groups":
            return {"a": {"desc": "A"}, "b": {"desc": "B"}, "auto": {"desc": "Auto"}}
        if path.startswith("/api/token/?"):
            page = int(path.split("p=")[1].split("&")[0])
            return {
                "items": copy.deepcopy(self.rows[(page - 1) * 50 : page * 50]),
                "total": len(self.rows),
            }
        if method == "GET":
            return copy.deepcopy(
                next(
                    row for row in self.rows if row["id"] == int(path.rsplit("/", 1)[1])
                )
            )
        if self.failure:
            raise self.failure
        if path.endswith("/key"):
            return {"key": KEY}
        if method == "PUT":
            next(row for row in self.rows if row["id"] == body["id"]).update(body)
        elif method == "DELETE":
            self.rows = [
                row for row in self.rows if row["id"] != int(path.rsplit("/", 1)[1])
            ]
        elif method == "POST":
            new = {
                **ROW,
                **body,
                "id": max((r["id"] for r in self.rows), default=0) + 1,
            }
            self.rows.insert(0, new)
            if self.extra_create:
                self.rows.insert(0, {**new, "id": new["id"] + 1})
        if self.lose_reply:
            raise account.AccountError("fixture lost reply")

    def rpc(self, name, arguments=None):
        return self.client.post(
            "/cockpit/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments or {}},
            },
            headers={
                "Authorization": "Bearer " + PAT,
                "Accept": "application/json, text/event-stream",
            },
        )

    def result(self, name, arguments=None):
        response = self.rpc(name, arguments)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertFalse(response.json["result"]["isError"], response.text)
        return response.json["result"]["structuredContent"]

    def test_list_groups_and_reveal_are_explicit_and_lists_never_leak_keys(self):
        response = self.rpc("list_keys")
        self.assertNotIn(KEY, response.text)
        self.assertNotIn("user_id", response.text)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.result("list_key_groups")["groups"][0]["name"], "")
        self.assertEqual(self.result("reveal_key", {"key_id": 10})["key"], KEY)
        self.assertEqual(self.calls[-1], ("POST", "/api/token/10/key", {}))
        self.assertEqual(response.headers["Cache-Control"], "no-store")

    def test_group_put_preserves_current_configuration_and_never_reads_secret(self):
        result = self.result("change_key_group", {"key_id": 10, "group": "b"})
        self.assertTrue(result["applied"])
        self.assertTrue(result["verified"])
        writes = [call for call in self.calls if call[0] != "GET"]
        self.assertEqual(len(writes), 1)
        method, path, body = writes[0]
        self.assertEqual((method, path), ("PUT", "/api/token/"))
        for field in (
            "name",
            "expired_time",
            "remain_quota",
            "unlimited_quota",
            "model_limits",
            "model_limits_enabled",
            "allow_ips",
            "allow_user_quota_query",
        ):
            self.assertEqual(body[field], ROW[field])
        self.assertEqual(body["group"], "b")
        self.assertNotIn("key", body)
        self.assertNotIn("used_quota", body)
        self.assertEqual(sum("?p=" in call[1] for call in self.calls), 2)
        self.assertEqual(len(self.calls), 5)

    def test_lost_write_replies_are_read_back_without_resending(self):
        self.lose_reply = True
        for name, args in (
            ("change_key_group", {"key_id": 10, "group": "b"}),
            (
                "create_key",
                {
                    "name": "new",
                    "group": "a",
                    "unlimited_quota": False,
                    "amount": "0.000002",
                },
            ),
            ("delete_key", {"key_id": 10}),
        ):
            self.calls.clear()
            result = self.result(name, args)
            self.assertTrue(result["applied"], name)
            self.assertTrue(result["verified"], name)
            self.assertEqual(sum(call[0] != "GET" for call in self.calls), 1)

    def test_ambiguous_concurrent_create_is_not_reported_as_success(self):
        self.lose_reply = self.extra_create = True
        result = self.result("create_key", {"name": "new"})
        self.assertFalse(result["applied"])
        self.assertEqual(result["outcome"], "unknown")
        self.assertEqual(sum(call[0] == "POST" for call in self.calls), 1)

    def test_delete_on_later_page_is_verified_without_false_page_one_success(self):
        self.rows = [{**ROW, "id": i} for i in range(70, 9, -1)]
        self.lose_reply = True
        result = self.result("delete_key", {"key_id": 10})
        self.assertTrue(result["verified"])
        self.assertTrue(any("?p=2&" in call[1] for call in self.calls))

    def test_foreign_owner_and_unknown_or_sensitive_arguments_never_write(self):
        self.rows[0]["user_id"] = 999
        response = self.rpc("change_key_group", {"key_id": 10, "group": "b"})
        self.assertTrue(response.json["result"]["isError"])
        self.assertFalse(any(call[0] != "GET" for call in self.calls))
        self.calls.clear()
        for name, args in (
            ("list_keys", {"user_id": 99}),
            ("list_keys", {"page": True}),
            ("create_key", {"name": "new", "key": KEY}),
            ("reveal_key", {"key_id": True}),
            ("prepare_client_config", {"key_id": 10}),
        ):
            self.assertIn("error", self.rpc(name, args).json)
        self.assertEqual(self.calls, [])

    def test_invalid_create_values_and_negative_group_quota_never_write(self):
        for args in (
            {"name": "new", "unlimited_quota": False, "amount": "-1"},
            {"name": "new", "unlimited_quota": False, "amount": "0.000001"},
            {"name": "new", "unlimited_quota": True, "amount": "10"},
            {"name": "x" * 51},
            {"name": "new", "expired_time": 0},
        ):
            self.assertIn("error", self.rpc("create_key", args).json)
        self.rows[0]["remain_quota"] = -1
        self.assertIn(
            "error", self.rpc("change_key_group", {"key_id": 10, "group": "b"}).json
        )
        self.assertFalse(any(call[0] != "GET" for call in self.calls))

    def test_write_rejection_preserves_429_and_still_reads_after_once(self):
        self.failure = account.AccountError("rate limited", 429, retry_after="90")
        response = self.rpc("delete_key", {"key_id": 10})
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.headers["Retry-After"], "90")
        self.assertEqual(sum("?p=" in call[1] for call in self.calls), 2)
        self.assertEqual(sum(call[0] != "GET" for call in self.calls), 1)


class OwnKeyTransportTest(unittest.TestCase):
    def test_fixed_origin_official_put_and_no_cookie_proxy_or_redirect(self):
        with patch.object(account.request, "build_opener") as build:
            response = build.return_value.open.return_value.__enter__.return_value
            response.status = 200
            response.read.return_value = b'{"success":true}'
            with patch.dict(
                os.environ, {"NEW_API_INTERNAL_URL": "https://internal.test/root"}
            ):
                account.own_keys(
                    "/api/token/", PAT, method="PUT", body={"id": 10, "group": "b"}
                )
            req = build.return_value.open.call_args.args[0]
            self.assertEqual(req.get_method(), "PUT")
            self.assertEqual(req.full_url, "https://internal.test/root/api/token/")
            self.assertEqual(json.loads(req.data), {"id": 10, "group": "b"})
            self.assertEqual(build.call_args.args[0].proxies, {})
            self.assertIsInstance(build.call_args.args[1], account.NoRedirect)
            for header in ("Cookie", "New-api-user", "X-forwarded-for"):
                self.assertIsNone(req.get_header(header))
            self.assertEqual(response.read.call_args.args, (262145,))

    def test_other_routes_and_upstream_error_bodies_cannot_escape(self):
        for method, path in (
            ("PATCH", "/api/token/10"),
            ("GET", "/api/user/1"),
            ("GET", "/api/token/?p=1&page_size=50&user_id=1"),
            ("PUT", "https://other.test/api/token/"),
            ("POST", "/v1/responses"),
        ):
            with (
                patch.object(account.request, "build_opener") as build,
                self.assertRaises(account.AccountError),
            ):
                account.own_keys(path, PAT, method=method)
            build.assert_not_called()
        with patch.object(account.request, "build_opener") as build:
            build.return_value.open.side_effect = HTTPError(
                "https://internal.test", 403, KEY, {}, io.BytesIO(KEY.encode())
            )
            with self.assertRaises(account.AccountError) as caught:
                account.own_keys("/api/token/10/key", PAT, method="POST", body={})
            self.assertEqual(caught.exception.status, 403)
            self.assertIn("api_key:reveal", str(caught.exception))
            self.assertNotIn(KEY, str(caught.exception))


if __name__ == "__main__":
    unittest.main()
