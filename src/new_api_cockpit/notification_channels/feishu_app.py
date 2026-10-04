"""Feishu enterprise application bot: cached tenant token and outbound messages only."""

import hashlib
import json
import re
import threading
import time
import uuid
from urllib import request
from urllib.error import HTTPError
from .base import DeliveryError

BASE = "https://open.feishu.cn/open-apis"
DISPLAY_NAME = "飞书企业自建应用机器人"
_tokens = {}
_lock = threading.Lock()


def validate(config, secret):
    if not re.fullmatch(r"cli_[A-Za-z0-9]+", config["app_id"]):
        raise ValueError("请填写有效的 App ID（cli_ 开头）。")
    if not secret or len(secret) > 512:
        raise ValueError("请填写 App Secret。")
    kind = config["receive_id_type"]
    target = config["receive_id"]
    if kind == "chat_id" and re.fullmatch(r"oc_[A-Za-z0-9_-]+", target):
        return
    # Tenant user IDs need no fixed prefix; do not accept a pasted Open ID.
    if (
        kind == "user_id"
        and re.fullmatch(r"[A-Za-z0-9_-]{1,128}", target)
        and not target.startswith(("ou_", "oc_"))
    ):
        return
    raise ValueError(
        "接收目标应为群聊 chat_id（oc_）或个人 user_id，不能填写 open_id（ou_）。"
    )


class NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def _post(path, body, token=None):
    headers = {"Content-Type": "application/json; charset=utf-8"}
    if token:
        headers["Authorization"] = "Bearer " + token
    req = request.Request(
        BASE + path, data=json.dumps(body).encode(), headers=headers, method="POST"
    )
    status = None
    try:
        try:
            response = request.build_opener(NoRedirect()).open(req, timeout=8)
        except HTTPError as exc:
            # Feishu returns structured business errors with non-2xx HTTP statuses.
            response = exc
        with response:
            status = response.code
            result = json.loads(response.read(1024 * 1024))
        if not isinstance(result, dict):
            raise ValueError()
    except Exception:
        # Do not retain response bodies, credentials, proxies or request headers.
        detail = f"（HTTP {status}）" if isinstance(status, int) else ""
        raise DeliveryError(
            "飞书连接或响应异常" + detail + "，请检查网络和应用配置。"
        ) from None
    if isinstance(status, int) and not 200 <= status < 300:
        if type(result.get("code")) is not int or result["code"] == 0:
            raise DeliveryError(
                f"飞书请求失败（HTTP {status}），未返回有效业务错误码。"
            )
    return result


def _check(result):
    code = result.get("code")
    if type(code) is not int or code != 0:
        if code == 99991672:
            raise DeliveryError(
                "飞书错误码 99991672：应用缺少接口权限。发送消息请开通 "
                "im:message:send_as_bot（以应用的身份发消息），并发布应用使权限生效。"
            )
        suffix = str(code) if type(code) is int else "未知"
        raise DeliveryError(
            "飞书发送失败，错误码 "
            + suffix
            + "；请检查应用凭证、权限、发布状态及接收目标。"
        )


def _tenant_token(app_id, secret, refresh=False):
    key = (app_id, hashlib.sha256(secret.encode()).hexdigest())
    with _lock:
        cached = _tokens.get(key)
        if not refresh and cached and cached[1] > time.monotonic():
            return cached[0]
        result = _post(
            "/auth/v3/tenant_access_token/internal",
            {"app_id": app_id, "app_secret": secret},
        )
        _check(result)
        token, expire = result.get("tenant_access_token"), result.get("expire")
        if (
            not isinstance(token, str)
            or not token
            or type(expire) is not int
            or expire <= 0
        ):
            raise DeliveryError("飞书未返回有效的访问凭证。")
        _tokens.clear()
        _tokens[key] = (token, time.monotonic() + max(0, expire - 120))
        return token


def send(config, secret, text):
    validate(config, secret)
    body = {
        "receive_id": config["receive_id"],
        "msg_type": "text",
        "content": json.dumps({"text": text}, ensure_ascii=False),
        "uuid": str(uuid.uuid4()),
    }
    path = "/im/v1/messages?receive_id_type=" + config["receive_id_type"]
    token = _tenant_token(config["app_id"], secret)
    result = _post(path, body, token)
    # A rejected expired token is safe to refresh; keep the same message UUID.
    if result.get("code") in (99991663, 99991668):
        token = _tenant_token(config["app_id"], secret, refresh=True)
        result = _post(path, body, token)
    _check(result)
