"""DingTalk custom Webhook robot with optional HMAC signing."""

import base64
import hashlib
import hmac
import json
import time
from urllib import parse, request
from urllib.error import HTTPError

from .base import DeliveryError

DISPLAY_NAME = "钉钉自定义 Webhook 机器人"


class NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def validate(config, secret):
    """Accept only DingTalk's HTTPS robot endpoint and a single access token."""
    webhook = config.get("webhook_url", "")
    if not isinstance(webhook, str) or not webhook or len(webhook) > 2048:
        raise ValueError("请填写有效的钉钉 Webhook URL。")
    try:
        parts = parse.urlsplit(webhook)
        port = parts.port
    except ValueError:
        raise ValueError("请填写有效的钉钉 Webhook URL。") from None
    query = parse.parse_qs(parts.query, keep_blank_values=True)
    if (
        parts.scheme != "https"
        or parts.hostname != "oapi.dingtalk.com"
        or port not in (None, 443)
        or parts.path != "/robot/send"
        or parts.username
        or parts.password
        or parts.fragment
        or set(query) != {"access_token"}
        or len(query["access_token"]) != 1
        or not query["access_token"][0]
    ):
        raise ValueError("钉钉 Webhook 必须是官方机器人地址并包含 access_token。")
    if type(config.get("signing_enabled")) is not bool:
        raise ValueError("钉钉加签设置无效。")
    if config["signing_enabled"] and (not secret or len(secret) > 512):
        raise ValueError("启用加签后必须填写有效的加签密钥。")


def signed_url(webhook, secret, timestamp):
    """Append DingTalk's timestamp and HMAC-SHA256 signature."""
    string = f"{timestamp}\n{secret}".encode()
    signature = base64.b64encode(
        hmac.new(secret.encode(), string, hashlib.sha256).digest()
    ).decode()
    separator = "&" if "?" in webhook else "?"
    return (
        webhook
        + separator
        + parse.urlencode({"timestamp": timestamp, "sign": signature})
    )


def _post(url, body):
    req = request.Request(
        url,
        data=json.dumps(body, ensure_ascii=False).encode(),
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    status = None
    try:
        try:
            response = request.build_opener(NoRedirect()).open(req, timeout=8)
        except HTTPError as exc:
            response = exc
        with response:
            status = response.code
            raw = response.read(1024 * 1024)
        result = json.loads(raw) if raw else {}
        if not isinstance(result, dict):
            raise ValueError()
    except Exception:
        detail = f"（HTTP {status}）" if isinstance(status, int) else ""
        raise DeliveryError(
            "钉钉连接或响应异常" + detail + "，请检查网络和 Webhook 配置。"
        ) from None
    code = result.get("errcode")
    if not 200 <= status < 300 or code not in (0, "0"):
        suffix = f"，错误码 {code}" if isinstance(code, (str, int)) else ""
        raise DeliveryError(
            f"钉钉发送失败（HTTP {status}{suffix}），请检查 Webhook、安全设置和消息关键词。"
        )
    return result


def send(config, secret, text):
    validate(config, secret)
    webhook = config["webhook_url"]
    if config["signing_enabled"]:
        webhook = signed_url(webhook, secret, int(time.time() * 1000))
    _post(webhook, {"msgtype": "text", "text": {"content": text}})
