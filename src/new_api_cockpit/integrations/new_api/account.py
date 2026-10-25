"""Fixed-origin own-account transport. No admin fallback or automatic retries."""

import json
import os
import re
from http.client import HTTPException
from urllib import error, request
from urllib.parse import urlsplit

from ...platform.http_headers import retry_after


class AccountError(ValueError):
    def __init__(self, message, status=503, *, retry_after=None):
        super().__init__(message)
        self.status = status
        self.retry_after = retry_after


class NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        return None


def read(path, credential):
    # Callers construct the query, not the user. Never accept arbitrary URLs,
    # proxy settings, cookies, upstream error bodies, or model-generation APIs.
    if path != "/api/user/self" and not path.startswith("/api/log/self/stat?"):
        raise AccountError("不允许的账户查询。", 400)
    data = _exchange(path, credential)
    if not isinstance(data, dict):
        raise AccountError("New API 账户查询响应无效。")
    return data


def own_keys(path, credential, *, method="GET", body=None):
    """Only official, owner-scoped token routes; never accept an arbitrary URL."""
    allowed = (
        method == "GET"
        and (
            path == "/api/user/self/groups"
            or re.fullmatch(r"/api/token/\?p=[1-9][0-9]{0,3}&page_size=50", path)
            or re.fullmatch(r"/api/token/[1-9][0-9]{0,14}", path)
        )
        or method in {"POST", "PUT"}
        and path == "/api/token/"
        or method == "POST"
        and re.fullmatch(r"/api/token/[1-9][0-9]{0,14}/key", path)
        or method == "DELETE"
        and re.fullmatch(r"/api/token/[1-9][0-9]{0,14}", path)
    )
    if not allowed or method in {"GET", "DELETE"} and body is not None:
        raise AccountError("不允许的本人 KEY 操作。", 400)
    scope = (
        "profile:read"
        if path == "/api/user/self/groups"
        else "api_key:reveal"
        if path.endswith("/key")
        else "api_key:read"
        if method == "GET"
        else "api_key:write"
    )
    return _exchange(path, credential, method=method, payload=body, scope=scope)


def _exchange(path, credential, *, method="GET", payload=None, scope=None):
    base = os.environ.get("NEW_API_INTERNAL_URL", "http://new-api:3000").rstrip("/")
    try:
        parsed = urlsplit(base)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError()
        req = request.Request(
            base + path,
            method=method,
            data=json.dumps(payload).encode() if payload is not None else None,
            headers={
                "Authorization": "Bearer " + credential,
                "Accept": "application/json",
                **({"Content-Type": "application/json"} if payload is not None else {}),
            },
        )
        opener = request.build_opener(request.ProxyHandler({}), NoRedirect())
        with opener.open(req, timeout=5) as response:
            if response.status != 200:
                raise AccountError("账户查询服务暂不可用。")
            limit = 262144 if scope else 65536
            raw = response.read(limit + 1)
        if len(raw) > limit:
            raise ValueError()
        body = json.loads(raw)
        if scope and isinstance(body, dict) and body.get("success") is False:
            raise AccountError("New API 拒绝 KEY 操作，请检查权限、分组和额度。", 400)
        if not isinstance(body, dict) or body.get("success") is not True:
            raise ValueError()
        return body.get("data")
    except error.HTTPError as exc:
        status = exc.code
        wait = retry_after(exc.headers.get("Retry-After")) if exc.headers else None
        exc.close()
        if status == 429:
            raise AccountError(
                "New API 请求过于频繁，请稍后再试。", 429, retry_after=wait
            ) from None
        if status == 401:
            raise AccountError("用户 API 访问令牌无效、过期或已撤销。", 401) from None
        if status == 403:
            raise AccountError(
                "用户 API 访问令牌缺少 "
                + (scope or "profile:read 或 usage:read")
                + " 权限。",
                403,
            ) from None
        if scope and status in {400, 404, 405, 413}:
            raise AccountError(
                "New API 拒绝 KEY 操作，请检查参数与站点版本。", status
            ) from None
        raise AccountError("New API 账户查询暂不可用，未自动重试。") from None
    except (error.URLError, OSError, ValueError, HTTPException) as exc:
        if isinstance(exc, AccountError):
            raise
        raise AccountError("New API 账户查询暂不可验证，未自动重试。") from None


def public_user(user):
    if (
        type(user.get("id")) is not int
        or user["id"] <= 0
        or not isinstance(user.get("username"), str)
        or not user["username"].strip()
        or type(user.get("role")) is not int
        or user["role"] < 1
        or type(user.get("status")) is not int
    ):
        raise AccountError("New API 身份响应无效。")
    if user["status"] != 1:
        raise AccountError("当前用户已停用。", 403)
    # Never return the complete profile: it may contain unrelated private data.
    return {
        key: user.get(key)
        for key in ("id", "username", "quota", "used_quota", "request_count")
    }
