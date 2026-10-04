"""Canonical pages/assets and absence of retired routes, no network."""

import re
import unittest
from unittest.mock import patch

from new_api_cockpit.app import app


class CockpitRoutesTest(unittest.TestCase):
    def setUp(self):
        self.client = app.test_client()
        self.auth = ("admin", "fixture-password")

    def test_all_four_pages_and_their_assets_are_authenticated(self):
        with (
            patch("new_api_cockpit.app.verify_admin", return_value=True),
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
                    self.assertEqual(self.client.get(path).status_code, 401)
                    response = self.client.get(path, auth=self.auth)
                    self.assertEqual(response.status_code, 200)
                    body = response.get_data(as_text=True)
                    self.assertIn(title, body)
                    for asset in re.findall(
                        r'(?:src|href)="(/cockpit/[^"?#]+\.(?:js|css))"', body
                    ):
                        self.assertEqual(self.client.get(asset).status_code, 401, asset)
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
        with patch("new_api_cockpit.app.verify_admin", return_value=True):
            for old in (
                "/statistics",
                "/statistics/",
                "/quota/",
                "/users/",
                "/statistics/api/balance",
                "/statistics/api/alert",
                "/quota/api/users",
                "/users/api/options",
                "/cockpit/keys/api/operations",
                "/cockpit/users/api/schedule-runs",
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
            self.assertEqual(
                self.client.get("/cockpit", auth=self.auth).headers["Location"],
                "/cockpit/statistics/",
            )

    def test_canonical_external_balance_paths_require_admin_pat(self):
        with (
            patch("new_api_cockpit.app.verify_api_key", return_value=True),
            patch("new_api_cockpit.app.balance.snapshot", return_value={}) as unused,
        ):
            for suffix in ("balance", "alert"):
                for prefix in ("/cockpit/statistics",):
                    path = prefix + "/api/" + suffix
                    self.assertEqual(
                        self.client.get(path, auth=self.auth).status_code, 401
                    )
                    self.assertEqual(
                        self.client.get(path).headers["WWW-Authenticate"], "Bearer"
                    )
            unused.assert_not_called()

    def test_operation_records_are_read_only_and_profile_writes_check_origin(self):
        with (
            patch("new_api_cockpit.app.verify_admin", return_value=True),
            patch(
                "new_api_cockpit.app.operation_records.list_records",
                return_value={"rows": [], "next_before": None},
            ),
            patch("new_api_cockpit.app.user_management.single_action") as mutate,
        ):
            self.assertEqual(
                self.client.get(
                    "/cockpit/operations/api/records", auth=self.auth
                ).status_code,
                200,
            )
            for headers in (
                {},
                {"X-Management-Action": "confirm", "Origin": "https://foreign.invalid"},
            ):
                response = self.client.post(
                    "/cockpit/users/api/user/2/action",
                    auth=self.auth,
                    json={"action": "edit"},
                    headers=headers,
                )
                self.assertEqual(response.status_code, 403)
            mutate.assert_not_called()
