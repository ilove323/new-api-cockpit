"""Ledger integration regressions against an isolated, disposable schema."""

from datetime import date, datetime, timedelta
from decimal import Decimal
from unittest.mock import patch

from database import MonitorTestCase
from new_api_statistics import balance

CHANNEL_SOURCE = balance.source_channel_amounts


class BalanceDatabaseTest(MonitorTestCase):
    def setUp(self):
        super().setUp()
        self.now = datetime.now(balance.TZ).replace(day=10, hour=10)
        self.current = self.now.date().replace(day=1)
        self.previous = (self.current - timedelta(days=1)).replace(day=1)
        self.first = (self.previous - timedelta(days=1)).replace(day=1)
        self.body = dict(
            budget="100",
            threshold="10",
            enabled=True,
            start_month=self.first.strftime("%Y-%m"),
            version=1,
        )
        self.calls = []
        self.enterContext(
            patch.object(balance, "source_channel_amounts", self.channels)
        )
        balance.save_settings(self.body, "fixture-admin")

    def channels(self, months, _now):
        return {
            m: [dict(channel_id=1, channel_name="fixture", amount=Decimal(45))]
            for m in months
        }

    def source(self, months, _now):
        self.calls.append(months)
        return {m: Decimal(45) for m in months}

    def test_daily_dedup_live_month_and_rollover(self):
        balance.check_once(self.now, self.source)
        balance.check_once(self.now, self.source)
        self.assertEqual(self.calls, [[self.current]])
        with self.connect() as conn:
            state = conn.execute("SELECT * FROM balance_state WHERE id=1").fetchone()
            self.assertEqual((state["archived_amount"], state["remaining"]), (90, -35))
            self.assertEqual(
                conn.execute("SELECT count(*) AS n FROM balance_alerts").fetchone()[
                    "n"
                ],
                1,
            )
        with patch.object(balance, "source_amounts", self.source):
            live = balance.snapshot(live=True)
        self.assertEqual(self.calls, [[self.current], [self.current]])
        self.assertEqual(live["state"]["remaining"], -35)
        rollover = datetime.combine(
            balance.next_month(self.current), datetime.min.time(), tzinfo=balance.TZ
        )
        balance.check_once(rollover, self.source)
        self.assertEqual(
            self.calls[-1], [self.current, balance.next_month(self.current)]
        )
        with self.connect() as conn:
            self.assertEqual(
                conn.execute(
                    "SELECT count(*) AS n FROM balance_channel_archive_months"
                ).fetchone()["n"],
                3,
            )

    def test_alert_is_replaced_recovered_and_settings_races_fail_closed(self):
        balance.check_once(self.now, self.source, daily=False)
        with self.connect() as conn:
            alert_id = conn.execute("SELECT id FROM balance_alerts").fetchone()["id"]
        balance.check_once(self.now + timedelta(days=1), self.source, daily=False)
        with self.connect() as conn:
            self.assertEqual(
                conn.execute("SELECT id FROM balance_alerts").fetchone()["id"], alert_id
            )
        balance.save_settings(dict(self.body, budget="200", version=2), "fixture-admin")
        balance.check_once(self.now, self.source, daily=False)
        self.assertEqual(balance.snapshot()["alerts"], [])
        with self.assertRaises(balance.SettingsConflict):
            balance.save_settings(self.body, "fixture-admin")

        def race(months, now):
            with self.connect() as conn:
                conn.execute("UPDATE balance_settings SET version=version+1 WHERE id=1")
            return self.source(months, now)

        with self.assertRaises(balance.CheckBusy):
            balance.check_once(self.now, race, daily=False)
        with self.connect() as conn:
            self.assertEqual(
                conn.execute("SELECT version FROM balance_state WHERE id=1").fetchone()[
                    "version"
                ],
                3,
            )
        balance.record_failure()
        self.assertTrue(balance.snapshot()["stale"])

    def test_history_preview_confirm_and_missing_data_preserve_archive(self):
        body = dict(self.body, version=2)
        details = {
            m: [dict(channel_id=1, channel_name="fixture", amount=Decimal(46))]
            for m in (self.first, self.previous)
        }
        with patch.object(balance, "source_channel_amounts", return_value=details):
            preview = balance.history_preview(body, self.now)
            self.assertEqual(
                [(r["before"], r["after"]) for r in preview["rows"]],
                [("45.000000", "46")] * 2,
            )
            bad_preview = [dict(r, after="47") for r in preview["rows"]]
            with self.assertRaises(balance.HistoryPreviewChanged):
                balance.recalculate_history(
                    body, bad_preview, "fixture-admin", self.now
                )
            result = balance.recalculate_history(
                body, preview["rows"], "fixture-admin", self.now
            )
            self.assertEqual(result, {"version": 3, "months": 2})
        with self.connect() as conn:
            before = conn.execute(
                "SELECT * FROM balance_month_channels ORDER BY month"
            ).fetchall()
        with patch.object(balance, "source_channel_amounts", return_value={}):
            with self.assertRaises(balance.ArchiveDataMissing):
                balance.history_preview(dict(body, version=3), self.now)
        with self.connect() as conn:
            self.assertEqual(
                before,
                conn.execute(
                    "SELECT * FROM balance_month_channels ORDER BY month"
                ).fetchall(),
            )
            self.assertEqual(
                conn.execute(
                    "SELECT version FROM balance_settings WHERE id=1"
                ).fetchone()["version"],
                3,
            )

    def test_source_sql_half_open_months_and_channel_aggregation(self):
        with self.connect() as conn:
            conn.execute(
                "CREATE TABLE logs(created_at bigint,type integer,quota bigint,channel_id bigint,channel_name text)"
            )
            for when, kind, units, channel in (
                (datetime(2025, 12, 31, 23, 59, 59, tzinfo=balance.TZ), 2, 500000, 1),
                (datetime(2026, 1, 1, tzinfo=balance.TZ), 2, 1000000, 1),
                (datetime(2026, 1, 1, tzinfo=balance.TZ), 1, 9000000, 1),
                (datetime(2026, 1, 2, tzinfo=balance.TZ), 2, 5000000, 2),
                (datetime(2026, 1, 3, tzinfo=balance.TZ), 2, 1500000, None),
                (datetime(2026, 2, 1, tzinfo=balance.TZ), 2, 1500000, 1),
            ):
                conn.execute(
                    "INSERT INTO logs VALUES (%s,%s,%s,%s,'fixture')",
                    (int(when.timestamp()), kind, units, channel),
                )
        end = datetime(2026, 2, 1, tzinfo=balance.TZ)
        months = [date(2025, 12, 1), date(2026, 1, 1), date(2026, 2, 1)]
        with (
            patch.object(balance.psycopg, "connect", self.connect),
            patch.object(balance, "source_channel_amounts", CHANNEL_SOURCE),
        ):
            self.assertEqual(
                balance.source_amounts(months, end), dict(zip(months, (1, 15, 3)))
            )
            rows = balance.source_channel_amounts([months[1]], end)[months[1]]
        self.assertEqual(
            {r["channel_id"]: r["amount"] for r in rows}, {1: 2, 2: 10, None: 3}
        )
