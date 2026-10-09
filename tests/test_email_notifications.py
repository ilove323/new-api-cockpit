"""Email transport coverage: no credentials or messages leave the test process."""

import smtplib
import ssl
import unittest
from unittest.mock import patch

from new_api_cockpit.notification_channels import email_smtp as email
from new_api_cockpit.notification_channels.base import DeliveryError

CONFIG = {
    "smtp_host": "smtp.example.test",
    "smtp_port": 587,
    "smtp_security": "starttls",
    "auth_enabled": True,
    "username": "fixture-user",
    "from_address": "alerts@example.test",
    "from_name": "余额监控",
    "recipients": ["one@example.test", "two@example.test"],
}


class EmailNotificationTest(unittest.TestCase):
    def test_validation_and_disabled_drafts(self):
        email.validate(CONFIG, "fixture-secret")
        email.validate(dict(CONFIG, smtp_host="::1", auth_enabled=False), "")
        email.validate(
            dict(CONFIG, smtp_host="", from_address="", recipients=[], username=""),
            "",
            require_complete=False,
        )
        invalid = [
            {"smtp_host": "https://smtp.example.test"},
            {"smtp_host": "smtp.example.test:587"},
            {"smtp_host": "smtp.example.test\nEHLO other"},
            {"smtp_host": ""},
            {"smtp_port": True},
            {"smtp_port": 0},
            {"smtp_port": 65536},
            {"smtp_port": "587"},
            {"smtp_security": "auto"},
            {"smtp_security": []},
            {"auth_enabled": "true"},
            {"username": ""},
            {"username": "user\r\nAUTH other"},
            {"from_address": "Name <a@example.test>"},
            {"from_address": ""},
            {"from_name": "monitor\r\nBcc: private@example.test"},
            {"recipients": []},
            {"recipients": "one@example.test"},
            {"recipients": ["one@example.test\r\nBcc: other@example.test"]},
            {"recipients": ["not-an-email"]},
            {"recipients": [None]},
            {"recipients": ["one@example.test"] * 101},
        ]
        for changes in invalid:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                email.validate(dict(CONFIG, **changes), "fixture-secret")
        with self.assertRaises(ValueError):
            email.validate(CONFIG, "")

    def test_modes_auth_and_utf8_message(self):
        for mode, port in [("smtp", 25), ("starttls", 587), ("smtps", 465)]:
            for auth in (False, True):
                with (
                    self.subTest(mode=mode, auth=auth),
                    patch.object(email.smtplib, "SMTP") as plain,
                    patch.object(email.smtplib, "SMTP_SSL") as secure,
                ):
                    factory = secure if mode == "smtps" else plain
                    client = factory.return_value.__enter__.return_value
                    client.send_message.return_value = {}
                    config = dict(
                        CONFIG, smtp_security=mode, smtp_port=port, auth_enabled=auth
                    )
                    email.send(
                        config,
                        "fixture-secret",
                        "【余额不足报警】\n站点：测试站点\n剩余额度：¥1.20",
                    )
                    self.assertEqual(
                        factory.call_args.args, (config["smtp_host"], port)
                    )
                    self.assertEqual(factory.call_args.kwargs["timeout"], email.TIMEOUT)
                    (plain if mode == "smtps" else secure).assert_not_called()
                    if mode == "smtps":
                        context = factory.call_args.kwargs["context"]
                        self.assertTrue(context.check_hostname)
                        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
                    if mode == "starttls":
                        client.starttls.assert_called_once()
                        context = client.starttls.call_args.kwargs["context"]
                        self.assertTrue(context.check_hostname)
                        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
                        names = [call[0] for call in client.mock_calls]
                        self.assertLess(
                            names.index("starttls"), names.index("send_message")
                        )
                        if auth:
                            self.assertLess(
                                names.index("starttls"), names.index("login")
                            )
                        self.assertEqual(client.ehlo.call_count, 2)
                    else:
                        client.starttls.assert_not_called()
                    if auth:
                        client.login.assert_called_once_with(
                            "fixture-user", "fixture-secret"
                        )
                    else:
                        client.login.assert_not_called()
                    message = client.send_message.call_args.args[0]
                    self.assertEqual(message["Subject"], "【余额不足报警】")
                    self.assertIn("余额监控", str(message["From"]))
                    self.assertIn("测试站点", message.get_content())
                    self.assertNotIn("fixture-secret", message.as_string())
                    self.assertEqual(
                        client.send_message.call_args.kwargs["to_addrs"],
                        config["recipients"],
                    )

    def test_errors_are_sanitized_and_never_retry_or_downgrade(self):
        for error in (
            smtplib.SMTPAuthenticationError(535, b"fixture-secret"),
            smtplib.SMTPNotSupportedError("fixture-secret"),
            ssl.SSLError("fixture-secret"),
            smtplib.SMTPRecipientsRefused(
                {"private@example.test": (550, b"fixture-secret")}
            ),
            TimeoutError("fixture-secret"),
        ):
            with (
                self.subTest(error=type(error).__name__),
                patch.object(email.smtplib, "SMTP") as smtp,
                patch.object(email.smtplib, "SMTP_SSL") as secure,
            ):
                smtp.return_value.__enter__.return_value.starttls.side_effect = error
                with self.assertRaises(DeliveryError) as caught:
                    email.send(CONFIG, "fixture-secret", "test")
                self.assertNotIn("fixture-secret", str(caught.exception))
                self.assertNotIn("private@example.test", str(caught.exception))
                self.assertEqual(smtp.call_count, 1)
                secure.assert_not_called()
                smtp.return_value.__enter__.return_value.send_message.assert_not_called()

    def test_partial_refusal_is_not_reported_as_full_success_or_retried(self):
        with patch.object(email.smtplib, "SMTP") as smtp:
            client = smtp.return_value.__enter__.return_value
            client.send_message.return_value = {
                "private@example.test": (550, b"fixture-secret")
            }
            with self.assertRaises(DeliveryError) as caught:
                email.send(
                    dict(CONFIG, recipients=CONFIG["recipients"] * 2),
                    "fixture-secret",
                    "test",
                )
            self.assertIn("可能已送达", str(caught.exception))
            self.assertNotIn("private@example.test", str(caught.exception))
            self.assertNotIn("fixture-secret", str(caught.exception))
            client.send_message.assert_called_once()
            self.assertEqual(
                client.send_message.call_args.kwargs["to_addrs"], CONFIG["recipients"]
            )
