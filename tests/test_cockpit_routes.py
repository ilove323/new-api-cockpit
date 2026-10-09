"""Canonical pages/assets and absence of retired routes, no network."""

from session_fixture import fixture_identity, session_auth

import re
import unittest
from pathlib import Path
from unittest.mock import patch

from new_api_cockpit.app import app


class CockpitRoutesTest(unittest.TestCase):
    def setUp(self):
        self.client = app.test_client()
        self.auth = session_auth("admin")

    def test_four_pages_require_session_and_static_assets_are_public(self):
        with (
            patch("new_api_cockpit.app.request_identity", side_effect=fixture_identity),
            patch("new_api_cockpit.app.load_site_name", return_value="Fixture"),
        ):
            for route, title in (
                ("statistics", "用量统计"),
                ("users", "用户管理"),
                ("keys", "令牌管理"),
                ("operations", "操作记录"),
            ):
                with self.subTest(route=route):
                    path = "/cockpit/" + route + "/"
                    self.assertEqual(self.client.get(path).status_code, 302)
                    response = self.client.get(path, auth=self.auth)
                    self.assertEqual(response.status_code, 200)
                    body = response.get_data(as_text=True)
                    self.assertIn(title, body)
                    for asset in re.findall(
                        r'(?:src|href)="(/cockpit/[^"?#]+\.(?:js|css))"', body
                    ):
                        with self.client.get(asset) as public_asset:
                            self.assertEqual(public_asset.status_code, 200, asset)
                        with self.client.get(asset, auth=self.auth) as asset_response:
                            self.assertEqual(asset_response.status_code, 200, asset)
                    active = re.findall(r'<a href="([^"]+)" aria-current="page"', body)
                    self.assertEqual(active, [path])

    def test_only_cockpit_business_routes_are_registered(self):
        for rule in app.url_map.iter_rules():
            self.assertTrue(
                rule.rule == "/healthz"
                or rule.rule == "/cockpit"
                or rule.rule.startswith("/cockpit/"),
                rule.rule,
            )
            if "/api/" in rule.rule:
                self.assertTrue(rule.rule.startswith("/cockpit/api/"), rule.rule)
        with patch(
            "new_api_cockpit.app.request_identity", side_effect=fixture_identity
        ):
            for old in (
                "/statistics",
                "/statistics/",
                "/quota/",
                "/users/",
                "/statistics/api/balance",
                "/statistics/api/alert",
                "/quota/api/users",
                "/users/api/options",
                "/cockpit/auth/session",
                "/cockpit/statistics/api/usage",
                "/cockpit/statistics/api/balance",
                "/cockpit/statistics/api/alert",
                "/cockpit/statistics/api/export",
                "/cockpit/users/api/users",
                "/cockpit/users/api/user/2/groups",
                "/cockpit/users/api/schedules",
                "/cockpit/keys/api/options",
                "/cockpit/operations/api/records",
                "/cockpit/api/keys/operations",
                "/cockpit/api/users/schedule-runs",
            ):
                response = self.client.get(old, auth=self.auth)
                self.assertEqual(response.status_code, 404)
                self.assertNotIn("Location", response.headers)
            response = self.client.post(
                "/quota/api/apply",
                auth=self.auth,
                json={"user_ids": [2]},
                headers={"X-Quota-Action": "confirm"},
            )
            self.assertEqual(response.status_code, 404)
            for old in (
                "/cockpit/users/api/apply",
                "/cockpit/keys/api/token/2/action",
                "/cockpit/auth/session",
            ):
                response = self.client.post(old, json={}, auth=self.auth)
                self.assertEqual(response.status_code, 404)
                self.assertNotIn("Location", response.headers)
            self.assertEqual(
                self.client.get("/cockpit", auth=self.auth).headers["Location"],
                "/cockpit/statistics/",
            )

    def test_canonical_external_balance_paths_require_admin_pat(self):
        with (
            patch(
                "new_api_cockpit.app.verify_pat",
                return_value={"id": 1, "username": "test_admin", "role": 100},
            ),
            patch("new_api_cockpit.app.balance.snapshot", return_value={}) as unused,
        ):
            for suffix in ("balance", "alert"):
                for prefix in ("/cockpit/api/statistics",):
                    path = prefix + "/" + suffix
                    self.assertEqual(
                        self.client.get(
                            path, auth=("admin", "fixture-password")
                        ).status_code,
                        401,
                    )
                    self.assertEqual(
                        self.client.get(path).headers["WWW-Authenticate"], "Bearer"
                    )
            unused.assert_not_called()

    def test_api_reference_covers_every_registered_method_and_no_retired_routes(self):
        doc = (Path(__file__).resolve().parents[1] / "docs/api.md").read_text()

        def normalize(path):
            return re.sub(r"<[^>]+>|\{[^}]+\}", "{}", path)

        documented = {
            (method, normalize(path))
            for method, path in re.findall(
                r"^\| `(GET|POST|PUT|PATCH|DELETE)` \| `(/cockpit/api/[^`]+)`",
                doc,
                re.M,
            )
        }
        actual = {
            (method, normalize(rule.rule))
            for rule in app.url_map.iter_rules()
            if rule.rule.startswith("/cockpit/api/")
            for method in rule.methods - {"HEAD", "OPTIONS"}
        }
        self.assertEqual(documented, actual)
        for path, method in (
            ("/cockpit/api/missing", "GET"),
            ("/cockpit/api/users", "PUT"),
        ):
            response = self.client.open(path, method=method)
            self.assertIn(response.status_code, (404, 405))
            self.assertIsInstance(response.json["error"], str)

    def test_operation_records_are_read_only_and_profile_writes_check_origin(self):
        with (
            patch("new_api_cockpit.app.request_identity", side_effect=fixture_identity),
            patch(
                "new_api_cockpit.app.operation_records.list_records",
                return_value={"rows": [], "next_before": None},
            ),
            patch("new_api_cockpit.app.user_management.single_action") as mutate,
        ):
            self.assertEqual(
                self.client.get("/cockpit/api/operations", auth=self.auth).status_code,
                200,
            )
            for headers in (
                {},
                {"X-Management-Action": "confirm", "Origin": "https://foreign.invalid"},
            ):
                response = self.client.post(
                    "/cockpit/api/users/2/action",
                    auth=self.auth,
                    json={"action": "edit"},
                    headers=headers,
                )
                self.assertEqual(response.status_code, 403)
            mutate.assert_not_called()
