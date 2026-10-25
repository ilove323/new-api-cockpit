"""Owner-only token primitives shared by tutorials and the image workstation.

No PAT provisioning, token mutations or key persistence. Callers choose the
existing bounded New API client; only explicit key lookup reads a raw key.
"""

import re
import time


class TokenError(ValueError):
    def __init__(self, message, status=400, code="MANUAL_ERROR", *, reason=None):
        super().__init__(message)
        self.status = status
        self.code = code
        self.reason = reason


def token_id(body):
    if (
        not isinstance(body, dict)
        or set(body) != {"token_id"}
        or type(body["token_id"]) is not int
        or not 1 <= body["token_id"] <= 2147483647
    ):
        raise TokenError("请选择有效的令牌；不接受用户 ID 或上游地址。")
    return body["token_id"]


def summary(row, user):
    if (
        not isinstance(row, dict)
        or type(row.get("id")) is not int
        or row["id"] <= 0
        or type(row.get("user_id")) is not int
        or row["user_id"] != user["id"]
        or any(
            type(row.get(field)) is not int
            for field in ("status", "expired_time", "remain_quota")
        )
        or type(row.get("unlimited_quota")) is not bool
    ):
        raise TokenError("New API 令牌响应无效或不属于当前账号。", 403)
    reason = ""
    if row["status"] != 1:
        reason = "未启用"
    elif row["expired_time"] != -1 and row["expired_time"] <= time.time():
        reason = "已过期"
    elif not row["unlimited_quota"] and row["remain_quota"] <= 0:
        reason = "额度不足"
    key = str(row.get("key") or "").removeprefix("sk-")
    masked = key[:4] + "********" + key[-4:] if len(key) >= 12 else "********"
    return {
        "id": row["id"],
        "name": str(row.get("name") or "未命名令牌")[:128],
        "key_masked": masked,
        "group": str(row.get("group") or "")[:256],
        "status": row["status"],
        "expired_time": row["expired_time"],
        "remain_quota": row["remain_quota"],
        "unlimited_quota": row["unlimited_quota"],
        "usable": not reason,
        "unavailable_reason": reason,
    }


def owned_row(user, credential, body, *, upstream, group=None):
    target = token_id(body)
    row = upstream(f"/api/token/{target}", credential, user["id"]).get("data")
    info = summary(row, user)
    if info["id"] != target:
        raise TokenError("New API 返回了不同的令牌。", 502)
    if group is not None and row.get("group") != group:
        raise TokenError("请选择本人 GPT 分组的令牌。", 403)
    if not info["usable"]:
        raise TokenError(
            "所选令牌" + info["unavailable_reason"] + "，请换一个令牌。", 409
        )
    return row


def key_for(user, credential, body, *, upstream, group=None):
    target = owned_row(user, credential, body, upstream=upstream, group=group)["id"]
    return _reveal_owned_key(user, credential, target, upstream=upstream, group=group)


def _reveal_owned_key(user, credential, target, *, upstream, group=None):
    """Internal continuation after owned_row; revalidate after the secret read."""
    result = upstream(f"/api/token/{target}/key", credential, user["id"], body={})
    data = result.get("data")
    raw = data.get("key") if isinstance(data, dict) else None
    if not isinstance(raw, str):
        raise TokenError("New API 未返回有效的模型调用 KEY。", 502)
    raw = raw.removeprefix("sk-")
    if not re.fullmatch(r"[A-Za-z0-9_-]{16,256}", raw) or raw.startswith("sk-"):
        raise TokenError("New API 模型调用 KEY 格式无效。", 502)
    owned_row(user, credential, {"token_id": target}, upstream=upstream, group=group)
    return "sk-" + raw
