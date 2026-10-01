"""Run with MONITOR_DATABASE_URL pointing to disposable PostgreSQL (isolated schema)."""

import os
import unittest
import uuid
from datetime import datetime, date
from decimal import Decimal
from unittest.mock import patch

import psycopg
from psycopg import sql
from psycopg.rows import dict_row

from new_api_statistics import balance, scopes


class ScopeUnitTest(unittest.TestCase):
    def test_names_have_no_display_name_column(self):
        self.assertEqual(scopes.scope_name(dict(kind="tag", tag_value="A")), "A")
        self.assertEqual(scopes.scope_name(dict(kind="all", tag_value="")), "全部")
        self.assertEqual(
            scopes.scope_name(dict(kind="ungrouped", tag_value="")), "未分组"
        )

    def test_invalid_ids_rejected_without_database(self):
        for value in (True, 1.5, None, "bad"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                scopes.get_scope(value)

    def test_daily_dispatch_enabled_only_and_continues_after_failure(self):
        with (
            patch.object(
                scopes,
                "list_scopes",
                return_value=[
                    dict(id=1, enabled=True),
                    dict(id=2, enabled=False),
                    dict(id=3, enabled=True),
                ],
            ),
            patch.object(
                balance, "check_once", side_effect=[RuntimeError(), True]
            ) as check,
            patch.object(balance, "record_failure") as failure,
            self.assertLogs(level="ERROR"),
        ):
            result = balance.check_all_enabled()
        self.assertEqual(result, {1: False, 3: True})
        self.assertEqual([c.kwargs["scope_id"] for c in check.call_args_list], [1, 3])
        failure.assert_called_once_with(scope_id=1)


@unittest.skipUnless(
    os.environ.get("MONITOR_DATABASE_URL"), "disposable PostgreSQL not configured"
)
class ScopeDatabaseTest(unittest.TestCase):
    def setUp(self):
        self.dsn = os.environ["MONITOR_DATABASE_URL"]
        self.schema = "scopes_test_" + uuid.uuid4().hex
        with psycopg.connect(self.dsn) as conn:
            conn.execute(
                sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(self.schema))
            )
        self.addCleanup(self.cleanup_schema)
        self.catalog = [
            dict(channel_id=1, channel_name="one", channel_status=1, tag_value="A"),
            dict(channel_id=2, channel_name="two", channel_status=2, tag_value="B"),
            dict(channel_id=0, channel_name="zero", channel_status=1, tag_value="A"),
        ]
        self.enterContext(patch.object(balance, "connect", self.connect))
        self.enterContext(patch.object(balance, "configured", return_value=True))
        self.enterContext(
            patch.object(
                balance, "source_channels", side_effect=lambda **kwargs: self.catalog
            )
        )
        self.enterContext(patch("new_api_statistics.notifications.notify_safely"))
        balance.initialize()
        scopes.refresh_scopes()
        self.ids = {
            r["tag_value"]: r["id"]
            for r in scopes.list_scopes(False)
            if r["kind"] == "tag"
        }

    def connect(self):
        return psycopg.connect(
            self.dsn, row_factory=dict_row, options="-c search_path=" + self.schema
        )

    def cleanup_schema(self):
        with psycopg.connect(self.dsn) as conn:
            conn.execute(
                sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(self.schema))
            )

    def write_month(self):
        month = date(2026, 8, 1)
        details = [
            dict(channel_id=i, channel_name=str(i), amount=Decimal(amount))
            for i, amount in [(1, 10), (2, 20), (0, 3), (None, 4), (99, 5)]
        ]
        with self.connect() as conn:
            balance.write_channel_archives(conn, [month], {month: details})
        return month

    def test_archive_current_membership_visibility_and_deleted(self):
        month = self.write_month()
        with self.connect() as conn:
            before = conn.execute(
                "SELECT scope_id,amount FROM balance_month_scopes WHERE month=%s",
                (month,),
            ).fetchall()
            self.assertEqual(
                {r["scope_id"]: r["amount"] for r in before},
                {1: 42, 2: 12, self.ids["A"]: 10, self.ids["B"]: 20},
            )
            conn.execute("INSERT INTO balance_excluded_channels(channel_id) VALUES(1)")
        self.catalog[0]["tag_value"] = "C"
        self.catalog = [r for r in self.catalog if r["channel_id"] != 2]
        scopes.refresh_scopes()
        new_id = next(
            r["id"] for r in scopes.list_scopes(False) if r["tag_value"] == "C"
        )
        with self.connect() as conn:
            self.assertEqual(
                balance.archived_month_rows(
                    conn, month, date(2026, 9, 1), scope_id=new_id
                )[0]["amount"],
                10,
            )
            self.assertEqual(
                balance.archived_month_rows(
                    conn, month, date(2026, 9, 1), scope_id=self.ids["A"]
                )[0]["amount"],
                0,
            )
            self.assertEqual(
                balance.archived_month_rows(
                    conn, month, date(2026, 9, 1), [1], scope_id=1
                )[0]["amount"],
                42,
            )
        self.assertNotIn(self.ids["B"], [r["id"] for r in scopes.list_scopes(False)])
        self.assertIsNone(balance.check_once(scope_id=self.ids["B"]))
        self.catalog.append(dict(channel_id=2, channel_name="two", tag_value="B"))
        scopes.refresh_scopes()
        self.assertIn(self.ids["B"], [r["id"] for r in scopes.list_scopes(False)])
        self.assertEqual(scopes.channel_filter(self.ids["B"], False), [2])
        self.assertEqual(set(scopes.channel_filter(2, False)), {0, None, 99})
        self.assertIsNone(scopes.channel_filter(1, False))

    def test_independent_alerts_recovery_and_disabled_live_balance(self):
        now = datetime.now(balance.TZ)
        first = now.date().replace(day=1)
        a, b = self.ids["A"], self.ids["B"]
        with self.connect() as conn:
            conn.execute(
                "UPDATE balance_settings SET budget=5,threshold=1,enabled=true,start_month=%s",
                (first,),
            )
        rows = {
            first: [
                dict(channel_id=1, amount=Decimal(10)),
                dict(channel_id=2, amount=Decimal(20)),
            ]
        }
        with patch.object(balance, "source_channel_amounts", return_value=rows):
            balance.check_once(now, daily=False, scope_id=a)
            balance.check_once(now, daily=False, scope_id=b)
            with self.connect() as conn:
                self.assertEqual(
                    conn.execute("SELECT count(*) n FROM balance_alerts").fetchone()[
                        "n"
                    ],
                    2,
                )
                conn.execute(
                    "UPDATE balance_settings SET budget=100 WHERE scope_id=%s", (a,)
                )
            balance.check_once(now, daily=False, scope_id=a)
            with self.connect() as conn:
                self.assertEqual(
                    conn.execute("SELECT scope_id FROM balance_alerts").fetchall(),
                    [dict(scope_id=b)],
                )
                conn.execute(
                    "UPDATE balance_settings SET enabled=false WHERE scope_id=%s", (b,)
                )
            self.assertIsNone(balance.check_once(now, daily=False, scope_id=b))
            snap = balance.snapshot(live=True, scope_id=b)
            self.assertEqual(snap["state"]["current_amount"], 20)
            self.assertEqual(snap["state"]["remaining"], -15)

    def test_first_backfill_preserves_totals_unknown_deleted_ungrouped(self):
        month = self.write_month()
        with self.connect() as conn:
            conn.execute(
                "UPDATE balance_month_channels SET scope_id=NULL,tag_value=NULL"
            )
            conn.execute("UPDATE balance_scope_backfill SET completed=false")
            conn.execute("DELETE FROM balance_month_scopes WHERE scope_id<>1")
        scopes.refresh_scopes()
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT channel_id,scope_id FROM balance_month_channels WHERE month=%s",
                (month,),
            ).fetchall()
            self.assertEqual(
                {r["channel_id"]: r["scope_id"] for r in rows},
                {1: self.ids["A"], 2: self.ids["B"], 0: 2, None: 2, 99: 2},
            )
            self.assertEqual(
                conn.execute(
                    "SELECT amount FROM balance_month_scopes WHERE month=%s AND scope_id=1",
                    (month,),
                ).fetchone()["amount"],
                42,
            )
