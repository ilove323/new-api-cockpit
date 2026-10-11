"""Own-token tutorials: delegate ownership to New API, without PAT provisioning.

Only fixed token-management and model-list endpoints are called. No generation,
database writes, key persistence, automatic retries or user-supplied upstreams.
"""

from http.client import HTTPConnection, HTTPSConnection, HTTPException
import json
import os
import time
from urllib.parse import urlsplit

from . import personal_tokens
from .personal_tokens import TokenError as ManualError

MAX_RESPONSE = 1024 * 1024
MAX_TOKENS = 2000
PROTOCOLS = {"openai": "chat", "openai-response": "responses", "anthropic": "anthropic"}


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
    return personal_tokens.token_id(body)


def token_summary(row, user):
    return personal_tokens.summary(row, user)


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
    return personal_tokens.owned_row(user, credential, body, upstream=upstream)["id"]


def reveal_key(user, credential, body):
    return personal_tokens.key_for(user, credential, body, upstream=upstream)


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
