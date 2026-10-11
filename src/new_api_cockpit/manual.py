"""Own-token tutorials: delegate ownership to New API, without PAT provisioning.

Only fixed token-management and model-list endpoints are called. No generation,
database writes, key persistence, automatic retries or user-supplied upstreams.
"""

from http.client import HTTPConnection, HTTPSConnection, HTTPException
import json
import os
import re
import time
from urllib.parse import urlsplit

MAX_RESPONSE = 1024 * 1024
MAX_TOKENS = 2000
PROTOCOLS = {"openai": "chat", "openai-response": "responses", "anthropic": "anthropic"}


class ManualError(ValueError):
    def __init__(self, message, status=400, code="MANUAL_ERROR"):
        super().__init__(message)
        self.status = status
        self.code = code


def upstream(path, credential, user_id, *, body=None, model_key=False):
    """No cookies, redirect, ambient proxy, forwarded IP or upstream error text."""
    base = urlsplit(os.environ.get("NEW_API_INTERNAL_URL", "http://new-api:3000"))
    if (
        base.scheme not in {"http", "https"}
        or not base.hostname
        or base.username is not None
        or base.query
        or base.fragment
        or not isinstance(credential, str)
        or not 1 <= len(credential) <= 3500
        or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in credential)
    ):
        raise ManualError("New API 连接配置或调用凭据无效。", 503)
    connection = None
    deadline = time.monotonic() + 10
    try:
        connection_type = HTTPSConnection if base.scheme == "https" else HTTPConnection
        connection = connection_type(base.hostname, base.port, timeout=10)
        headers = {
            "Authorization": "Bearer " + credential,
            "Accept": "application/json",
        }
        if not model_key:
            headers["New-Api-User"] = str(user_id)
        if body is not None:
            headers["Content-Type"] = "application/json"
        connection.request(
            "POST" if body is not None else "GET",
            base.path.rstrip("/") + path,
            body=None if body is None else json.dumps(body).encode(),
            headers=headers,
        )
        sock = connection.sock
        sock.settimeout(max(0.01, deadline - time.monotonic()))
        with connection.getresponse() as response:
            if response.status in {401, 403, 404}:
                message = (
                    "所选令牌不可用或受 IP 限制，无法查询模型；请检查令牌设置。"
                    if model_key
                    else "无法读取该令牌，请确认它属于当前账号且仍然有效。"
                )
                raise ManualError(message, 403)
            if response.status != 200:
                raise ManualError(
                    f"New API 请求失败（HTTP {response.status}），未自动重试。", 503
                )
            chunks, size = [], 0
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError()
                sock.settimeout(remaining)
                chunk = response.read1(min(65536, MAX_RESPONSE + 1 - size))
                if not chunk:
                    break
                size += len(chunk)
                if size > MAX_RESPONSE:
                    raise ManualError("New API 响应过大。", 502)
                chunks.append(chunk)
            result = json.loads(b"".join(chunks))
        if (
            not isinstance(result, dict)
            or result.get("success") is False
            or "error" in result
        ):
            raise ManualError("New API 未返回有效结果，请检查令牌和站点版本。", 502)
        return result
    except (OSError, HTTPException, ValueError) as exc:
        if isinstance(exc, ManualError):
            raise
        raise ManualError("New API 连接或响应异常，未自动重试。", 503) from None
    finally:
        if connection is not None:
            connection.close()


def token_id(body):
    if (
        not isinstance(body, dict)
        or set(body) != {"token_id"}
        or type(body["token_id"]) is not int
        or not 1 <= body["token_id"] <= 2147483647
    ):
        raise ManualError("请选择有效的令牌；不接受用户 ID 或上游地址。")
    return body["token_id"]


def token_summary(row, user):
    # Even a mistaken upstream response must not disclose another user's token.
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
        raise ManualError("New API 令牌响应无效或不属于当前账号。", 403)
    reason = ""
    if row["status"] != 1:
        reason = "未启用"
    elif row["expired_time"] != -1 and row["expired_time"] <= time.time():
        reason = "已过期"
    elif not row["unlimited_quota"] and row["remain_quota"] <= 0:
        reason = "额度不足"
    key = str(row.get("key") or "").removeprefix("sk-")
    # Mask locally as well: never rely on upstream masking to protect the UI.
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


def list_tokens(user, credential):
    entries, seen = [], set()
    for page in range(1, MAX_TOKENS // 100 + 1):
        result = upstream(f"/api/token/?p={page}&page_size=100", credential, user["id"])
        data = result.get("data")
        if not isinstance(data, dict) or not isinstance(data.get("items"), list):
            raise ManualError("New API 令牌列表响应无效。", 502)
        total = data.get("total")
        if type(total) is not int or total < 0:
            raise ManualError("New API 令牌分页响应无效。", 502)
        if total > MAX_TOKENS:
            raise ManualError(
                "令牌超过 2000 个，请先在 New API 令牌页整理后再使用教程。", 413
            )
        for row in data["items"]:
            summary = token_summary(row, user)
            if summary["id"] not in seen:
                entries.append(summary)
                seen.add(summary["id"])
        if len(entries) >= total:
            return {"tokens": entries, "token_manager_path": "/keys"}
        if not data["items"]:
            break
    raise ManualError("令牌列表在查询期间发生变化，请重新加载。", 409)


def owned_token(user, credential, body):
    target = token_id(body)
    result = upstream(f"/api/token/{target}", credential, user["id"])
    row = result.get("data")
    summary = token_summary(row, user)
    if summary["id"] != target:
        raise ManualError("New API 返回了不同的令牌。", 502)
    if not summary["usable"]:
        raise ManualError(
            "所选令牌" + summary["unavailable_reason"] + "，请换一个令牌。", 409
        )
    return target


def reveal_key(user, credential, body):
    target = owned_token(user, credential, body)
    # Official endpoint performs the ownership check again and audits the reveal.
    result = upstream(f"/api/token/{target}/key", credential, user["id"], body={})
    data = result.get("data")
    raw = data.get("key") if isinstance(data, dict) else None
    if not isinstance(raw, str):
        raise ManualError("New API 未返回有效的模型调用 KEY。", 502)
    raw = raw.removeprefix("sk-")
    if not re.fullmatch(r"[A-Za-z0-9_-]{16,256}", raw) or raw.startswith("sk-"):
        raise ManualError("New API 模型调用 KEY 格式无效。", 502)
    # Reveal checks ownership upstream, not expiry/status. Recheck after lookup.
    owned_token(user, credential, body)
    return "sk-" + raw


def models(user, credential, body):
    key = reveal_key(user, credential, body)
    result = upstream("/v1/models", key, user["id"], model_key=True)
    rows = result.get("data")
    if not isinstance(rows, list) or len(rows) > 10000:
        raise ManualError("New API 模型列表响应无效。", 502)
    entries = {}
    for row in rows:
        if not isinstance(row, dict):
            raise ManualError("New API 模型响应无效。", 502)
        name = row.get("id")
        if (
            not isinstance(name, str)
            or not 1 <= len(name) <= 256
            or any(ord(c) < 32 or ord(c) == 127 for c in name)
        ):
            raise ManualError("New API 模型 ID 无效。", 502)
        endpoints = row.get("supported_endpoint_types")
        protocols = (
            sorted(
                {
                    PROTOCOLS[e]
                    for e in endpoints
                    if isinstance(e, str) and e in PROTOCOLS
                }
            )
            if isinstance(endpoints, list)
            else []
        )
        # Missing endpoint metadata means unknown, never guess from model names.
        entries[name] = {"id": name, "protocols": protocols}
    return {"models": [entries[name] for name in sorted(entries)]}
