"""Encrypted channel settings and delivery failures in a disposable schema."""

import os
from datetime import datetime
from unittest.mock import patch

from cryptography.fernet import Fernet

from database import MonitorTestCase
from new_api_statistics import balance, notifications
from new_api_statistics.notification_channels import feishu_app, dingtalk_webhook
from new_api_statistics.notification_channels.base import DeliveryError

NOTIFY = notifications.notify_safely


class NotificationDatabaseTest(MonitorTestCase):
    def setUp(self):
        super().setUp()
        self.enterContext(
            patch.dict(
                os.environ, NOTIFICATION_ENCRYPTION_KEY=Fernet.generate_key().decode()
            )
        )
        self.enterContext(
            patch.object(
                notifications, "load_site_name", return_value="Fixture gateway"
            )
        )

    def test_separate_encrypted_settings_secret_retention_and_optimistic_version(self):
        config = dict(
            version=1,
            enabled=True,
            channel="feishu_app",
            app_id="cli_fixture",
            app_secret="fixture-secret",
            receive_id_type="chat_id",
            receive_id="oc_fixture_chat",
        )
        notifications.save(config, "fixture-admin")
        snapshot = notifications.snapshot()
        self.assertTrue(snapshot["secret_configured"])
        self.assertNotIn("secret_encrypted", snapshot)
        with self.connect() as conn:
            encrypted = conn.execute(
                "SELECT secret_encrypted FROM notification_feishu_settings"
            ).fetchone()["secret_encrypted"]
        self.assertNotEqual(encrypted, config["app_secret"])
        self.assertEqual(notifications.decrypt(encrypted), config["app_secret"])
        notifications.save(dict(config, version=2, app_secret=""), "fixture-admin")
        with patch.object(feishu_app, "send") as send:
            notifications.deliver(test=True, expected_version=3)
            self.assertEqual(send.call_args.args[1], "fixture-secret")
        with self.assertRaises(balance.SettingsConflict):
            notifications.save(config, "fixture-admin")
        with self.assertRaises(ValueError):
            notifications.save(
                dict(config, version=3, app_id="cli_changed", app_secret=""),
                "fixture-admin",
            )
        webhook = "https://oapi.dingtalk.com/robot/send?access_token=fixture-token"
        notifications.save(
            dict(
                version=3,
                enabled=True,
                channel="dingtalk_webhook",
                webhook_url=webhook,
                signing_enabled=True,
                signing_secret="fixture-signing-secret",
            ),
            "fixture-admin",
        )
        with self.connect() as conn:
            stored = conn.execute(
                "SELECT * FROM notification_dingtalk_webhook_settings"
            ).fetchone()
        self.assertEqual(notifications.decrypt(stored["webhook_encrypted"]), webhook)
        with patch.object(dingtalk_webhook, "send") as send:
            notifications.deliver(test=True, expected_version=4)
            self.assertEqual(send.call_args.args[0]["webhook_url"], webhook)
            self.assertEqual(send.call_args.args[1], "fixture-signing-secret")

    def test_notification_failure_cannot_rollback_balance_or_alert(self):
        config = dict(
            version=1,
            enabled=True,
            channel="feishu_app",
            app_id="cli_fixture",
            app_secret="fixture-secret",
            receive_id_type="chat_id",
            receive_id="oc_fixture_chat",
        )
        notifications.save(config, "fixture-admin")
        self.notify.side_effect = NOTIFY
        with self.connect() as conn:
            conn.execute(
                "UPDATE balance_settings SET budget=1,threshold=2,enabled=true"
            )
        now = datetime.now(balance.TZ)
        with patch.object(
            feishu_app, "send", side_effect=DeliveryError("fixture failure")
        ):
            balance.check_once(
                now, lambda months, now: {m: 3 for m in months}, daily=False
            )
        self.assertEqual(balance.snapshot()["state"]["remaining"], -2)
        self.assertEqual(len(balance.snapshot()["alerts"]), 1)
        self.assertEqual(notifications.snapshot()["last_error"], "fixture failure")
