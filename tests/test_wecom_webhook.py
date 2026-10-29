"""Offline WeCom contract, credential safety and saved settings; no real messages."""

import io
import json
import os
import unittest
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError

from cryptography.fernet import Fernet

from new_api_cockpit import notifications
from new_api_cockpit.notification_channels import wecom_webhook as wecom
from new_api_cockpit.notification_channels.base import DeliveryError

WEBHOOK = "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=fixture-robot-key"
CONFIG = dict(
    webhook_url=WEBHOOK, mentioned_list=["ops.user", "@all"], mentioned_mobile_list=[]
)


class Response(io.BytesIO):
    def __init__(self, raw, code=200):
        super().__init__(raw)
        self.code = code


class WecomWebhookTest(unittest.TestCase):
    def test_only_official_https_endpoint_with_one_key_is_accepted(self):
        wecom.validate(CONFIG)
        wecom.validate(dict(CONFIG, webhook_url=WEBHOOK.replace(".com/", ".com:443/")))
        invalid = [
            "",
            None,
            42,
            "x" * 2049,
            WEBHOOK.replace("https:", "http:"),
            WEBHOOK.replace("qyapi.weixin.qq.com", "127.0.0.1"),
            WEBHOOK.replace("qyapi.weixin.qq.com", "qyapi.weixin.qq.com.example.test"),
            WEBHOOK.replace(".com/", ".com:444/"),
            WEBHOOK.replace(".com/", ".com:bad/"),
            WEBHOOK.replace("/webhook/send", "/webhook/upload_media"),
            WEBHOOK + "#secret",
            WEBHOOK + "&key=other",
            WEBHOOK + "&extra=x",
            WEBHOOK.replace("?key=", "?access_token="),
            WEBHOOK.split("?")[0],
            WEBHOOK.split("=")[0] + "=",
            WEBHOOK.replace("https://", "https://user:pass@"),
            WEBHOOK.replace("https://", "https://@"),
            WEBHOOK + "\r\n",
            WEBHOOK + "%0A",
            WEBHOOK + "&",
            WEBHOOK.replace("key=", "key=one;key="),
        ]
        for url in invalid:
            with self.subTest(url=url), self.assertRaises(ValueError):
                wecom.validate(dict(CONFIG, webhook_url=url))

    def test_optional_mentions_are_bounded_deduplicated_and_not_display_names(self):
        self.assertEqual(wecom.mentions(["ops", "ops", "@all"]), ["ops", "@all"])
        self.assertEqual(
            wecom.mentions(["+8613800000000"], mobile=True), ["+8613800000000"]
        )
        for value in (
            None,
            "ops",
            [True],
            [""],
            ["王五"],
            ["has space"],
            ["a" * 65],
            ["a"] * 101,
        ):
            with self.subTest(value=value), self.assertRaises(ValueError):
                wecom.mentions(value)
        for value in (["ops"], ["1234"], ["1" * 21], ["<@all>"]):
            with self.subTest(value=value), self.assertRaises(ValueError):
                wecom.mentions(value, mobile=True)

    def test_text_and_mentions_use_the_robot_payload(self):
        config = dict(
            CONFIG,
            mentioned_list=["ops.user", "ops.user"],
            mentioned_mobile_list=["13800000000"],
        )
        with patch.object(wecom, "_post") as post:
            wecom.send(config, "", "余额不足\n账本：研发")
        post.assert_called_once_with(
            WEBHOOK,
            {
                "msgtype": "text",
                "text": {
                    "content": "余额不足\n账本：研发",
                    "mentioned_list": ["ops.user"],
                    "mentioned_mobile_list": ["13800000000"],
                },
            },
        )
        with patch.object(wecom, "_post") as post:
            wecom.send({"webhook_url": WEBHOOK}, "", "测试")
        post.assert_called_once_with(
            WEBHOOK, {"msgtype": "text", "text": {"content": "测试"}}
        )

    def test_utf8_limit_does_not_truncate_or_split_financial_message(self):
        with patch.object(wecom, "_post") as post:
            wecom.send(CONFIG, "", "中" * 682 + "ab")
            self.assertEqual(
                len(post.call_args.args[1]["text"]["content"].encode()), 2048
            )
            post.reset_mock()
            for text in ("中" * 683, "", None):
                with (
                    self.subTest(text_type=type(text).__name__),
                    self.assertRaises(DeliveryError),
                ):
                    wecom.send(CONFIG, "", text)
            post.assert_not_called()

    def test_http_transport_disables_redirects_and_environment_proxies(self):
        with patch.object(wecom.request, "build_opener") as opener:
            opener.return_value.open.return_value = Response(
                b'{"errcode":0,"errmsg":"ok"}'
            )
            wecom._post(WEBHOOK, {"msgtype": "text", "text": {"content": "测试"}})
            args = opener.call_args.args
            self.assertEqual(args[0].proxies, {})
            self.assertIsInstance(args[1], wecom.NoRedirect)
            request = opener.return_value.open.call_args.args[0]
            self.assertEqual(request.get_method(), "POST")
            self.assertEqual(json.loads(request.data)["text"]["content"], "测试")
            self.assertEqual(opener.return_value.open.call_args.kwargs["timeout"], 8)
        self.assertIsNone(
            wecom.NoRedirect().redirect_request(
                None, None, 302, None, None, "https://example.test"
            )
        )

    def test_business_code_must_be_an_explicit_integer_zero(self):
        for code in (None, False, True, 0.0, "0", "fixture-robot-key", 93000):
            with (
                self.subTest(code=code),
                patch.object(wecom.request, "build_opener") as opener,
            ):
                opener.return_value.open.return_value = Response(
                    json.dumps({"errcode": code, "errmsg": WEBHOOK}).encode()
                )
                with self.assertRaises(DeliveryError) as caught:
                    wecom._post(WEBHOOK, {})
                self.assertNotIn("fixture-robot-key", str(caught.exception))
                self.assertNotIn("https://", str(caught.exception))
                opener.return_value.open.assert_called_once()

    def test_http_errors_invalid_json_and_oversized_responses_never_succeed_or_leak(
        self,
    ):
        for status, raw in (
            ("fixture-robot-key", b'{"errcode":0}'),
            (302, b'{"errcode":0}'),
            (429, b'{"errcode":45009}'),
            (502, b'{"errcode":0}'),
            (200, b"{}"),
            (200, b"[]"),
            (200, b"<html>fixture-robot-key</html>"),
            (200, b"x" * 65537),
        ):
            with (
                self.subTest(status=status, size=len(raw)),
                patch.object(wecom.request, "build_opener") as opener,
            ):
                opener.return_value.open.return_value = Response(raw, status)
                with self.assertRaises(DeliveryError) as caught:
                    wecom._post(WEBHOOK, {})
                self.assertNotIn("fixture-robot-key", str(caught.exception))
                opener.return_value.open.assert_called_once()
        with patch.object(wecom.request, "build_opener") as opener:
            opener.return_value.open.side_effect = HTTPError(
                WEBHOOK,
                400,
                "secret",
                {},
                io.BytesIO(b'{"errcode":93000,"errmsg":"fixture-robot-key"}'),
            )
            with self.assertRaisesRegex(DeliveryError, "93000") as caught:
                wecom._post(WEBHOOK, {})
            self.assertNotIn("fixture-robot-key", str(caught.exception))

    def test_network_failure_does_not_expose_url_or_retry(self):
        with patch.object(wecom.request, "build_opener") as opener:
            opener.return_value.open.side_effect = RuntimeError(WEBHOOK)
            with self.assertRaises(DeliveryError) as caught:
                wecom._post(WEBHOOK, {})
            self.assertNotIn("fixture-robot-key", str(caught.exception))
            opener.return_value.open.assert_called_once()

    def test_saved_webhook_is_encrypted_retained_and_not_returned(self):
        conn = MagicMock()
        with patch.dict(
            os.environ, NOTIFICATION_ENCRYPTION_KEY=Fernet.generate_key().decode()
        ):
            notifications.save_wecom(
                conn, {**CONFIG, "enabled": True}, {"webhook_encrypted": ""}
            )
            encrypted, members, mobiles = conn.execute.call_args.args[1]
            self.assertNotIn("fixture-robot-key", encrypted)
            self.assertEqual(notifications.decrypt(encrypted), WEBHOOK)
            notifications.save_wecom(
                conn,
                {"webhook_url": "", "enabled": True},
                {"webhook_encrypted": encrypted},
            )
            self.assertEqual(conn.execute.call_args.args[1][0], encrypted)
            row = dict(
                channel="wecom_webhook",
                version=2,
                enabled=True,
                webhook_encrypted=encrypted,
                mentioned_list=CONFIG["mentioned_list"],
                mentioned_mobile_list=[],
                last_error=None,
                last_attempt_at=None,
                last_success_at=None,
            )
            public = notifications.public(row)
            self.assertTrue(public["webhook_configured"])
            self.assertEqual(public["mentioned_list"], CONFIG["mentioned_list"])
            self.assertNotIn("webhook_encrypted", public)
            self.assertNotIn("fixture-robot-key", json.dumps(public))
            self.assertNotIn("signing_enabled", public)

    def test_disabled_empty_channel_needs_no_secret_and_bad_settings_do_not_write(self):
        conn = MagicMock()
        with patch.object(notifications, "cipher") as cipher:
            notifications.save_wecom(
                conn, {"enabled": False}, {"webhook_encrypted": ""}
            )
            cipher.assert_not_called()
        for body in (
            {"enabled": True},
            {"enabled": False, "webhook_url": "https://example.test"},
            {"enabled": False, "mentioned_list": "@all"},
        ):
            conn.reset_mock()
            with self.subTest(body=body), self.assertRaises(ValueError):
                notifications.save_wecom(conn, body, {"webhook_encrypted": ""})
            conn.execute.assert_not_called()
