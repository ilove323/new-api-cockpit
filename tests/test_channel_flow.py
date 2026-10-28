"""Channel spending attribution, with no New API calls or source writes."""

from decimal import Decimal
import os
import unittest
from unittest.mock import MagicMock, patch

from werkzeug.datastructures import MultiDict

from database import isolated_schema
from session_fixture import fixture_identity, session_auth
from new_api_cockpit import auth, channel_flow as flow, pat_permissions
from new_api_cockpit.app import app
from new_api_cockpit.openapi import document
from new_api_cockpit.report import period

START, END = "2026-10-01T00:00:00", "2026-10-01T00:00:00"


def record(channel=1, user=1, quota=500000, requests=1, **extra):
    return dict(
        channel_id=channel,
        channel_name=f"channel-{channel}",
        user_id=user,
        username=f"user-{user}",
        display_name="",
        quota=Decimal(quota),
        request_count=requests,
        **extra,
    )


def empty_channel(channel):
    row = record(channel=channel)
    row.update(user_id=None, quota=None, request_count=None)
    return row


class ChannelFlowTest(unittest.TestCase):
    def test_actual_charges_user_shares_zero_channels_and_deterministic_order(self):
        rows = flow.summarize(
            [
                record(user=1, quota=750000, requests=3),
                record(user=2, quota=250000),
                empty_channel(2),
                record(channel=3, quota=0),
                record(channel=4, user=None, quota=1),
            ]
        )
        self.assertEqual([r["channel_id"] for r in rows], [1, 4, 2, 3])
        first = rows[0]
        self.assertEqual(first["amount"], Decimal(2))
        self.assertEqual(first["request_count"], 4)
        self.assertEqual(first["user_count"], 2)
        self.assertEqual(
            [u["percentage"] for u in first["users"]], [Decimal(75), Decimal(25)]
        )
        self.assertEqual(sum(u["amount"] for u in first["users"]), first["amount"])
        self.assertEqual(rows[1]["amount"], Decimal("0.000002"))
        self.assertEqual(rows[1]["users"][0]["percentage"], 100)
        self.assertEqual(rows[2]["users"], [])
        self.assertEqual(rows[2]["user_count"], 0)
        self.assertEqual(rows[3]["users"][0]["percentage"], None)

    def test_same_usernames_do_not_merge_ids_and_amounts_are_not_rounded(self):
        records = [record(user=1, quota=1), record(user=2, quota=2)]
        for r in records:
            r["username"] = "same-name"
        result = flow.summarize(records)[0]
        self.assertEqual([r["user_id"] for r in result["users"]], [2, 1])
        self.assertEqual(result["amount"], Decimal("0.000006"))
        self.assertEqual(result["users"][0]["amount"], Decimal("0.000004"))
        self.assertGreater(result["users"][0]["percentage"], Decimal("66.666666"))
        self.assertEqual(flow.summarize([]), [])
        with patch.object(flow, "MAX_ROWS", 1), self.assertRaises(flow.FlowUnavailable):
            flow.summarize(records)

    def test_parameters_reject_foreign_scopes_filters_duplicates_and_invalid_dates(
        self,
    ):
        self.assertEqual(
            flow.parameters(MultiDict({"start": START, "end": END})), (START, END)
        )
        for args in [
            {"start": "invalid", "end": END},
            {"start": END, "end": "2026-09-01"},
            {"start": "2025-01-01", "end": "2026-10-01"},
            *(
                {"start": START, "end": END, key: "1"}
                for key in ("scope_id", "user", "model", "token_id", "group")
            ),
            [("start", START), ("start", START), ("end", END)],
        ]:
            with self.subTest(args=args), self.assertRaises(ValueError):
                flow.parameters(MultiDict(args))

    def test_source_is_readonly_bounded_single_query_with_inclusive_seconds(self):
        with patch.object(flow.psycopg, "connect") as connect:
            flow.source_connection()
        self.assertIn(
            "default_transaction_read_only=on", connect.call_args.kwargs["options"]
        )
        self.assertIn("statement_timeout=60000", connect.call_args.kwargs["options"])
        conn = MagicMock()
        conn.__enter__.return_value = conn
        conn.execute.return_value.fetchall.return_value = [record()]
        with patch.object(flow, "source_connection", return_value=conn) as connect:
            result = flow.load(START, END)
        connect.assert_called_once()
        conn.execute.assert_called_once()
        sql, params = conn.execute.call_args.args
        self.assertIs(sql, flow.SQL)
        first, last = period(START, END)
        self.assertEqual(params, dict(start=first, end=last, limit=flow.MAX_ROWS + 1))
        self.assertEqual(last - first, 1)
        self.assertIn("WHERE status=1", sql)
        self.assertIn("l.type=2", sql)
        self.assertIn("GROUP BY l.channel_id,l.user_id", sql)
        self.assertIn("LEFT JOIN usage", sql)
        self.assertEqual(result["start"], START)

    def test_endpoint_admin_only_and_scoped_pat_needs_both_read_permissions(self):
        client = app.test_client()
        path = f"/cockpit/api/statistics/channel-flow?start={START}&end={END}"
        data = dict(start=START, end=END, rows=flow.summarize([record()]))
        with (
            patch("new_api_cockpit.app.request_identity", side_effect=fixture_identity),
            patch.object(flow, "load", return_value=data) as load,
        ):
            self.assertEqual(client.get(path).status_code, 401)
            with patch(
                "new_api_cockpit.app.request_identity",
                return_value={"id": 2, "username": "member", "role": 1},
            ):
                self.assertEqual(client.get(path).status_code, 403)
            load.assert_not_called()
            response = client.get(path, auth=session_auth("admin"))
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json["rows"][0]["amount"], "1")
            self.assertEqual(response.headers["Cache-Control"], "no-store")
            load.assert_called_once_with(START, END)
            self.assertEqual(
                client.get(
                    path + "&scope_id=1", auth=session_auth("admin")
                ).status_code,
                400,
            )
            self.assertEqual(load.call_count, 1)
        for scopes in (set(), {"log:read"}, {"channel:read"}):
            actor = auth.VerifiedPat(
                {"id": 1, "username": "admin", "role": 100}, frozenset(scopes)
            )
            with self.subTest(scopes=scopes), self.assertRaises(auth.LoginForbidden):
                pat_permissions.require_access(actor, "channel_flow_usage", "GET")
        actor = auth.VerifiedPat(
            {"id": 1, "username": "admin", "role": 100},
            frozenset({"log:read", "channel:read"}),
        )
        pat_permissions.require_access(actor, "channel_flow_usage", "GET")
        self.assertEqual(
            document()["paths"]["/cockpit/api/statistics/channel-flow"]["get"][
                "x-cockpit-effect"
            ],
            "read",
        )

    def test_oversized_result_is_explicit_error_not_partial_data(self):
        with (
            patch("new_api_cockpit.app.request_identity", side_effect=fixture_identity),
            patch.object(
                flow, "load", side_effect=flow.FlowUnavailable("too many rows")
            ),
        ):
            response = app.test_client().get(
                f"/cockpit/api/statistics/channel-flow?start={START}&end={END}",
                auth=session_auth("admin"),
            )
            self.assertEqual(response.status_code, 503)
            self.assertNotIn("rows", response.json)


