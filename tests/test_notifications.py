"""Run with unittest discovery; all notification requests are mocked, never sent."""

from session_fixture import fixture_identity, session_auth

import json
import io
import unittest
from datetime import datetime
from decimal import Decimal
from urllib.error import HTTPError
from unittest.mock import patch, MagicMock

from cryptography.fernet import Fernet
from new_api_cockpit import notifications
from new_api_cockpit import balance
from new_api_cockpit.notification_channels import feishu_app as feishu
from new_api_cockpit.notification_channels import dingtalk_webhook as dingtalk
from new_api_cockpit.notification_channels.base import DeliveryError
from new_api_cockpit.app import app
from new_api_cockpit.report import TZ


class NotificationsTest(unittest.TestCase):
    def setUp(self):
        feishu._tokens.clear()
        self.config = dict(
            app_id="cli_example", receive_id_type="chat_id", receive_id="oc_example"
        )

    def test_validation(self):
        feishu.validate(self.config, "fixture-secret")
        feishu.validate(
            dict(self.config, receive_id_type="user_id", receive_id="employee_123"),
            "fixture-secret",
        )
        for config in [
            dict(self.config, app_id="bad"),
            dict(self.config, receive_id="ou_wrong"),
            dict(self.config, receive_id_type="open_id", receive_id="ou_example"),
            dict(self.config, receive_id_type="user_id", receive_id="ou_example"),
            dict(self.config, receive_id_type="user_id", receive_id=""),
            dict(self.config, receive_id_type="chat_id&bad=1"),
        ]:
            with self.assertRaises(ValueError):
                feishu.validate(config, "fixture-secret")

    def test_send_and_token_cache(self):
        token = dict(code=0, tenant_access_token="fixture-token", expire=7200)
        with patch.object(
            feishu, "_post", side_effect=[token, dict(code=0), dict(code=0)]
        ) as post:
            feishu.send(self.config, "fixture-secret", "余额不足")
            feishu.send(self.config, "fixture-secret", "第二次")
            self.assertEqual(post.call_count, 3)
            path, body, auth = post.call_args_list[1].args
            self.assertEqual(path, "/im/v1/messages?receive_id_type=chat_id")
            self.assertEqual(auth, "fixture-token")
            self.assertEqual(json.loads(body["content"]), {"text": "余额不足"})

    def test_expired_token_refresh_same_uuid(self):
        token = dict(code=0, tenant_access_token="fixture-token", expire=7200)
        with patch.object(
            feishu,
            "_post",
            side_effect=[token, dict(code=99991663), token, dict(code=0)],
        ) as post:
            feishu.send(self.config, "fixture-secret", "test")
            self.assertEqual(
                post.call_args_list[1].args[1]["uuid"],
                post.call_args_list[3].args[1]["uuid"],
            )

    def test_personal_message_uses_user_id(self):
        token = dict(code=0, tenant_access_token="fixture-token", expire=7200)
        with patch.object(feishu, "_post", side_effect=[token, dict(code=0)]) as post:
            feishu.send(
                dict(self.config, receive_id_type="user_id", receive_id="employee123"),
                "fixture-secret",
                "test",
            )
            path, body, _ = post.call_args.args
            self.assertEqual(path, "/im/v1/messages?receive_id_type=user_id")
            self.assertEqual(body["receive_id"], "employee123")

    def test_response_error_is_sanitized(self):
        with self.assertRaises(DeliveryError) as error:
            feishu._check(dict(code=99991672, msg="do-not-expose-fixture-secret"))
        self.assertNotIn("fixture-secret", str(error.exception))
        self.assertIn("99991672", str(error.exception))
        with patch.object(feishu.request, "build_opener") as opener:
            opener.return_value.open.side_effect = RuntimeError("fixture-secret")
            with self.assertRaises(DeliveryError) as error:
                feishu._post("/example", {})
            self.assertNotIn("fixture-secret", str(error.exception))

    def test_encryption_and_public_allowlist(self):
        with patch.dict(
            "os.environ", NOTIFICATION_ENCRYPTION_KEY=Fernet.generate_key().decode()
        ):
            encrypted = notifications.cipher().encrypt(b"fixture-secret").decode()
            self.assertEqual(notifications.decrypt(encrypted), "fixture-secret")
            self.assertNotIn("fixture-secret", encrypted)
        row = dict(
            version=1,
            enabled=True,
            channel="feishu_app",
            active_channel="feishu_app",
            **self.config,
            last_attempt_at=None,
            last_success_at=None,
            last_error=None,
            secret_encrypted=encrypted,
            unexpected_secret="hidden",
        )
        output = notifications.public(row)
        self.assertTrue(output["secret_configured"])
        self.assertNotIn("secret_encrypted", output)
        self.assertNotIn("unexpected_secret", output)

        webhook = notifications.public(
            dict(
                version=2,
                enabled=False,
                channel="dingtalk_webhook",
                active_channel="dingtalk_webhook",
                webhook_encrypted="encrypted-webhook",
                secret_encrypted="encrypted-signing-secret",
                signing_enabled=True,
                last_attempt_at=None,
                last_success_at=None,
                last_error=None,
            )
        )
        self.assertTrue(webhook["webhook_configured"])
        self.assertTrue(webhook["signing_secret_configured"])
        self.assertNotIn("webhook_encrypted", webhook)
        self.assertNotIn("secret_encrypted", webhook)

    def test_dingtalk_validation_and_send(self):
        config = dict(
            webhook_url="https://oapi.dingtalk.com/robot/send?access_token=fixture-token",
            signing_enabled=True,
        )
        dingtalk.validate(config, "fixture-secret")
        for invalid in [
            dict(config, webhook_url=""),
            dict(config, webhook_url="https://example.com/robot/send?access_token=x"),
            dict(config, webhook_url="https://oapi.dingtalk.com/other?access_token=x"),
            dict(config, webhook_url="https://oapi.dingtalk.com/robot/send"),
            dict(config, signing_enabled="true"),
        ]:
            with self.assertRaises(ValueError):
                dingtalk.validate(invalid, "fixture-secret")
        with self.assertRaises(ValueError):
            dingtalk.validate(config, "")
        with (
            patch.object(dingtalk, "_post") as post,
            patch.object(dingtalk.time, "time", return_value=1234.5),
        ):
            dingtalk.send(config, "fixture-secret", "余额不足")
            url, body = post.call_args.args
            query = dict(dingtalk.parse.parse_qsl(dingtalk.parse.urlsplit(url).query))
            self.assertEqual(query["access_token"], "fixture-token")
            self.assertEqual(query["timestamp"], "1234500")
            self.assertTrue(query["sign"])
            self.assertEqual(body, {"msgtype": "text", "text": {"content": "余额不足"}})
        unsigned = dict(config, signing_enabled=False)
        with patch.object(dingtalk, "_post") as post:
            dingtalk.send(unsigned, "", "第二次")
            self.assertEqual(post.call_args.args[0], unsigned["webhook_url"])

    def test_dingtalk_error_is_sanitized(self):
        payload = json.dumps({"errcode": 310000, "errmsg": "fixture-secret"}).encode()
        error = HTTPError(
            "https://oapi.dingtalk.com/robot/send?access_token=fixture-token",
            401,
            "Unauthorized",
            {},
            io.BytesIO(payload),
        )
        with patch.object(dingtalk.request, "build_opener") as opener:
            opener.return_value.open.side_effect = error
            with self.assertRaises(DeliveryError) as caught:
                dingtalk._post(
                    "https://oapi.dingtalk.com/robot/send?access_token=fixture-token",
                    {},
                )
        self.assertIn("310000", str(caught.exception))
        self.assertNotIn("fixture-secret", str(caught.exception))
        self.assertNotIn("fixture-token", str(caught.exception))

    def test_http_business_error_is_not_reported_as_network_failure(self):
        payload = json.dumps({"code": 99991672, "msg": "fixture-secret"}).encode()
        error = HTTPError(
            "https://open.feishu.cn/example",
            400,
            "Bad Request",
            {},
            io.BytesIO(payload),
        )
        with patch.object(feishu.request, "build_opener") as opener:
            opener.return_value.open.side_effect = error
            result = feishu._post("/example", {})
        self.assertEqual(result["code"], 99991672)
        with self.assertRaises(DeliveryError) as caught:
            feishu._check(result)
        self.assertIn("im:message:send_as_bot", str(caught.exception))
        self.assertNotIn("fixture-secret", str(caught.exception))

    def test_http_invalid_response_never_leaks_body_or_succeeds(self):
        for raw in (b"<html>fixture-secret</html>", b'{"code":0}', b"{}"):
            error = HTTPError(
                "https://open.feishu.cn/example",
                502,
                "Bad Gateway",
                {},
                io.BytesIO(raw),
            )
            with patch.object(feishu.request, "build_opener") as opener:
                opener.return_value.open.side_effect = error
                with self.assertRaises(DeliveryError) as caught:
                    feishu._post("/example", {})
            self.assertIn("HTTP 502", str(caught.exception))
            self.assertNotIn("fixture-secret", str(caught.exception))

    def test_routes_and_csrf(self):
        client = app.test_client()
        base = "/cockpit/statistics/api/balance/channel"
        with (
            patch("new_api_cockpit.app.browser_identity", side_effect=fixture_identity),
            patch("new_api_cockpit.notifications.save") as save,
            patch(
                "new_api_cockpit.notifications.snapshot", return_value={"version": 2}
            ),
            patch("new_api_cockpit.notifications.deliver") as send,
        ):
            args = dict(auth=session_auth("fixture_admin"), json={"version": 2})
            self.assertEqual(client.put(base, **args).status_code, 403)
            self.assertEqual(client.post(base + "/test", **args).status_code, 403)
            save.assert_not_called()
            send.assert_not_called()
            headers = {"X-Statistics-Request": "1"}
            self.assertEqual(client.put(base, headers=headers, **args).status_code, 200)
            self.assertEqual(
                client.post(base + "/test", headers=headers, **args).status_code, 200
            )
            send.assert_called_once_with(test=True, expected_version=2)
            send.side_effect = ValueError("失败")
            self.assertEqual(
                client.post(base + "/test", headers=headers, **args).status_code, 400
            )

    def test_notification_failure_is_nonfatal(self):
        with patch(
            "new_api_cockpit.notifications.deliver",
            side_effect=RuntimeError("private"),
        ):
            with self.assertLogs(level="ERROR") as logs:
                notifications.notify_safely()
            self.assertNotIn("private", "".join(logs.output))

    def test_alert_message_format(self):
        alert = dict(
            budget=Decimal("220000"),
            spent=Decimal("218042.91"),
            remaining=Decimal("1957.09"),
            threshold=Decimal("2000"),
            updated_at=datetime(2026, 9, 16, 23, 34, 46, tzinfo=TZ),
        )
        self.assertEqual(
            notifications.format_alert_message(alert, "示例网关"),
            "【余额不足报警】\n站点：示例网关\n账本：全部\n总额度：¥220,000.00\n"
            "累计消费：¥218,042.91\n剩余额度：¥1,957.09\n报警阈值：¥2,000.00\n"
            "检查时间：2026-09-16 23:34:46（北京时间）",
        )

    def test_alert_api_uses_new_api_bearer_auth(self):
        client = app.test_client()
        url = "/cockpit/statistics/api/alert"
        with (
            patch(
                "new_api_cockpit.app.verify_api_key",
                side_effect=lambda token: token == "sk-fixture",
            ),
            patch("new_api_cockpit.balance.check_once", return_value=True) as check,
            patch(
                "new_api_cockpit.notifications.current_alert_record",
                return_value={"site_name": "示例网关"},
            ) as load,
        ):
            self.assertEqual(client.get(url).status_code, 401)
            self.assertEqual(
                client.get(
                    url, headers={"Authorization": "Bearer sk-wrong"}
                ).status_code,
                401,
            )
            response = client.get(url, headers={"Authorization": "Bearer sk-fixture"})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.mimetype, "application/json")
            self.assertEqual(
                response.get_json(),
                {"has_alert": True, "alert": {"site_name": "示例网关"}},
            )
            check.assert_called_once_with(daily=False)
            load.assert_called_once_with()
            load.return_value = None
            response = client.get(url, headers={"Authorization": "Bearer sk-fixture"})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.get_json(), {"has_alert": False, "alert": None})
            check.side_effect = balance.CheckBusy()
            response = client.get(url, headers={"Authorization": "Bearer sk-fixture"})
            self.assertEqual(response.status_code, 409)
            self.assertIn("error", response.get_json())

    def test_current_alert_record_has_typed_api_fields(self):
        alert = dict(
            budget=Decimal("220000"),
            spent=Decimal("218042.91"),
            remaining=Decimal("1957.09"),
            threshold=Decimal("2000"),
            updated_at=datetime(2026, 9, 16, 23, 34, 46, tzinfo=TZ),
        )
        connection = MagicMock()
        connection.__enter__.return_value.execute.return_value.fetchone.return_value = (
            alert
        )
        with (
            patch("new_api_cockpit.balance.connect", return_value=connection),
            patch(
                "new_api_cockpit.scopes.get_scope",
                return_value={"id": 1, "kind": "all", "tag_value": ""},
            ),
            patch(
                "new_api_cockpit.notifications.load_site_name",
                return_value="示例网关",
            ),
        ):
            record = notifications.current_alert_record()
        self.assertEqual(
            record,
            {
                "title": "余额不足报警",
                "scope_id": 1,
                "scope_name": "全部",
                "site_name": "示例网关",
                "budget": 220000.0,
                "spent": 218042.91,
                "remaining": 1957.09,
                "threshold": 2000.0,
                "currency": "CNY",
                "checked_at": "2026-09-16T23:34:46+08:00",
                "timezone": "Asia/Shanghai",
            },
        )
