"""Fresh and versioned monitoring upgrades; data must survive repeat startup."""

import os
from pathlib import Path
import unittest
from unittest.mock import patch

from database import isolated_schema
from new_api_cockpit import balance

MIGRATIONS = sorted(Path(balance.__file__).with_name("migrations").glob("[0-9]*.sql"))


@unittest.skipUnless(
    os.environ.get("MONITOR_DATABASE_URL"), "Requires disposable PostgreSQL"
)
class MigrationTest(unittest.TestCase):
    def apply_prefix(self, connect, count):
        with connect() as conn:
            conn.execute(
                "CREATE TABLE schema_migrations(version text PRIMARY KEY,applied_at timestamptz NOT NULL DEFAULT now())"
            )
            for file in MIGRATIONS[:count]:
                conn.execute(file.read_text())
                conn.execute(
                    "INSERT INTO schema_migrations(version) VALUES(%s)", (file.name,)
                )
            conn.execute(
                "INSERT INTO balance_settings(id,start_month,budget) VALUES(1,'2026-01-01',1234)"
            )
            conn.execute(
                "INSERT INTO balance_months(month,amount) VALUES('2026-01-01',12.345678)"
            )

    def verify(self, connect):
        with patch.object(balance, "connect", connect):
            balance.initialize()
            balance.initialize()
            balance.require_schema()
        with connect() as conn:
            self.assertEqual(
                {
                    r["version"]
                    for r in conn.execute("SELECT version FROM schema_migrations")
                },
                {f.name for f in MIGRATIONS},
            )
            self.assertEqual(
                conn.execute(
                    "SELECT budget FROM balance_settings WHERE id=1"
                ).fetchone()["budget"],
                1234,
            )

    def test_fresh_schema_is_idempotent(self):
        with isolated_schema() as connect, patch.object(balance, "connect", connect):
            balance.initialize()
            with connect() as conn:
                conn.execute("UPDATE balance_settings SET budget=1234 WHERE id=1")
            self.verify(connect)

    def test_each_recorded_prefix_upgrades_without_reset(self):
        for count in range(1, len(MIGRATIONS)):
            with (
                self.subTest(last=MIGRATIONS[count - 1].name),
                isolated_schema() as connect,
            ):
                self.apply_prefix(connect, count)
                self.verify(connect)
                with connect() as conn:
                    self.assertEqual(
                        str(
                            conn.execute(
                                "SELECT amount FROM balance_months"
                            ).fetchone()["amount"]
                        ),
                        "12.345678",
                    )

    def test_old_dingtalk_credentials_are_not_misused_as_a_webhook(self):
        with isolated_schema() as connect:
            self.apply_prefix(connect, 3)
            with connect() as conn:
                conn.execute(
                    "CREATE TABLE notification_dingtalk_settings(id integer PRIMARY KEY,secret_encrypted text)"
                )
                conn.execute(
                    "UPDATE notification_settings SET channel='dingtalk_app',enabled=true WHERE id=1"
                )
            self.verify(connect)
            with connect() as conn:
                config = conn.execute(
                    "SELECT * FROM notification_settings WHERE channel='dingtalk_webhook'"
                ).fetchone()
                self.assertEqual(config["channel"], "dingtalk_webhook")
                self.assertFalse(config["enabled"])
                self.assertIsNone(
                    conn.execute(
                        "SELECT to_regclass('notification_dingtalk_settings') AS name"
                    ).fetchone()["name"]
                )

    def test_independent_notifications_preserve_existing_selection_and_credentials(
        self,
    ):
        for selected, enabled in [
            ("feishu_app", True),
            ("dingtalk_webhook", True),
            ("feishu_app", False),
        ]:
            with (
                self.subTest(channel=selected, enabled=enabled),
                isolated_schema() as connect,
            ):
                self.apply_prefix(connect, 10)
                with connect() as conn:
                    conn.execute(
                        """UPDATE notification_settings SET channel=%s,enabled=%s,version=9,
                        updated_by='fixture-admin',last_error='fixture error',last_attempt_at=now() WHERE id=1""",
                        (selected, enabled),
                    )
                    original = conn.execute(
                        "SELECT * FROM notification_settings"
                    ).fetchone()
                    conn.execute(
                        "UPDATE notification_feishu_settings SET secret_encrypted='fixture-cipher-feishu'"
                    )
                    conn.execute(
                        "UPDATE notification_dingtalk_webhook_settings SET webhook_encrypted='fixture-cipher-webhook'"
                    )
                self.verify(connect)
                with connect() as conn:
                    # The new migration is safe to repeat without resetting saved channels.
                    conn.execute(MIGRATIONS[-1].read_text())
                    rows = {
                        r["channel"]: r
                        for r in conn.execute("SELECT * FROM notification_settings")
                    }
                    self.assertEqual(
                        set(rows), {"feishu_app", "dingtalk_webhook", "email"}
                    )
                    for key, value in original.items():
                        if key != "id":
                            self.assertEqual(rows[selected][key], value)
                    for name in rows.keys() - {selected}:
                        self.assertFalse(rows[name]["enabled"])
                        self.assertIsNone(rows[name]["last_attempt_at"])
                        self.assertEqual(rows[name]["version"], 1)
                    self.assertEqual(
                        conn.execute(
                            "SELECT secret_encrypted FROM notification_feishu_settings"
                        ).fetchone()["secret_encrypted"],
                        "fixture-cipher-feishu",
                    )
                    self.assertEqual(
                        conn.execute(
                            "SELECT webhook_encrypted FROM notification_dingtalk_webhook_settings"
                        ).fetchone()["webhook_encrypted"],
                        "fixture-cipher-webhook",
                    )