@unittest.skipUnless(
    os.environ.get("MONITOR_DATABASE_URL"), "Requires disposable PostgreSQL"
)
class ChannelFlowSQLTest(unittest.TestCase):
    def test_enabled_channels_all_users_historical_charges_and_time_bounds(self):
        with (
            isolated_schema() as connect,
            patch.object(flow, "source_connection", connect),
        ):
            first, _ = period(START, END)
            with connect() as conn:
                conn.execute("CREATE TABLE channels(id bigint,name text,status int)")
                conn.execute(
                    "CREATE TABLE users(id bigint,username text,display_name text)"
                )
                conn.execute(
                    "CREATE TABLE logs(id bigint,created_at bigint,type int,channel_id bigint,user_id bigint,username text,quota bigint)"
                )
                conn.execute(
                    "INSERT INTO channels VALUES(1,'enabled',1),(2,'disabled',2),(3,'empty',1),(4,'zero',1),(5,'auto-disabled',3)"
                )
                conn.execute(
                    "INSERT INTO users VALUES(1,'renamed','显示名'),(3,'renamed','另一个同名用户')"
                )
                entries = [
                    (1, first, 2, 1, 1, "old-name", 500000),
                    (2, first, 2, 1, 1, "renamed", 250000),
                    (3, first, 2, 1, 2, "deleted-user", 250000),
                    (4, first, 5, 1, 1, "error-log", 999999),
                    (5, first - 1, 2, 1, 1, "before", 999999),
                    (6, first + 1, 2, 1, 1, "after", 999999),
                    (7, first, 2, 2, 1, "disabled", 999999),
                    (8, first, 2, 4, 3, "renamed", 0),
                    (9, first, 2, 99, 1, "deleted-channel", 999999),
                    (10, first, 2, 5, 1, "auto-disabled", 999999),
                ]
                with conn.cursor() as cursor:
                    cursor.executemany(
                        "INSERT INTO logs VALUES(%s,%s,%s,%s,%s,%s,%s)", entries
                    )
            rows = flow.load(START, END)["rows"]
            self.assertEqual([r["channel_id"] for r in rows], [1, 3, 4])
            self.assertEqual(rows[0]["amount"], 2)
            self.assertEqual(rows[0]["request_count"], 3)
            self.assertEqual([u["user_id"] for u in rows[0]["users"]], [1, 2])
            self.assertEqual(
                [u["username"] for u in rows[0]["users"]], ["renamed", "deleted-user"]
            )
            self.assertEqual([u["percentage"] for u in rows[0]["users"]], [75, 25])
            self.assertEqual(rows[1]["users"], [])
            self.assertIsNone(rows[2]["users"][0]["percentage"])
