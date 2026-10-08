"""Pure schedule/API boundary tests. Never make a real quota mutation."""

from session_fixture import fixture_identity, session_auth

from datetime import datetime, timezone
from pathlib import Path
from threading import Event
import unittest
from unittest.mock import MagicMock, patch

from new_api_cockpit import quota, quota_schedule as schedules, quota_timer
from new_api_cockpit.app import app
from new_api_cockpit.report import TZ


class ScheduleTest(unittest.TestCase):
    def test_both_compose_templates_have_one_complete_application_service(self):
        root = Path(__file__).resolve().parents[1]
        for name in ("docker-compose.yml", "compose.release.yml"):
            with self.subTest(template=name):
                text = (root / name).read_text()
                self.assertEqual(text.count("  statistics:"), 1)
                self.assertNotIn("  quota-worker:", text)
                self.assertNotIn("  balance-worker:", text)
                self.assertNotIn("profiles:", text)
                self.assertIn("stop_grace_period: 90s", text)

    def test_calendar_boundaries_are_strictly_future_and_beijing(self):
        examples = [
            ("daily", "2026-10-01T00:00:00", "2026-10-02T00:00:00"),
            ("weekly", "2026-10-04T23:59:59", "2026-10-05T00:00:00"),
            ("weekly", "2026-10-05T00:00:00", "2026-10-12T00:00:00"),
            ("monthly", "2026-12-31T23:59:59", "2027-01-01T00:00:00"),
            ("monthly", "2028-02-01T00:00:00", "2028-03-01T00:00:00"),
        ]
        for period, before, after in examples:
            with self.subTest(period=period, before=before):
                self.assertEqual(
                    schedules.next_boundary(
                        period, datetime.fromisoformat(before).replace(tzinfo=TZ)
                    ),
                    datetime.fromisoformat(after).replace(tzinfo=TZ),
                )
        self.assertEqual(
            schedules.next_boundary(
                "daily", datetime(2026, 10, 1, 16, tzinfo=timezone.utc)
            ),
            datetime(2026, 10, 3, tzinfo=TZ),
        )
        for period, now in [("invalid", datetime.now(TZ)), ("daily", datetime.now())]:
            with self.assertRaises(ValueError):
                schedules.next_boundary(period, now)

    def test_rule_validation_preserves_exact_units_and_rejects_invalid_settings(self):
        body = {
            "enabled": True,
            "groups": ["team-a", ""],
            "period": "monthly",
            "operation": "add",
            "amount_yuan": "100.01",
        }
        self.assertEqual(schedules.validate_rule(body)["amount_units"], 50005000)
        self.assertEqual(schedules.validate_rule(body)["groups"], ["", "team-a"])
        for change in [
            {"groups": []},
            {"groups": ["a", "a"]},
            {"groups": [1]},
            {"enabled": 1},
            {"operation": "override"},
            {"period": "hourly"},
            {"amount_yuan": "NaN"},
            {"amount_yuan": "0.000001"},
        ]:
            with self.subTest(change=change), self.assertRaises(ValueError):
                schedules.validate_rule({**body, **change})

    def test_missing_monitor_database_has_clear_read_and_write_boundaries(self):
        with patch.object(schedules.balance, "configured", return_value=False):
            self.assertFalse(schedules.list_rules("admin")["configured"])
            with self.assertRaises(schedules.ScheduleUnavailable):
                schedules.initialize()

    def test_ownership_and_versions(self):
        row = {"executor_user_id": 1, "deleted_at": None, "version": 3}
        schedules._owned(row, {"id": 1, "role": 10})
        schedules._owned(row, {"id": 2, "role": 100})
        with self.assertRaises(schedules.ScheduleForbidden):
            schedules._owned(row, {"id": 2, "role": 10})
        for version in [2, True, None]:
            with self.assertRaises(schedules.ScheduleConflict):
                schedules._version({"version": version}, row)

    def test_settings_apis_require_session_but_static_assets_are_public(self):
        client = app.test_client()
        for path in [
            "/cockpit/users/api/schedules",
            "/cockpit/static/quota-schedule.js",
        ]:
            expected = 200 if "/static/" in path else 401
            with client.get(path) as response:
                self.assertEqual(response.status_code, expected)
            with client.get(
                path, headers={"Authorization": "Bearer fixture-pat"}
            ) as response:
                self.assertEqual(response.status_code, expected)

    def test_write_headers_and_error_status_codes(self):
        client = app.test_client()
        with patch(
            "new_api_cockpit.app.browser_identity", side_effect=fixture_identity
        ):
            for method, path in [
                ("POST", "/cockpit/users/api/schedules"),
                ("PUT", "/cockpit/users/api/schedules/1"),
                ("PATCH", "/cockpit/users/api/schedules/1"),
                ("DELETE", "/cockpit/users/api/schedules/1"),
            ]:
                self.assertEqual(
                    client.open(
                        path, method=method, json={}, auth=session_auth("admin")
                    ).status_code,
                    403,
                )
                self.assertEqual(
                    client.open(
                        path,
                        method=method,
                        json={},
                        auth=session_auth("admin"),
                        headers={
                            "X-Quota-Action": "schedule",
                            "Sec-Fetch-Site": "cross-site",
                        },
                    ).status_code,
                    403,
                )
            for error, code in [
                (schedules.ScheduleConflict("changed"), 409),
                (schedules.ScheduleForbidden("denied"), 403),
                (schedules.ScheduleUnavailable("not configured"), 503),
            ]:
                with patch.object(schedules, "save_rule", side_effect=error):
                    self.assertEqual(
                        client.post(
                            "/cockpit/users/api/schedules",
                            json={},
                            auth=session_auth("admin"),
                            headers={"X-Quota-Action": "schedule"},
                        ).status_code,
                        code,
                    )
            for path in [
                "/cockpit/users/api/schedule-runs?before=bad",
                "/cockpit/users/api/schedule-runs/1?after=bad",
            ]:
                self.assertEqual(
                    client.get(path, auth=session_auth("admin")).status_code, 404
                )

    def test_creating_rule_does_not_execute_or_expose_pat(self):
        client = app.test_client()
        with (
            patch("new_api_cockpit.app.browser_identity", side_effect=fixture_identity),
            patch.object(
                schedules,
                "save_rule",
                return_value={"id": 1, "next_run_at": datetime(2026, 10, 2, tzinfo=TZ)},
            ) as save,
            patch.object(quota, "_call_manage") as mutate,
        ):
            response = client.post(
                "/cockpit/users/api/schedules",
                json={"enabled": True},
                auth=session_auth("admin"),
                headers={"X-Quota-Action": "schedule"},
            )
            self.assertEqual(response.status_code, 201)
            self.assertEqual(save.call_args.args[0], "admin")
            self.assertNotIn("access_token", response.get_data(as_text=True))
            mutate.assert_not_called()

    def test_standby_worker_does_not_recover_claim_or_execute(self):
        leader = MagicMock()
        leader.__enter__.return_value = leader
        leader.execute.return_value.fetchone.return_value = {"acquired": False}
        with (
            patch.object(schedules, "initialize"),
            patch.object(quota_timer.balance, "connect", return_value=leader),
            patch.object(schedules, "recover_interrupted") as recover,
            patch.object(schedules, "claim_due") as claim,
            patch.object(quota_timer.executor, "execute") as execute,
        ):
            stopped = MagicMock(spec=Event)
            quota_timer.serve(stopped)
            recover.assert_not_called()
            claim.assert_not_called()
            execute.assert_not_called()


if __name__ == "__main__":
    unittest.main()
