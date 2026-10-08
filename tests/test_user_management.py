"""User/owner-PAT boundaries with isolated data and mocked upstream writes."""

from session_fixture import fixture_identity, session_auth

import base64
import json
import unittest
from unittest.mock import MagicMock, patch

from new_api_cockpit import user_management as manage
from new_api_cockpit.app import app


class UserManagementTest(unittest.TestCase):
    def setUp(self):
        self.operator = {"id": 1, "username": "admin", "role": 100, "status": 1}
        self.user = {"id": 2, "username": "alice", "role": 1, "status": 1, "group": "a"}
        self.token = {
            "id": 3,
            "name": "demo",
            "status": 1,
            "expired_time": -1,
            "remain_quota": 500000,
            "unlimited_quota": False,
            "model_limits_enabled": True,
            "model_limits": "model-a",
            "allow_ips": "127.0.0.1",
            "group": "a",
            "cross_group_retry": False,
            "auto_groups": [],
        }

    def test_pat_matches_base64_crypto_generator_not_inference_key(self):
        for _ in range(100):
            pat = manage.generate_pat()
            self.assertIn(len(base64.b64decode(pat, validate=True)), (21, 22, 23, 24))
            self.assertIn(len(pat), (28, 32))
            self.assertFalse(pat.startswith("sk-"))

    def test_existing_pat_is_read_each_time_without_a_writer_connection(self):
        connection = MagicMock()
        connection.__enter__.return_value = connection
        connection.execute.return_value.fetchone.side_effect = [
            {"access_token": "fixture-first"},
            {"access_token": "fixture-next"},
        ]
        with (
            patch.object(manage, "target", return_value=self.user),
            patch.object(manage.quota, "connect", return_value=connection),
            patch.object(manage.psycopg, "connect") as writer,
        ):
            self.assertEqual(manage.owner_pat(self.operator, 2), "fixture-first")
            self.assertEqual(manage.owner_pat(self.operator, 2), "fixture-next")
            writer.assert_not_called()

    def test_missing_pat_only_calls_restricted_function_then_reads_committed_value(
        self,
    ):
        read = MagicMock()
        read.__enter__.return_value = read
        read.execute.return_value.fetchone.side_effect = [
            {"access_token": None},
            {"access_token": "fixture-committed"},
        ]
        write = MagicMock()
        write.__enter__.return_value = write
        with (
            patch.object(manage, "target", return_value=self.user),
            patch.object(manage.quota, "connect", return_value=read),
            patch.object(manage.psycopg, "connect", return_value=write) as writer,
            patch.object(manage, "_audit_ready"),
            patch.object(manage, "_record_pat_creation"),
        ):
            self.assertEqual(manage.owner_pat(self.operator, 2), "fixture-committed")
        self.assertEqual(
            write.execute.call_args.args[0],
            "SELECT public.statistics_ensure_user_pat(%s,%s,%s)",
        )
        self.assertNotIn("UPDATE", write.execute.call_args.args[0])
        self.assertIn(
            "-c default_transaction_read_only=off", writer.call_args.kwargs["options"]
        )

    def test_filters_are_literal_parameterized_and_fail_closed(self):
        query, params = manage.filters(
            self.operator, {"search": "%_SK", "user_statuses": []}, tokens=True
        )
        self.assertIn("strpos", query)
        self.assertNotIn("%_SK", query)
        self.assertIn("%_SK", params)
        self.assertIn([], params)
        for args in (
            {"user_statuses": [True]},
            {"token_statuses": [9]},
            {"user_groups": "a"},
        ):
            with self.assertRaises(manage.ManagementError):
                manage.filters(self.operator, args, tokens=True)

    def test_numeric_validation_rejects_fractional_ids_and_handles_zero_amount(self):
        for invalid in (True, 1.5, "1.5", "１", None):
            with self.assertRaises(manage.ManagementError):
                manage.positive_id(invalid)
        for zero in (0, "0.0", "0.0000", "0e-6"):
            self.assertEqual(manage.amount_units(zero), 0)
        for invalid in ("NaN", "sNaN", "1e9999999", "0e-9999999", "-1"):
            with self.assertRaises(ValueError):
                manage.amount_units(invalid)
        with self.assertRaises(manage.ManagementError):
            manage.filters(self.operator, ["a"], tokens=True)

    def test_group_options_distinguish_absent_defaults_from_explicit_empty_values(self):
        self.assertEqual(
            manage.config_map({}, "GroupRatio", manage.DEFAULT_GROUP_RATIO),
            {"default": 1, "vip": 1, "svip": 1},
        )
        for value in ("", "  ", None, "null", "{}"):
            self.assertEqual(manage.config_map({"x": value}, "x", {"vip": 1}), {})
        for value in ("[]", "malformed"):
            with self.assertRaises(manage.ManagementError):
                manage.config_map({"x": value}, "x")

    def test_group_change_preserves_latest_api_quota_and_unrelated_settings(self):
        sent = []

        def api(_op, _id, path, **kwargs):
            if kwargs.get("method") == "PUT":
                sent.append(kwargs["body"])
                return {"key": "must-not-return"}
            return {**self.token, "remain_quota": 400000}

        with (
            patch.object(manage, "token_owner", return_value=(self.user, 3)),
            patch.object(manage, "call_api", side_effect=api),
            patch.object(manage, "validate_group"),
        ):
            result = manage._perform(
                self.operator,
                "token",
                3,
                "group",
                {"group": "b"},
                {"group": "a", "status": 1},
            )
        self.assertEqual(sent[0]["remain_quota"], 400000)
        self.assertEqual(sent[0]["model_limits"], "model-a")
        self.assertEqual(sent[0]["allow_ips"], "127.0.0.1")
        self.assertNotIn("used_quota", sent[0])
        self.assertEqual(result, {"updated": True})

    def test_stale_group_preview_does_not_submit(self):
        with (
            patch.object(manage, "token_owner", return_value=(self.user, 3)),
            patch.object(manage, "call_api", return_value=self.token) as api,
        ):
            with self.assertRaises(manage.Conflict):
                manage._perform(
                    self.operator,
                    "token",
                    3,
                    "group",
                    {"group": "b"},
                    {"group": "changed"},
                )
            self.assertEqual(api.call_count, 1)

    def test_user_group_change_uses_admin_and_preserves_latest_profile_not_quotas(self):
        latest = {
            "username": "alice-renamed",
            "display_name": "Latest",
            "remark": "Latest remark",
            "group": "a",
            "role": 10,
            "quota": 400000,
            "used_quota": 100000,
            "status": 2,
            "password": "fixture-hash",
            "access_token": "fixture-secret",
        }
        with (
            patch.object(manage, "target", return_value=self.user) as target,
            patch.object(manage, "available_user_groups", return_value=["a", "b"]),
            patch.object(manage, "call_api", side_effect=[latest, None]) as api,
        ):
            manage._perform(self.operator, "user", 2, "group", {"group": "b"})
        target.assert_called_once_with(self.operator, 2)
        self.assertEqual(api.call_args_list[0].args, (self.operator, 1, "/api/user/2"))
        sent = api.call_args_list[1]
        self.assertEqual(sent.args, (self.operator, 1, "/api/user/"))
        self.assertEqual(sent.kwargs["method"], "PUT")
        self.assertEqual(
            sent.kwargs["body"],
            {
                "id": 2,
                "username": latest["username"],
                "display_name": latest["display_name"],
                "remark": latest["remark"],
                "group": "b",
                "role": 10,
            },
        )

    def test_user_group_rechecks_upstream_group_and_role_before_put(self):
        operator = {**self.operator, "role": 10}
        for latest, exception in (
            ({"group": "b", "role": 1}, manage.Conflict),
            ({"group": "a", "role": 100}, manage.Forbidden),
        ):
            with (
                self.subTest(latest=latest),
                patch.object(manage, "target", return_value=self.user),
                patch.object(manage, "available_user_groups", return_value=["a", "b"]),
                patch.object(manage, "call_api", return_value=latest) as api,
            ):
                with self.assertRaises(exception):
                    manage._perform(operator, "user", 2, "group", {"group": "b"})
                self.assertEqual(api.call_count, 1)

    def test_quota_subtraction_cannot_be_negative_or_apply_to_unlimited(self):
        for data in (self.token, {**self.token, "unlimited_quota": True}):
            with (
                patch.object(manage, "token_owner", return_value=(self.user, 3)),
                patch.object(manage, "call_api", return_value=data) as api,
            ):
                with self.assertRaises(manage.ManagementError):
                    manage._perform(
                        self.operator,
                        "token",
                        3,
                        "quota",
                        {"mode": "subtract", "amount_yuan": "2"},
                    )
                self.assertEqual(api.call_count, 1)

    def test_password_and_pat_do_not_enter_audit_changes(self):
        self.assertEqual(
            manage._safe_changes(
                {
                    "password": "fixture",
                    "password_confirm": "fixture",
                    "key": "fixture",
                    "access_token": "fixture",
                    "group": "a",
                }
            ),
            {"group": "a"},
        )

    def test_csrf_body_and_auth_all_pages_and_assets(self):
        client = app.test_client()
        for path in (
            "/cockpit/keys/",
            "/cockpit/static/keys.js",
            "/cockpit/static/navigation.js",
            "/cockpit/static/dropdowns.js",
            "/cockpit/keys/api/token/3/groups",
            "/cockpit/users/api/user/2/groups",
        ):
            with client.get(path) as response:
                self.assertEqual(
                    response.status_code,
                    200 if "/static/" in path else 401 if "/api/" in path else 302,
                )
        auth = {"Authorization": "Bearer fixture-session-admin"}
        with patch(
            "new_api_cockpit.app.browser_identity", side_effect=fixture_identity
        ):
            response = client.post(
                "/cockpit/keys/api/query/grouped",
                json={"search": "fixture"},
                headers=auth,
            )
            self.assertEqual(response.status_code, 403)
            for extra in (
                {"Sec-Fetch-Site": "cross-site"},
                {"Origin": "https://evil.invalid"},
            ):
                response = client.post(
                    "/cockpit/keys/api/query/grouped",
                    json={"search": "fixture-key"},
                    headers={**auth, "X-Management-Action": "confirm", **extra},
                )
                self.assertEqual(response.status_code, 403)

    def test_standalone_key_lookup_route_is_not_available(self):
        with (
            patch("new_api_cockpit.app.browser_identity", side_effect=fixture_identity),
            patch.object(manage, "list_grouped") as query,
        ):
            response = app.test_client().post(
                "/cockpit/keys/api/search-key",
                json={"key": "fixture-secret"},
                auth=session_auth("admin"),
                headers={"X-Management-Action": "confirm"},
            )
            self.assertEqual(response.status_code, 404)
            query.assert_not_called()
            self.assertFalse(hasattr(manage, "search_key"))
            response.close()

    def test_group_options_route_is_authenticated_read_only_and_owner_scoped(self):
        with (
            patch("new_api_cockpit.app.browser_identity", side_effect=fixture_identity),
            patch.object(
                manage,
                "token_group_options",
                return_value={
                    "id": 3,
                    "group": "a",
                    "user_status": 1,
                    "available_groups": ["a", "b"],
                },
            ) as groups,
            patch.object(manage, "single_action") as action,
        ):
            response = app.test_client().get(
                "/cockpit/keys/api/token/3/groups", auth=session_auth("admin")
            )
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json["available_groups"], ["a", "b"])
            groups.assert_called_once_with("admin", 3)
            action.assert_not_called()

    def test_user_group_options_route_is_read_only_and_write_requires_confirmation(
        self,
    ):
        client = app.test_client()
        with (
            patch("new_api_cockpit.app.browser_identity", side_effect=fixture_identity),
            patch.object(
                manage,
                "user_group_options",
                return_value={
                    "id": 2,
                    "username": "alice",
                    "group": "a",
                    "available_groups": ["a", "b"],
                    "persistence": True,
                },
            ) as groups,
            patch.object(manage, "single_action", return_value={"ok": True}) as action,
        ):
            response = client.get(
                "/cockpit/users/api/user/2/groups", auth=session_auth("admin")
            )
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json["available_groups"], ["a", "b"])
            groups.assert_called_once_with("admin", 2)
            action.assert_not_called()
            body = {"action": "group", "changes": {"group": "b"}}
            for headers in (
                {},
                {"X-Management-Action": "confirm", "Sec-Fetch-Site": "cross-site"},
                {"X-Management-Action": "confirm", "Origin": "https://evil.invalid"},
            ):
                response = client.post(
                    "/cockpit/users/api/user/2/action",
                    json=body,
                    auth=session_auth("admin"),
                    headers=headers,
                )
                self.assertEqual(response.status_code, 403)
                action.assert_not_called()
            response = client.post(
                "/cockpit/users/api/user/2/action",
                json=body,
                auth=session_auth("admin"),
                headers={"X-Management-Action": "confirm"},
            )
            self.assertEqual(response.status_code, 200)
            action.assert_called_once_with("admin", "user", 2, body)

    def test_three_pages_share_relative_sidebar_and_no_old_switch_buttons(self):
        with (
            patch("new_api_cockpit.app.browser_identity", side_effect=fixture_identity),
            patch("new_api_cockpit.app.load_site_name", return_value="Fixture Gateway"),
        ):
            for path in ("/cockpit/statistics/", "/cockpit/users/", "/cockpit/keys/"):
                response = app.test_client().get(
                    path, headers={"Authorization": "Bearer fixture-session-admin"}
                )
                self.assertEqual(response.status_code, 200)
                html = response.get_data(as_text=True)
                for url in (
                    "/cockpit/statistics/",
                    "/cockpit/users/",
                    "/cockpit/keys/",
                ):
                    self.assertIn('href="' + url + '"', html)
                self.assertIn('aria-current="page"', html)
                self.assertNotIn("返回用量统计", html)
                self.assertNotIn('class="quota-nav"', html)

    def test_api_uses_owner_pat_and_refuses_credential_redirects(self):
        response = MagicMock()
        response.__enter__.return_value = response
        response.read.return_value = json.dumps(
            {"success": True, "data": {"updated": True}}
        ).encode()
        opener = MagicMock()
        opener.open.return_value = response
        with (
            patch.object(manage, "owner_pat", return_value="fixture-owner-pat") as pat,
            patch.object(manage.urllib.request, "build_opener", return_value=opener),
        ):
            manage.call_api(
                self.operator, 2, "/api/token/", method="PUT", body={"id": 3}
            )
        pat.assert_called_once_with(self.operator, 2)
        req = opener.open.call_args.args[0]
        self.assertEqual(req.get_header("Authorization"), "Bearer fixture-owner-pat")
        self.assertEqual(req.get_header("New-api-user"), "2")
        self.assertIsNone(
            manage.NoRedirect().redirect_request(None, None, None, None, None, None)
        )

    def test_api_mutation_unknown_and_upstream_errors_do_not_leak_secrets(self):
        opener = MagicMock()
        opener.open.side_effect = TimeoutError()
        with (
            patch.object(manage, "owner_pat", return_value="fixture-pat"),
            patch.object(manage.urllib.request, "build_opener", return_value=opener),
        ):
            with self.assertRaises(manage.Uncertain):
                manage.call_api(self.operator, 2, "/api/token/", method="PUT", body={})
            with self.assertRaises(manage.ManagementError):
                manage.call_api(self.operator, 2, "/api/token/3")


if __name__ == "__main__":
    unittest.main()
