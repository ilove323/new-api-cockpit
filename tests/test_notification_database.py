"""Encrypted channel settings and delivery failures in a disposable schema."""

import os
from datetime import datetime
from unittest.mock import patch

from cryptography.fernet import Fernet

from database import MonitorTestCase
from new_api_cockpit import balance, notifications
from new_api_cockpit.notification_channels import (
    feishu_app,
    dingtalk_webhook,
    email_smtp,
    wecom_webhook,
)
from new_api_cockpit.notification_channels.base import DeliveryError
from new_api_cockpit.app import app
from session_fixture import fixture_identity, session_auth

NOTIFY = notifications.notify_safely
EMAIL = dict(
    version=1,
    enabled=True,
    channel="email",
    smtp_host="smtp.example.test",
    smtp_port=587,
    smtp_security="starttls",
    auth_enabled=False,
    username="",
    password="",
    from_address="alerts@example.test",
    from_name="监控",
    recipients=["one@example.test", "two@example.test"],
)
FEISHU = dict(
    version=1,
    enabled=True,
    channel="feishu_app",
    app_id="cli_fixture",
    app_secret="fixture-secret",
    receive_id_type="chat_id",
    receive_id="oc_fixture_chat",
)
DINGTALK = dict(
    version=1,
    enabled=True,
    channel="dingtalk_webhook",
    webhook_url="https://oapi.dingtalk.com/robot/send?access_token=fixture-token",
    signing_enabled=False,
    signing_secret="",
)
WECOM = dict(
    version=1,
    enabled=True,
    channel="wecom_webhook",
    webhook_url="https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=fixture-robot-key",
    mentioned_list=["ops_user"],
    mentioned_mobile_list=[],
)


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
            notifications.deliver(test=True, expected_version=3, channel="feishu_app")
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
                version=1,
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
            notifications.deliver(
                test=True, expected_version=2, channel="dingtalk_webhook"
            )
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

    def test_channel_switches_versions_and_credentials_are_independent(self):
        notifications.save(FEISHU, "fixture-admin")
        notifications.save(DINGTALK, "fixture-admin")
        notifications.save(EMAIL, "fixture-admin")
        notifications.save(WECOM, "fixture-admin")
        for name in notifications.CHANNELS:
            state = notifications.snapshot(name)
            self.assertTrue(state["enabled"])
            self.assertEqual(state["version"], 2)
            self.assertNotIn("active_channel", state)
        notifications.save(
            dict(FEISHU, version=2, enabled=False, app_secret=""), "fixture-admin"
        )
        self.assertFalse(notifications.snapshot("feishu_app")["enabled"])
        self.assertTrue(notifications.snapshot("dingtalk_webhook")["enabled"])
        self.assertTrue(notifications.snapshot("email")["enabled"])
        self.assertEqual(notifications.snapshot("email")["version"], 2)
        self.assertTrue(notifications.snapshot("feishu_app")["secret_configured"])

    def test_email_encryption_retention_and_destination_change(self):
        config = dict(
            EMAIL, auth_enabled=True, username="fixture-user", password="fixture-secret"
        )
        notifications.save(config, "fixture-admin")
        snapshot = notifications.snapshot("email")
        self.assertTrue(snapshot["password_configured"])
        self.assertNotIn("password", snapshot)
        self.assertNotIn("secret_encrypted", snapshot)
        with self.connect() as conn:
            encrypted = conn.execute(
                "SELECT secret_encrypted FROM notification_email_settings"
            ).fetchone()["secret_encrypted"]
        self.assertNotEqual(encrypted, config["password"])
        self.assertEqual(notifications.decrypt(encrypted), config["password"])
        notifications.save(
            dict(config, version=2, password="", from_name="changed"), "fixture-admin"
        )
        with patch.object(email_smtp, "send") as send:
            notifications.deliver(test=True, channel="email", expected_version=3)
            self.assertEqual(send.call_args.args[1], "fixture-secret")
        for changes in (
            {"smtp_host": "other.example.test"},
            {"smtp_port": 2525},
            {"username": "other-user"},
            {"smtp_security": "smtp"},
        ):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                notifications.save(
                    dict(config, version=3, password="", **changes), "fixture-admin"
                )
            self.assertEqual(notifications.snapshot("email")["version"], 3)
        notifications.save(
            dict(
                config,
                version=3,
                smtp_host="other.example.test",
                password="new-fixture-secret",
            ),
            "fixture-admin",
        )
        with self.assertRaises(balance.SettingsConflict):
            notifications.save(dict(config, version=3), "fixture-admin")

    def test_anonymous_delivery_does_not_decrypt_or_use_a_stored_password(self):
        notifications.save(EMAIL, "fixture-admin")
        with self.connect() as conn:
            conn.execute(
                "UPDATE notification_email_settings SET secret_encrypted='not-a-valid-ciphertext'"
            )
        with (
            patch.object(email_smtp, "send") as send,
            patch.object(notifications, "decrypt") as decrypt,
        ):
            notifications.deliver(test=True, channel="email", expected_version=2)
            decrypt.assert_not_called()
            self.assertEqual(send.call_args.args[1], "")

    def test_enabled_channel_fanout_continues_after_failure_and_keeps_separate_status(
        self,
    ):
        for config in (FEISHU, DINGTALK, EMAIL, WECOM):
            notifications.save(config, "fixture-admin")
        with self.connect() as conn:
            conn.execute(
                "UPDATE balance_settings SET budget=1,threshold=2,enabled=true"
            )
        balance.check_once(
            datetime.now(balance.TZ),
            lambda months, now: {m: 3 for m in months},
            daily=False,
        )
        with (
            patch.object(feishu_app, "send") as feishu_send,
            patch.object(
                dingtalk_webhook, "send", side_effect=DeliveryError("fixture failure")
            ) as ding_send,
            patch.object(email_smtp, "send") as email_send,
            patch.object(wecom_webhook, "send") as wecom_send,
        ):
            notifications.deliver()
            for send in (feishu_send, ding_send, email_send, wecom_send):
                send.assert_called_once()
                self.assertIn("余额不足报警", send.call_args.args[2])
        self.assertEqual(
            notifications.snapshot("dingtalk_webhook")["last_error"], "fixture failure"
        )
        self.assertIsNone(notifications.snapshot("dingtalk_webhook")["last_success_at"])
        for name in ("feishu_app", "email", "wecom_webhook"):
            self.assertIsNone(notifications.snapshot(name)["last_error"])
            self.assertIsNotNone(notifications.snapshot(name)["last_success_at"])
        self.assertEqual(balance.snapshot()["state"]["remaining"], -2)
        with self.connect() as conn:
            conn.execute(
                "UPDATE notification_settings SET enabled=false WHERE channel='dingtalk_webhook'"
            )
            conn.execute("UPDATE balance_settings SET enabled=false")
        with (
            patch.object(feishu_app, "send") as feishu_send,
            patch.object(dingtalk_webhook, "send") as ding_send,
            patch.object(email_smtp, "send") as email_send,
            patch.object(wecom_webhook, "send") as wecom_send,
        ):
            notifications.deliver()
            for send in (feishu_send, ding_send, email_send, wecom_send):
                send.assert_not_called()

    def test_channel_api_returns_selected_provider_and_tests_only_it_even_if_disabled(
        self,
    ):
        client = app.test_client()
        url = "/cockpit/api/statistics/balance/channel"
        args = dict(
            auth=session_auth("fixture_admin"), headers={"X-Statistics-Request": "1"}
        )
        with patch(
            "new_api_cockpit.app.request_identity", side_effect=fixture_identity
        ):
            response = client.put(url, json=dict(EMAIL, enabled=False), **args)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json["channel"], "email")
            self.assertFalse(response.json["enabled"])
            with (
                patch.object(email_smtp, "send") as send,
                patch.object(feishu_app, "send") as feishu_send,
            ):
                self.assertEqual(
                    client.post(url + "/test", json={"version": 2}, **args).status_code,
                    400,
                )
                self.assertEqual(
                    client.post(
                        url + "/test", json={"channel": "email", "version": 1}, **args
                    ).status_code,
                    409,
                )
                send.assert_not_called()
                self.assertEqual(
                    client.post(
                        url + "/test", json={"channel": "email", "version": 2}, **args
                    ).status_code,
                    200,
                )
                send.assert_called_once()
                feishu_send.assert_not_called()
        self.assertIsNone(notifications.snapshot("feishu_app")["last_attempt_at"])

    def test_wecom_saved_config_version_disable_test_and_failure_isolation(self):
        self.assertFalse(notifications.snapshot("wecom_webhook")["enabled"])
        self.assertFalse(notifications.snapshot("wecom_webhook")["webhook_configured"])
        notifications.save(
            dict(WECOM, mentioned_list=["ops_user", "ops_user"]), "fixture-admin"
        )
        snapshot = notifications.snapshot("wecom_webhook")
        self.assertEqual(snapshot["mentioned_list"], ["ops_user"])
        self.assertEqual(snapshot["version"], 2)
        self.assertNotIn("webhook_url", snapshot)
        with self.connect() as conn:
            encrypted = conn.execute(
                "SELECT webhook_encrypted FROM notification_wecom_webhook_settings"
            ).fetchone()["webhook_encrypted"]
        self.assertEqual(notifications.decrypt(encrypted), WECOM["webhook_url"])
        self.assertNotIn("fixture-robot-key", encrypted)
        with self.assertRaises(balance.SettingsConflict):
            notifications.save(WECOM, "fixture-admin")
        for body in (
            dict(WECOM, version=2, webhook_url="https://127.0.0.1/?key=x"),
            dict(WECOM, version=2, mentioned_list="@all"),
        ):
            with self.assertRaises(ValueError):
                notifications.save(body, "fixture-admin")
            self.assertEqual(notifications.snapshot("wecom_webhook")["version"], 2)
        notifications.save(
            dict(
                WECOM,
                version=2,
                enabled=False,
                webhook_url="",
                mentioned_list=[],
                mentioned_mobile_list=["13800000000"],
            ),
            "fixture-admin",
        )
        self.assertFalse(notifications.snapshot("wecom_webhook")["enabled"])
        client = app.test_client()
        url = "/cockpit/api/statistics/balance/channel"
        args = dict(
            auth=session_auth("fixture_admin"), headers={"X-Statistics-Request": "1"}
        )
        with patch(
            "new_api_cockpit.app.request_identity", side_effect=fixture_identity
        ):
            response = client.get(url + "?channel=wecom_webhook", auth=args["auth"])
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.json["webhook_configured"])
            self.assertNotIn("fixture-robot-key", response.text)
            with patch.object(wecom_webhook, "send") as send:
                self.assertEqual(
                    client.post(
                        url + "/test",
                        json={"channel": "wecom_webhook", "version": 2},
                        **args,
                    ).status_code,
                    409,
                )
                send.assert_not_called()
                self.assertEqual(
                    client.post(
                        url + "/test",
                        json={"channel": "wecom_webhook", "version": 3},
                        **args,
                    ).status_code,
                    200,
                )
                send.assert_called_once()
                config, secret, text = send.call_args.args
                self.assertEqual(config["webhook_url"], WECOM["webhook_url"])
                self.assertEqual(config["mentioned_list"], [])
                self.assertEqual(config["mentioned_mobile_list"], ["13800000000"])
                self.assertEqual(secret, "")
                self.assertIn("Fixture gateway", text)
            with patch.object(wecom_webhook, "_post") as post:
                self.assertEqual(
                    client.post(
                        url + "/test",
                        json={"channel": "wecom_webhook", "version": 3},
                        **args,
                    ).status_code,
                    200,
                )
                post.assert_called_once()
                self.assertEqual(post.call_args.args[0], WECOM["webhook_url"])
                payload = post.call_args.args[1]
                self.assertEqual(payload["msgtype"], "text")
                self.assertEqual(
                    payload["text"]["mentioned_mobile_list"], ["13800000000"]
                )
                self.assertNotIn("mentioned_list", payload["text"])
            notifications.save(EMAIL, "fixture-admin")
            with patch.object(
                wecom_webhook,
                "send",
                side_effect=DeliveryError("fixture delivery failure"),
            ):
                self.assertEqual(
                    client.post(
                        url + "/test",
                        json={"channel": "wecom_webhook", "version": 3},
                        **args,
                    ).status_code,
                    400,
                )
        self.assertEqual(
            notifications.snapshot("wecom_webhook")["last_error"],
            "fixture delivery failure",
        )
        self.assertIsNone(notifications.snapshot("email")["last_attempt_at"])
