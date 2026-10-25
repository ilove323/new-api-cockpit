"""Bounded fixed-origin New API JSON calls, without credentials persistence."""

from http.client import HTTPConnection, HTTPSConnection, HTTPException
import json
import os
import re
import time
from urllib.parse import urlsplit

from .personal_tokens import TokenError as ManualError
from .connection_pool import DEFAULT_POOL, one_shot

MAX_RESPONSE = 1024 * 1024


def upstream(
    path,
    credential,
    user_id,
    *,
    body=None,
    model_key=False,
    connection_types=None,
    pool=DEFAULT_POOL,
):
    """No cookies, redirect, ambient proxy, forwarded IP or upstream error text."""
    base = urlsplit(os.environ.get("NEW_API_INTERNAL_URL", "http://new-api:3000"))
    if (
        base.scheme not in {"http", "https"}
        or not base.hostname
        or base.username is not None
        or base.query
        or base.fragment
        or type(user_id) is not int
        or not 1 <= user_id <= 2147483647
        or not isinstance(credential, str)
        or not 1 <= len(credential) <= 3500
        or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in credential)
    ):
        raise ManualError("New API 连接配置或调用凭据无效。", 503)
    deadline = time.monotonic() + 10
    try:
        http_type, https_type = connection_types or (HTTPConnection, HTTPSConnection)
        connection_type = https_type if base.scheme == "https" else http_type
        catalogue = (
            body is None
            and (
                path
                in {
                    "/v1/models",
                    "/api/user/self",
                    "/api/user/self/groups",
                    "/api/token/auto-groups",
                }
                or re.fullmatch(r"/api/token/[1-9][0-9]*", path)
                or path.startswith("/api/token/?")
            )
            or body == {}
            and re.fullmatch(r"/api/token/[1-9][0-9]*/key", path)
        )

        def factory(timeout):
            return connection_type(base.hostname, base.port, timeout=timeout)

        if pool is not None and catalogue:
            key = pool.scope(
                (base.scheme, base.hostname, base.port, base.path.rstrip("/")),
                os.environ.get("COCKPIT_PROCESS_ROLE", "web"),
                user_id,
                "model" if model_key else "account",
                credential,
                connection_type,
            )
            context = pool.connection(key, factory, deadline)
        else:
            context = one_shot(factory, deadline)
        with context as lease:
            connection = lease.connection
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError()
            connection.timeout = remaining
            if connection.sock is not None:
                connection.sock.settimeout(remaining)
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
                if response.status == 429:
                    raise ManualError(
                        "New API 请求过于频繁，请稍后再试。",
                        429,
                        reason="rate_limited",
                        retry_after=response.getheader("Retry-After"),
                    )
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
                    # A framed final chunk can close a Connection: close
                    # socket immediately. Do not touch it just to read EOF.
                    if response.isclosed() is True:
                        break
                result = json.loads(b"".join(chunks))
            if (
                not isinstance(result, dict)
                or result.get("success") is False
                or "error" in result
            ):
                raise ManualError("New API 未返回有效结果，请检查令牌和站点版本。", 502)
            lease.reusable = (
                response.will_close is False
                and not response.getheader("Set-Cookie")
                and connection.sock is not None
            )
            return result
    except (OSError, HTTPException, ValueError) as exc:
        if isinstance(exc, ManualError):
            raise
        raise ManualError("New API 连接或响应异常，未自动重试。", 503) from None
