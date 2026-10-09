"""Scope API isolation; optional PostgreSQL tests use read-only synthetic logs."""

from session_fixture import fixture_identity

import json
import os
from datetime import datetime
from decimal import Decimal
import unittest
from contextlib import ExitStack
from unittest.mock import patch

import psycopg
from psycopg.rows import dict_row
from new_api_cockpit.app import app
from new_api_cockpit import report

TAG = {"id": 3, "kind": "tag", "tag_value": "esencloud"}


class ScopeReportingTest(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(
            patch("new_api_cockpit.app.request_identity", side_effect=fixture_identity)
        )
        self.stack.enter_context(
            patch("new_api_cockpit.app.balance.configured", return_value=True)
        )
        self.resolve = self.stack.enter_context(
            patch("new_api_cockpit.app.scope_backend.get_scope", return_value=TAG)
        )
        self.channels = self.stack.enter_context(
            patch(
                "new_api_cockpit.app.scope_backend.channel_filter",
                return_value=[11, 12],
            )
        )
        self.load = self.stack.enter_context(
            patch("new_api_cockpit.app.load_report", return_value=[])
        )
        self.client = app.test_client()
        self.headers = {
            "Authorization": "Bearer fixture-session-a",
            "X-Statistics-Request": "1",
        }
        self.query = "start=2026-07-01&end=2026-07-31&scope_id=3"

    def get(self, path, query=None):
        return self.client.get(
            "/cockpit/api/statistics/" + path + "?" + (query or self.query),
            headers=self.headers,
        )

    def test_scope_catalog_is_public_shape_only(self):
        with patch(
            "new_api_cockpit.app.scope_backend.list_scopes",
            return_value=[dict(TAG, enabled=True)],
        ):
            self.assertEqual(self.get("scopes").json, {"rows": [TAG]})

    def test_all_report_paths_propagate_scope_and_failure_selection(self):
        for path, extra, by_token in [
            ("usage", "", False),
            ("usage/by-token", "", True),
            ("usage/by-selection", "&token_id=2&group=auto&by_token=1", True),
        ]:
            with self.subTest(path=path):
                response = self.get(path, self.query + extra + "&include_failures=1")
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json["scope"], TAG)
                kwargs = self.load.call_args.kwargs
                self.assertEqual(kwargs["channel_ids"], [11, 12])
                self.assertEqual(kwargs["by_token"], by_token)
                self.assertTrue(kwargs["include_failures"])

    def test_options_are_scoped_including_failures(self):
        for path, function in [
            ("tokens", "load_token_options"),
            ("groups", "load_group_options"),
        ]:
            with patch("new_api_cockpit.app." + function, return_value=[]) as load:
                response = self.get("usage/" + path, self.query + "&include_failures=1")
                self.assertEqual(response.json["scope"], TAG)
                load.assert_called_once_with(
                    "2026-07-01",
                    "2026-07-31",
                    channel_ids=[11, 12],
                    include_failures=True,
                )

    def test_default_all_and_explicit_empty_scope(self):
        self.get("usage", "start=2026-07-01&end=2026-07-31")
        self.resolve.assert_not_called()
        self.channels.assert_not_called()
        self.assertNotIn("channel_ids", self.load.call_args.kwargs)
        self.channels.return_value = []
        self.get("usage")
        self.assertEqual(self.load.call_args.kwargs["channel_ids"], [])

    def test_invalid_and_unknown_scope_fail_closed(self):
        for value in ["bad", "", "0", "-1"]:
            self.assertEqual(
                self.get(
                    "usage", self.query.replace("scope_id=3", "scope_id=" + value)
                ).status_code,
                400,
            )
        self.resolve.side_effect = ValueError("账本不存在。")
        self.assertEqual(self.get("usage").status_code, 400)
        self.load.assert_not_called()

    def test_without_monitor_all_catalog_and_explicit_all_still_report(self):
        with patch("new_api_cockpit.app.balance.configured", return_value=False):
            self.assertEqual(
                self.get("scopes").json["rows"],
                [{"id": 1, "kind": "all", "tag_value": ""}],
            )
            self.assertEqual(
                self.get(
                    "usage", self.query.replace("scope_id=3", "scope_id=1")
                ).status_code,
                200,
            )
            self.resolve.assert_not_called()
            self.channels.assert_not_called()
            self.assertNotIn("channel_ids", self.load.call_args.kwargs)

    def test_ungrouped_excludes_tagged_not_unknown_historical_channels(self):
        self.resolve.return_value = {"id": 2, "kind": "ungrouped", "tag_value": ""}
        with patch("new_api_cockpit.app.balance.connect") as connect:
            connect.return_value.__enter__.return_value.execute.return_value.fetchall.return_value = [
                {"channel_id": 11}
            ]
            response = self.get("usage", self.query.replace("scope_id=3", "scope_id=2"))
            self.assertEqual(response.status_code, 200)
            self.assertEqual(self.load.call_args.kwargs["excluded_channel_ids"], [11])
            self.assertNotIn("channel_ids", self.load.call_args.kwargs)

    def test_export_uses_scope_and_filename_without_diagnostics(self):
        response = self.get("export", self.query + "&include_failures=1")
        self.assertEqual(response.status_code, 200)
        self.assertIn("esencloud", response.headers["Content-Disposition"])
        self.assertEqual(self.load.call_args.kwargs["channel_ids"], [11, 12])
        self.assertNotIn("include_failures", self.load.call_args.kwargs)
        response.close()

    def test_balance_settings_status_history_channels_and_check_context(self):
        cases = [
            ("get", "balance/status", "snapshot", {}),
            ("get", "balance/usage-channels", "usage_channels_snapshot", []),
            ("put", "balance/settings", "save_settings", None),
            ("post", "balance/recalculate-history/preview", "history_preview", {}),
            ("post", "balance/recalculate-history", "recalculate_history", {}),
        ]
        for method, path, function, result in cases:
            with (
                self.subTest(path=path),
                patch(
                    "new_api_cockpit.app.balance." + function, return_value=result
                ) as call,
            ):
                response = getattr(self.client, method)(
                    "/cockpit/api/statistics/" + path + "?scope_id=3",
                    headers=self.headers,
                    json={},
                )
                self.assertEqual(response.status_code, 200)
                self.assertEqual(call.call_args.kwargs["scope_id"], 3)
                self.assertEqual(response.json["scope"], TAG)
        with (
            patch("new_api_cockpit.app.balance.check_once", return_value=True) as check,
            patch("new_api_cockpit.app.balance.snapshot", return_value={}) as snapshot,
        ):
            response = self.client.post(
                "/cockpit/api/statistics/balance/check?scope_id=3",
                json={},
                headers=self.headers,
            )
            self.assertEqual(response.status_code, 200)
            check.assert_called_once_with(daily=False, scope_id=3)
            snapshot.assert_called_once_with(live=False, scope_id=3)

    def test_external_balance_and_alert_scoped(self):
        fixture = {
            "configured": True,
            "valid": True,
            "settings": {"budget": Decimal(100), "threshold": Decimal(10)},
            "state": {
                "archived_amount": Decimal(70),
                "current_amount": Decimal(25),
                "remaining": Decimal(5),
                "checked_at": datetime.now(report.TZ),
            },
        }
        with (
            patch(
                "new_api_cockpit.app.balance.snapshot", return_value=fixture
            ) as snapshot,
            patch("new_api_cockpit.app.load_site_name", return_value="test"),
        ):
            response = self.client.get(
                "/cockpit/api/statistics/balance?scope_id=3",
                headers={"Authorization": "Bearer fixture-session-a"},
            )
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json["data"]["scope"], TAG)
            self.assertEqual(response.json["data"]["remaining_quota"], 5)
            snapshot.assert_called_once_with(live=True, scope_id=3)
        with (
            patch("new_api_cockpit.app.balance.check_once") as check,
            patch(
                "new_api_cockpit.app.notifications.current_alert_record",
                return_value=None,
            ) as alert,
        ):
            response = self.client.get(
                "/cockpit/api/statistics/alert?scope_id=3",
                headers={"Authorization": "Bearer fixture-session-a"},
            )
            self.assertEqual(
                response.json, {"scope": TAG, "has_alert": False, "alert": None}
            )
            check.assert_called_once_with(daily=False, scope_id=3)
            alert.assert_called_once_with(scope_id=3)

    def test_report_queries_both_consumption_and_failure_channels_readonly(self):
        with patch.object(report.psycopg, "connect") as connect:
            conn = connect.return_value.__enter__.return_value
            conn.execute.return_value.fetchall.return_value = []
            report.load_report(
                "2026-07-01", "2026-07-31", channel_ids=[11], include_failures=True
            )
            scoped_calls = [
                call
                for call in conn.execute.call_args_list
                if "channel_id = ANY" in call.args[0]
            ]
            self.assertEqual(len(scoped_calls), 2)
            for call in scoped_calls:
                query, params = call.args
                self.assertIn("channel_id = ANY", query)
                self.assertEqual(params["channel_ids"], [11])
            self.assertIn(
                "default_transaction_read_only=on", connect.call_args.kwargs["options"]
            )

    def test_notifications_remain_global(self):
        with patch(
            "new_api_cockpit.app.notifications.snapshot", return_value={}
        ) as load:
            response = self.get("balance/channel")
            self.assertEqual(response.status_code, 200)
            load.assert_called_once_with(None)
            self.resolve.assert_not_called()

    def test_readonly_option_queries_pass_empty_channel_array(self):
        with patch.object(report.psycopg, "connect") as connect:
            conn = connect.return_value.__enter__.return_value
            conn.execute.return_value.fetchall.return_value = []
            for function in [report.load_token_options, report.load_group_options]:
                function("2026-07-01", "2026-07-31", channel_ids=[])
                sql, args = conn.execute.call_args.args
                self.assertIn("channel_id = ANY", sql)
                self.assertEqual(args["channel_ids"], [])
                self.assertEqual(args["types"], [2])
                self.assertEqual(
                    (args["start"], args["end"]),
                    report.period("2026-07-01", "2026-07-31"),
                )
            self.assertIn(
                "default_transaction_read_only=on", connect.call_args.kwargs["options"]
            )


