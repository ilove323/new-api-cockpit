"""Run: .venv/bin/python -m unittest discover -s tests."""

from session_fixture import fixture_identity, session_auth

import unittest
from datetime import date, datetime
from decimal import Decimal
from unittest.mock import patch

from new_api_cockpit import balance
from new_api_cockpit import timers
from new_api_cockpit.app import app


class BalanceTest(unittest.TestCase):
    def test_external_balance_api_uses_bearer_and_live_snapshot(self):
        snapshot = {
            "configured": True,
            "valid": True,
            "settings": {"budget": Decimal("220000"), "threshold": Decimal("2000")},
            "state": {
                "archived_amount": Decimal("189626.08"),
                "current_amount": Decimal("28416.83"),
                "remaining": Decimal("1957.09"),
                "checked_at": datetime(2026, 9, 16, 23, 34, 46, tzinfo=balance.TZ),
            },
        }
        with (
            patch(
                "new_api_cockpit.app.verify_pat",
                return_value={"id": 1, "username": "test_admin", "role": 100},
            ) as verify,
            patch("new_api_cockpit.balance.snapshot", return_value=snapshot) as load,
            patch("new_api_cockpit.app.load_site_name", return_value="三生AI网关"),
            patch("new_api_cockpit.balance.check_once") as check,
        ):
            response = app.test_client().get(
                "/cockpit/api/statistics/balance",
                headers={"Authorization": "Bearer sk-fixture"},
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.get_json(),
            {
                "code": 0,
                "data": {
                    "site": "三生AI网关",
                    "currency": "CNY",
                    "total_quota": 220000.0,
                    "used_quota": 218042.91,
                    "remaining_quota": 1957.09,
                    "alert_threshold": 2000.0,
                    "usage_percent": 99.11,
                    "checked_at": "2026-09-16T23:34:46+08:00",
                },
            },
        )
        verify.assert_called_once_with("sk-fixture")
        load.assert_called_once_with(live=True)
        check.assert_not_called()

    def test_external_balance_api_rejects_basic_and_unavailable_data(self):
        client = app.test_client()
        with patch(
            "new_api_cockpit.app.request_identity", side_effect=fixture_identity
        ):
            response = client.get(
                "/cockpit/api/statistics/balance", auth=("test_admin", "fixture")
            )
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.headers["WWW-Authenticate"], "Bearer")
        with (
            patch(
                "new_api_cockpit.app.verify_pat",
                return_value={"id": 1, "username": "test_admin", "role": 100},
            ),
            patch(
                "new_api_cockpit.balance.snapshot",
                return_value={"configured": True, "valid": False},
            ),
        ):
            response = client.get(
                "/cockpit/api/statistics/balance",
                headers={"Authorization": "Bearer fixture"},
            )
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.get_json()["code"], 503)

    def test_balance_recalculates_each_time_without_alert_side_effects(self):
        fixture = {
            "configured": True,
            "valid": True,
            "settings": {"budget": Decimal(0), "threshold": Decimal(10)},
            "state": {
                "archived_amount": Decimal(1),
                "current_amount": Decimal(2),
                "remaining": Decimal(-3),
                "checked_at": datetime(2026, 9, 19, tzinfo=balance.TZ),
            },
        }
        with (
            patch(
                "new_api_cockpit.app.verify_pat",
                return_value={"id": 1, "username": "test_admin", "role": 100},
            ),
            patch("new_api_cockpit.balance.snapshot", return_value=fixture) as load,
            patch("new_api_cockpit.app.load_site_name", return_value="fixture"),
            patch("new_api_cockpit.balance.check_once") as check,
            patch("new_api_cockpit.notifications.notify_safely") as notify,
        ):
            for _ in range(2):
                response = app.test_client().get(
                    "/cockpit/api/statistics/balance",
                    headers={"Authorization": "Bearer fixture"},
                )
                self.assertEqual(response.status_code, 200)
                self.assertIsNone(response.json["data"]["usage_percent"])
                self.assertEqual(response.json["data"]["remaining_quota"], -3)
                self.assertEqual(response.headers["Cache-Control"], "no-store")
            self.assertEqual(load.call_count, 2)
            check.assert_not_called()
            notify.assert_not_called()

    def test_next_run_at_ten(self):
        before = datetime(2026, 9, 16, 9, 59, tzinfo=balance.TZ)
        self.assertEqual(
            timers.next_balance_run(before), before.replace(hour=10, minute=0)
        )
        after = datetime(2026, 9, 16, 22, 30, tzinfo=balance.TZ)
        self.assertEqual(
            timers.next_balance_run(after), datetime(2026, 9, 17, 10, tzinfo=balance.TZ)
        )

    def test_manual_check_endpoint(self):
        with (
            patch("new_api_cockpit.app.request_identity", side_effect=fixture_identity),
            patch("new_api_cockpit.balance.check_once", return_value=True) as check,
            patch(
                "new_api_cockpit.balance.snapshot", return_value={"configured": True}
            ) as snapshot,
        ):
            client = app.test_client()
            url = "/cockpit/api/statistics/balance/check"
            self.assertEqual(
                client.post(url, json={}, auth=session_auth("test_admin")).status_code,
                403,
            )
            check.assert_not_called()
            response = client.post(
                url,
                json={},
                auth=session_auth("test_admin"),
                headers={"X-Statistics-Request": "1"},
            )
            self.assertEqual(response.status_code, 200)
            check.assert_called_once_with(daily=False)
            snapshot.assert_called_once_with(live=False)
            check.side_effect = balance.CheckBusy()
            self.assertEqual(
                client.post(
                    url,
                    json={},
                    auth=session_auth("test_admin"),
                    headers={"X-Statistics-Request": "1"},
                ).status_code,
                409,
            )

    def body(self, **values):
        return dict(
            dict(
                budget="100.00",
                threshold="10",
                start_month="2026-01",
                enabled=True,
                version=1,
            ),
            **values,
        )

    def test_validation(self):
        result = balance.validate_settings(self.body(), date(2026, 2, 10))
        self.assertEqual(result["budget"], Decimal(100))
        self.assertEqual(result["start_month"], date(2026, 1, 1))
        for changes in [
            dict(budget="NaN"),
            dict(threshold="Infinity"),
            dict(budget="-1"),
            dict(budget="1.001"),
            dict(enabled="true"),
            dict(version=True),
            dict(start_month="2026-03"),
            dict(start_month="2026-1"),
        ]:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                balance.validate_settings(self.body(**changes), date(2026, 2, 10))

    def test_usage_channels_endpoint(self):
        rows = [
            {
                "channel_id": 1,
                "channel_name": "主渠道",
                "channel_status": 1,
            }
        ]
        with (
            patch("new_api_cockpit.app.request_identity", side_effect=fixture_identity),
            patch(
                "new_api_cockpit.balance.usage_channels_snapshot",
                return_value=rows,
            ),
        ):
            response = app.test_client().get(
                "/cockpit/api/statistics/balance/usage-channels",
                auth=session_auth("test_admin"),
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {"rows": rows})

    def test_recalculate_history_endpoints(self):
        client = app.test_client()
        body = self.body()
        preview_rows = [{"month": "2026-01", "before": "10", "after": "12"}]
        with (
            patch("new_api_cockpit.app.request_identity", side_effect=fixture_identity),
            patch(
                "new_api_cockpit.balance.history_preview",
                return_value={"version": 1, "rows": preview_rows},
            ) as preview,
            patch(
                "new_api_cockpit.balance.recalculate_history",
                return_value={"version": 2, "months": 1},
            ) as recalculate,
        ):
            preview_url = "/cockpit/api/statistics/balance/recalculate-history/preview"
            apply_url = "/cockpit/api/statistics/balance/recalculate-history"
            self.assertEqual(
                client.post(
                    preview_url, json=body, auth=session_auth("test_admin")
                ).status_code,
                403,
            )
            response = client.post(
                preview_url,
                json=body,
                auth=session_auth("test_admin"),
                headers={"X-Statistics-Request": "1"},
            )
            self.assertEqual(response.get_json()["rows"], preview_rows)
            preview.assert_called_once_with(body)
            payload = {"settings": body, "preview": preview_rows}
            response = client.post(
                apply_url,
                json=payload,
                auth=session_auth("test_admin"),
                headers={"X-Statistics-Request": "1"},
            )
            self.assertEqual(
                response.get_json(),
                {"recalculated": True, "version": 2, "months": 1},
            )
            recalculate.assert_called_once_with(body, preview_rows, "test_admin")
            recalculate.side_effect = balance.HistoryPreviewChanged()
            self.assertEqual(
                client.post(
                    apply_url,
                    json=payload,
                    auth=session_auth("test_admin"),
                    headers={"X-Statistics-Request": "1"},
                ).status_code,
                409,
            )

    def test_month_boundaries(self):
        self.assertEqual(
            balance.month_list(date(2025, 12, 1), date(2026, 3, 1)),
            [date(2025, 12, 1), date(2026, 1, 1), date(2026, 2, 1)],
        )

    def test_api_writes_require_auth_and_custom_header(self):
        client = app.test_client()
        with patch("new_api_cockpit.app.request_identity", return_value=None):
            self.assertEqual(
                client.put(
                    "/cockpit/api/statistics/balance/settings", json=self.body()
                ).status_code,
                401,
            )
        with (
            patch("new_api_cockpit.app.request_identity", side_effect=fixture_identity),
            patch("new_api_cockpit.balance.save_settings") as save,
        ):
            args = dict(auth=session_auth("test_admin"), json=self.body())
            self.assertEqual(
                client.put(
                    "/cockpit/api/statistics/balance/settings", **args
                ).status_code,
                403,
            )
            self.assertEqual(
                client.put(
                    "/cockpit/api/statistics/balance/settings",
                    headers={
                        "X-Statistics-Request": "1",
                        "Sec-Fetch-Site": "cross-site",
                    },
                    **args,
                ).status_code,
                403,
            )
            save.assert_not_called()
            self.assertEqual(
                client.put(
                    "/cockpit/api/statistics/balance/settings",
                    headers={"X-Statistics-Request": "1"},
                    **args,
                ).status_code,
                200,
            )
            save.assert_called_once_with(self.body(), "test_admin")
            save.side_effect = balance.SettingsConflict()
            self.assertEqual(
                client.put(
                    "/cockpit/api/statistics/balance/settings",
                    headers={"X-Statistics-Request": "1"},
                    **args,
                ).status_code,
                409,
            )

    def test_unconfigured(self):
        with patch.dict("os.environ", {"MONITOR_DATABASE_URL": ""}):
            self.assertEqual(balance.snapshot(), {"configured": False})


if __name__ == "__main__":
    unittest.main()
