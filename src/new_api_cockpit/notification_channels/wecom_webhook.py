"""WeCom group robot: one outbound text message, never redirect or retry."""

import json
import re
from urllib import parse, request
from urllib.error import HTTPError

from .base import DeliveryError

DISPLAY_NAME = "企业微信群机器人"
MAX_TEXT_BYTES = 2048


class NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def mentions(value, *, mobile=False):
    """Bound and deduplicate optional robot mentions, not display names."""
    label = "手机号" if mobile else "成员 UserID"
    if not isinstance(value, list) or len(value) > 100:
        raise ValueError(f"企业微信{label}须为数组，最多 100 项。")
    pattern = r"(?:\+?[0-9]{5,20}|@all)" if mobile else r"[A-Za-z0-9_.@-]{1,64}"
    result = []
    for item in value:
        if not isinstance(item, str) or not re.fullmatch(pattern, item):
            raise ValueError(f"企业微信{label}格式无效；提醒所有人请填写 @all。")
        if item not in result:
            result.append(item)
    return result


def validate(config, secret=""):
    """Only the official HTTPS endpoint with one nonempty webhook key is allowed."""
    webhook = config.get("webhook_url", "")
    if (
        not isinstance(webhook, str)
        or not webhook
        or len(webhook) > 2048
        or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in webhook)
    ):
        raise ValueError("请填写有效的企业微信 Webhook URL。")
    try:
        parts = parse.urlsplit(webhook)
        port = parts.port
        query = parse.parse_qs(parts.query, keep_blank_values=True, strict_parsing=True)
    except ValueError:
        raise ValueError("请填写有效的企业微信 Webhook URL。") from None
    if (
        parts.scheme != "https"
        or parts.hostname != "qyapi.weixin.qq.com"
        or port not in (None, 443)
        or parts.path != "/cgi-bin/webhook/send"
        or parts.username is not None
        or parts.password is not None
        or parts.fragment
        or set(query) != {"key"}
        or len(query["key"]) != 1
        or not re.fullmatch(r"[A-Za-z0-9_-]{1,256}", query["key"][0])
    ):
        raise ValueError("企业微信 Webhook 必须是官方群机器人地址并包含 key。")
    mentions(config.get("mentioned_list", []))
    mentions(config.get("mentioned_mobile_list", []), mobile=True)


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
            # Do not send the credential-bearing URL through environment proxies.
            response = request.build_opener(
                request.ProxyHandler({}), NoRedirect()
            ).open(req, timeout=8)
        except HTTPError as exc:
            response = exc
        with response:
            status = response.code
            raw = response.read(65537)
        if len(raw) > 65536:
            raise ValueError()
        result = json.loads(raw)
        if not isinstance(result, dict):
            raise ValueError()
    except Exception:
        detail = f"（HTTP {status}）" if type(status) is int else ""
        raise DeliveryError(
            "企业微信连接或响应异常" + detail + "，请检查网络和 Webhook 配置。"
        ) from None
    code = result.get("errcode")
    if (
        type(status) is not int
        or not 200 <= status < 300
        or type(code) is not int
        or code != 0
    ):
        # Never expose errmsg or an arbitrary string supplied as an error code.
        suffix = f"，错误码 {code}" if type(code) is int else ""
        http_status = status if type(status) is int else "未知"
        raise DeliveryError(
            f"企业微信发送失败（HTTP {http_status}{suffix}），请检查机器人、Webhook 和发送频率。"
        )


def send(config, secret, text):
    validate(config)
    if not isinstance(text, str) or not text or len(text.encode()) > MAX_TEXT_BYTES:
        raise DeliveryError("企业微信文本消息须为 1～2048 字节，请缩短站点或账本名称。")
    body = {"content": text}
    for key, mobile in (("mentioned_list", False), ("mentioned_mobile_list", True)):
        values = mentions(config.get(key, []), mobile=mobile)
        if values:
            body[key] = values
    _post(config["webhook_url"], {"msgtype": "text", "text": body})