@unittest.skipUnless(os.environ.get("PGHOST"), "PostgreSQL requires PGHOST")
class ScopeSQLTest(unittest.TestCase):
    def test_success_failure_and_null_channel_isolation(self):
        fixtures = [
            dict(
                id=i,
                created_at=i,
                user_id=1,
                username="u",
                token_id=i,
                token_name=f"key-{i}",
                model_name="gpt",
                quota=500000,
                prompt_tokens=10,
                completion_tokens=2,
                other='{"status_code":429}',
                type=kind,
                group="same-log-group",
                channel_id=channel,
            )
            for i, (kind, channel) in enumerate(
                [(2, 11), (2, 12), (2, None), (5, 11), (5, 12), (5, None)], 1
            )
        ]
        prefix = """WITH logs AS (SELECT * FROM jsonb_to_recordset(%(fixtures)s::jsonb) AS r(
            id bigint, created_at bigint, user_id bigint, username text, token_id bigint,
            token_name text, model_name text, quota numeric, prompt_tokens bigint,
            completion_tokens bigint, other text, type integer, "group" text, channel_id bigint)), """
        with psycopg.connect(
            row_factory=dict_row, options="-c default_transaction_read_only=on"
        ) as conn:
            for channel_ids, excluded_ids, count in [
                (None, None, 3),
                ([11], None, 1),
                ([], None, 0),
                ([None], None, 1),
                ([11, None], None, 2),
                (None, [11], 2),
                (None, [], 3),
            ]:
                for base, field in [
                    (report.SQL, "request_count"),
                    (report.FAILURE_SQL, "failure_count"),
                ]:
                    with self.subTest(channels=channel_ids, field=field):
                        sql = (
                            prefix
                            + report.scoped_sql(base, channel_ids, excluded_ids).split(
                                "WITH ", 1
                            )[1]
                        )
                        rows = conn.execute(
                            sql,
                            dict(
                                fixtures=json.dumps(fixtures),
                                start=0,
                                end=20,
                                by_token=True,
                                token_ids=None,
                                groups=["same-log-group"],
                                channel_ids=channel_ids,
                                excluded_channel_ids=excluded_ids,
                            ),
                        ).fetchall()
                        self.assertEqual(sum(row[field] for row in rows), count)
                # Exercise both public option loaders against the same SQL
                # fixtures, including all/empty/NULL channels and error logs.
                for include_failures in (False, True):
                    for load in (report.load_token_options, report.load_group_options):
                        with (
                            self.subTest(
                                loader=load.__name__,
                                channels=channel_ids,
                                excluded=excluded_ids,
                                failures=include_failures,
                            ),
                            patch.object(report.psycopg, "connect") as connect,
                        ):
                            mocked = connect.return_value.__enter__.return_value
                            load(
                                "1970-01-01T08:00:00",
                                "1970-01-01T08:00:19",
                                include_failures=include_failures,
                                channel_ids=channel_ids,
                                excluded_channel_ids=excluded_ids,
                            )
                            query, params = mocked.execute.call_args.args
                            rows = conn.execute(
                                prefix.removesuffix(", ") + " " + query,
                                dict(params, fixtures=json.dumps(fixtures)),
                            ).fetchall()
                            expected = (
                                count * (2 if include_failures else 1)
                                if load is report.load_token_options
                                else int(count > 0)
                            )
                            self.assertEqual(len(rows), expected)
                            if load is report.load_token_options:
                                for row in rows:
                                    self.assertEqual(
                                        row["token_name"], f"key-{row['token_id']}"
                                    )


@unittest.skipUnless(
    os.environ.get("CHECK_NEW_API_SOURCE_SCHEMA") == "1",
    "Explicit opt-in required for real New API source schema",
)
class SourceSchemaContractTest(unittest.TestCase):
    def test_actual_source_columns_and_scoped_query_planning(self):
        """No fixtures: validate real source columns and plan both scope queries.

        Set CHECK_NEW_API_SOURCE_SCHEMA=1 and libpq environment to a New API DB.
        EXPLAIN without ANALYZE validates columns without scanning usage records.
        """
        with psycopg.connect(
            options="-c default_transaction_read_only=on -c statement_timeout=10000"
        ) as conn:
            conn.execute("SELECT channel_id FROM logs LIMIT 0")
            conn.execute("SELECT id, tag FROM channels LIMIT 0")
            params = dict(
                start=0,
                end=1,
                by_token=False,
                token_ids=None,
                groups=None,
                channel_ids=[0],
                excluded_channel_ids=[],
            )
            for query in (report.SQL, report.FAILURE_SQL):
                conn.execute(
                    "EXPLAIN " + report.scoped_sql(query, [0], []), params
                ).fetchall()
