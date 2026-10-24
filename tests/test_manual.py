"""Own-token scope and access boundaries; all upstream calls are synthetic."""

import json
import re
import unittest
from unittest.mock import MagicMock, patch

from new_api_cockpit import manual
from new_api_cockpit.app import USER_ENDPOINTS, app, safe_next
from test_auth import HEADERS, USER, token, upstream as auth_upstream

OWNER = {
    "id": 12,
    "username": "member",
    "role": 1,
    "session_id": "test-session",
    "expires_at": 4102444800,
}
RAW_KEY = "A" * 48
REQUEST_HEADERS = {"X-Cockpit-Manual": "1"}


def own_token(**changes):
    return {
        "id": 7,
        "user_id": 12,
        "name": "My KEY",
        "key": RAW_KEY,
        "group": "default",
        "status": 1,
        "expired_time": -1,
        "remain_quota": 20000,
        "unlimited_quota": False,
        **changes,
    }


class ManualTest(unittest.TestCase):
    def test_ordinary_login_is_accepted_but_existing_routes_default_to_admin(self):
        client = app.test_client()
        with auth_upstream({**USER, "role": 1}):
            response = client.post(
                "/cockpit/api/auth/session",
                json={"access_token": token()},
                headers=HEADERS,
            )
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json["role"], 1)
        with patch("new_api_cockpit.app.request_identity", return_value=OWNER):
            self.assertEqual(client.get("/cockpit").location, "/cockpit/manual")
            # Enumerate all real endpoints, not only a selected list of pages.
            for rule in app.url_map.iter_rules():
                if not rule.rule.startswith(
                    "/cockpit"
                ) or rule.endpoint in USER_ENDPOINTS | {"static", "login_page"}:
                    continue
                path = re.sub(
                    r"<uuid:[^>]+>", "00000000-0000-4000-8000-000000000001", rule.rule
                )
                path = re.sub(r"<[^>]+>", "1", path)
                method = (
                    "GET"
                    if "GET" in rule.methods
                    else next(iter(rule.methods - {"OPTIONS", "HEAD"}))
                )
                with self.subTest(path=path, method=method):
                    response = client.open(path, method=method)
                    self.assertEqual(response.status_code, 403)
                    self.assertNotIn("Set-Cookie", response.headers)

    def test_manual_requires_login_preserves_path_and_hides_admin_navigation(self):
        client = app.test_client()
        self.assertIn(
            "next=%2Fcockpit%2Fmanual", client.get("/cockpit/manual").location
        )
        self.assertEqual(safe_next("/cockpit/manual"), "/cockpit/manual")
        self.assertEqual(client.get("/cockpit/api/manual/tokens").status_code, 401)
        with (
            patch("new_api_cockpit.app.request_identity", return_value=OWNER),
            patch("new_api_cockpit.app.load_site_name", return_value="Fixture"),
        ):
            response = client.get("/cockpit/manual")
        self.assertEqual(response.status_code, 200)
        self.assertIn('href="/cockpit/manual"', response.text)
        self.assertIn('href="/cockpit/intelligence/"', response.text)
        self.assertIn('href="/keys"', response.text)
        for path in ("statistics/", "users/", "keys/", "operations/", "docs/"):
            self.assertNotIn('href="/cockpit/' + path + '"', response.text)
        self.assertNotIn("unsafe-inline", response.headers["Content-Security-Policy"])

    def test_tokens_paginate_and_never_expose_key_or_unneeded_fields(self):
        first = own_token(password="private", access_token="private-pat")
        second = own_token(id=8, status=2)
        with patch.object(
            manual,
            "upstream",
            side_effect=[
                {"success": True, "data": {"items": [first], "total": 2}},
                {"success": True, "data": {"items": [second], "total": 2}},
            ],
        ) as api:
            result = manual.list_tokens(OWNER, "session-fixture")
        self.assertEqual(len(result["tokens"]), 2)
        self.assertTrue(result["tokens"][0]["usable"])
        self.assertFalse(result["tokens"][1]["usable"])
        serialized = json.dumps(result)
        self.assertNotIn(RAW_KEY, serialized)
        self.assertNotIn("private", serialized)
        self.assertIn("p=2", api.call_args.args[0])

    def test_foreign_token_stops_before_key_reveal(self):
        with patch.object(
            manual, "upstream", return_value={"data": own_token(user_id=99)}
        ) as api:
            with self.assertRaises(manual.ManualError) as caught:
                manual.reveal_key(OWNER, "session-fixture", {"token_id": 7})
        self.assertEqual(caught.exception.status, 403)
        api.assert_called_once_with("/api/token/7", "session-fixture", 12)

    def test_invalid_ids_never_reach_upstream(self):
        for body in (
            {},
            [],
            {"token_id": True},
            {"token_id": -1},
            {"token_id": "7"},
            {"token_id": 7, "user_id": 99},
            {"token_id": 7, "url": "http://elsewhere"},
        ):
            with (
                patch.object(manual, "upstream") as api,
                self.assertRaises(manual.ManualError),
            ):
                manual.reveal_key(OWNER, "session-fixture", body)
            api.assert_not_called()

    def test_unusable_tokens_stop_before_reveal_and_never_modify_settings(self):
        for change in ({"status": 2}, {"expired_time": 1}, {"remain_quota": 0}):
            with (
                patch.object(
                    manual, "upstream", return_value={"data": own_token(**change)}
                ) as api,
                self.assertRaises(manual.ManualError),
            ):
                manual.models(OWNER, "session-fixture", {"token_id": 7})
            self.assertEqual(api.call_count, 1)

    def test_reveal_uses_official_owner_endpoint_and_normalizes_prefix_once(self):
        for value in (RAW_KEY, "sk-" + RAW_KEY):
            with patch.object(
                manual,
                "upstream",
                side_effect=[
                    {"data": own_token()},
                    {"data": {"key": value}},
                    {"data": own_token()},
                ],
            ) as api:
                self.assertEqual(
                    manual.reveal_key(OWNER, "session-fixture", {"token_id": 7}),
                    "sk-" + RAW_KEY,
                )
                self.assertEqual(
                    api.call_args_list[1].args,
                    ("/api/token/7/key", "session-fixture", 12),
                )
                self.assertEqual(api.call_args_list[1].kwargs, {"body": {}})
                api.assert_called_with("/api/token/7", "session-fixture", 12)

    def test_models_use_selected_key_and_do_not_guess_unknown_protocols(self):
        with patch.object(
            manual,
            "upstream",
            side_effect=[
                {"data": own_token()},
                {"data": {"key": RAW_KEY}},
                {"data": own_token()},
                {
                    "data": [
                        {"id": "claude-unknown"},
                        {
                            "id": "b",
                            "supported_endpoint_types": [
                                "openai-response",
                                "openai",
                                "embeddings",
                            ],
                        },
                    ]
                },
            ],
        ) as api:
            result = manual.models(OWNER, "session-fixture", {"token_id": 7})
        api.assert_called_with("/v1/models", "sk-" + RAW_KEY, 12, model_key=True)
        self.assertEqual(
            result["models"],
            [
                {"id": "b", "protocols": ["chat", "responses"]},
                {"id": "claude-unknown", "protocols": []},
            ],
        )
        self.assertNotIn(RAW_KEY, json.dumps(result))

    def test_manual_headers_origin_size_and_session_switch_stop_before_api(self):
        client = app.test_client()
        with (
            patch("new_api_cockpit.app.request_identity", return_value=OWNER),
            patch.object(manual, "upstream") as api,
        ):
            for path in ("models", "key"):
                url = "/cockpit/api/manual/" + path
                for headers, body, expected in (
                    ({}, {"token_id": 7}, 403),
                    (
                        {**REQUEST_HEADERS, "Origin": "https://elsewhere.invalid"},
                        {"token_id": 7},
                        403,
                    ),
                    (
                        {**REQUEST_HEADERS, "X-Cockpit-Session": "old-session"},
                        {"token_id": 7},
                        409,
                    ),
                    (REQUEST_HEADERS, {"token_id": 7, "padding": "x" * 1100}, 413),
                ):
                    self.assertEqual(
                        client.post(url, json=body, headers=headers).status_code,
                        expected,
                    )
            api.assert_not_called()

    def test_admin_pat_can_only_select_its_real_owners_tokens(self):
        client = app.test_client()
        identity = {"id": 12, "username": "admin", "role": 10}
        with (
            patch("new_api_cockpit.app.verify_pat", return_value=identity),
            patch.object(
                manual,
                "upstream",
                side_effect=[
                    {"data": own_token()},
                    {"data": {"key": RAW_KEY}},
                    {"data": own_token()},
                ],
            ) as api,
        ):
            response = client.post(
                "/cockpit/api/manual/key",
                json={"token_id": 7},
                headers={**REQUEST_HEADERS, "Authorization": "Bearer synthetic-pat"},
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json, {"key": "sk-" + RAW_KEY})
        self.assertIn("no-store", response.headers["Cache-Control"])
        self.assertEqual(api.call_args.args[1:3], ("synthetic-pat", 12))

    def test_disable_or_expiry_during_key_lookup_does_not_return_a_key(self):
        for change in ({"status": 2}, {"expired_time": 1}):
            with (
                patch.object(
                    manual,
                    "upstream",
                    side_effect=[
                        {"data": own_token()},
                        {"data": {"key": RAW_KEY}},
                        {"data": own_token(**change)},
                    ],
                ),
                self.assertRaises(manual.ManualError) as caught,
            ):
                manual.reveal_key(OWNER, "session-fixture", {"token_id": 7})
            self.assertEqual(caught.exception.status, 409)
            self.assertNotIn(RAW_KEY, str(caught.exception))

    def test_fixed_transport_sanitizes_errors_and_never_redirects_or_spoofs_ip(self):
        with patch.object(manual, "HTTPConnection") as factory:
            conn = factory.return_value
            response = conn.getresponse.return_value.__enter__.return_value
            response.status = 200
            response.read1.side_effect = [b'{"data":[]}', b""]
            self.assertEqual(
                manual.upstream("/v1/models", "sk-" + RAW_KEY, 12, model_key=True),
                {"data": []},
            )
            call = conn.request.call_args
            self.assertEqual(call.args, ("GET", "/v1/models"))
            self.assertEqual(set(call.kwargs["headers"]), {"Authorization", "Accept"})
            conn.close.assert_called_once()
        for status in (301, 403, 429, 500):
            conn = MagicMock()
            conn.getresponse.return_value.__enter__.return_value.status = status
            with (
                patch.object(manual, "HTTPConnection", return_value=conn),
                self.assertRaises(manual.ManualError) as caught,
            ):
                manual.upstream("/v1/models", "sk-" + RAW_KEY, 12, model_key=True)
            self.assertNotIn(RAW_KEY, str(caught.exception))
            self.assertEqual(conn.request.call_count, 1)
            if status == 429:
                self.assertEqual(caught.exception.status, 429)
                self.assertEqual(caught.exception.reason, "rate_limited")


if __name__ == "__main__":
    unittest.main()
