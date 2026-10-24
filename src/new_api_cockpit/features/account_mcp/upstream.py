"""Only the configured New API's own-account GET endpoints are reachable."""

import json
import os
from http.client import HTTPException
from urllib import error, request
from urllib.parse import urlsplit


class AccountError(ValueError):
    def __init__(self, message, status=503):
        super().__init__(message)
        self.status = status


class NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        return None


def read(path, credential):
    # Callers construct the query, not the user. Never accept arbitrary URLs,
    # proxy settings, cookies, upstream error bodies, or model-generation APIs.
    if path != "/api/user/self" and not path.startswith("/api/log/self/stat?"):
        raise AccountError("不允许的账户查询。", 400)
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
            headers={
                "Authorization": "Bearer " + credential,
                "Accept": "application/json",
            },
        )
        opener = request.build_opener(request.ProxyHandler({}), NoRedirect())
        with opener.open(req, timeout=5) as response:
            if response.status != 200:
                raise AccountError("账户查询服务暂不可用。")
            raw = response.read(65537)
        if len(raw) > 65536:
            raise ValueError()
        body = json.loads(raw)
        if not isinstance(body, dict) or body.get("success") is not True:
            raise ValueError()
        data = body.get("data")
        if not isinstance(data, dict):
            raise ValueError()
        return data
    except error.HTTPError as exc:
        status = exc.code
        exc.close()
        if status == 401:
            raise AccountError("用户 API 访问令牌无效、过期或已撤销。", 401) from None
        if status == 403:
            raise AccountError(
                "用户 API 访问令牌缺少 profile:read 或 usage:read 权限。", 403
            ) from None
        raise AccountError("New API 账户查询暂不可用，未自动重试。") from None
    except (error.URLError, OSError, ValueError, HTTPException) as exc:
        if isinstance(exc, AccountError):
            raise
        raise AccountError("New API 账户查询暂不可验证，未自动重试。") from None


def authenticate(credential):
    user = read("/api/user/self", credential)
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
