"""Validate browser sessions via New API and administrator PATs via source SQL.

JWT payload parsing only filters transport/type. Session authorization requires
New API /api/user/self; Cockpit never signs credentials or reads Redis sessions.
"""

import base64
import json
import os
import time
from http.client import HTTPException
from urllib import error, request
from urllib.parse import urlsplit

import psycopg
from psycopg.rows import dict_row

COOKIE_NAME = "cockpit_access"
COOKIE_PATH = "/cockpit"
MAX_TOKEN_LENGTH = 3500


class LoginRequired(ValueError):
    pass


class LoginForbidden(ValueError):
    pass


class AuthUnavailable(RuntimeError):
    pass


class NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def session_claims(token):
    """Reject PATs, relay keys and expired input before contacting New API."""
    try:
        if not isinstance(token, str) or not 1 <= len(token) <= MAX_TOKEN_LENGTH:
            raise ValueError()
        parts = token.split(".")
        if len(parts) != 3 or any(not part for part in parts):
            raise ValueError()
        raw = base64.b64decode(
            parts[1] + "=" * (-len(parts[1]) % 4), altchars=b"-_", validate=True
        )
        claims = json.loads(raw)
        if not isinstance(claims, dict):
            raise ValueError()
        audience = claims.get("aud")
        if audience != "new-api-dashboard" and not (
            isinstance(audience, list) and "new-api-dashboard" in audience
        ):
            raise ValueError()
        if (
            claims.get("iss") != "new-api"
            or claims.get("token_use") != "access"
            or not isinstance(claims.get("sid"), str)
            or not 1 <= len(claims["sid"]) <= 64
            or not isinstance(claims.get("sub"), str)
            or not claims["sub"].isascii()
            or not claims["sub"].isdigit()
            or int(claims["sub"]) <= 0
            or type(claims.get("exp")) is not int
            or claims["exp"] <= time.time()
        ):
            raise ValueError()
        return claims
    except (ValueError, TypeError, UnicodeError, KeyError):
        raise LoginRequired("登录已失效，请重新登录。") from None


def verify_session(token):
    claims = session_claims(token)
    base = os.environ.get("NEW_API_INTERNAL_URL", "http://new-api:3000").rstrip("/")
    parsed = urlsplit(base)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or parsed.username is not None
        or parsed.query
        or parsed.fragment
    ):
        raise AuthUnavailable("New API 登录服务配置无效。")
    req = request.Request(
        base + "/api/user/self",
        headers={"Authorization": "Bearer " + token, "Accept": "application/json"},
    )
    try:
        # No ambient HTTP proxy, redirects, cookies, passwords or PAT substitution.
        opener = request.build_opener(request.ProxyHandler({}), NoRedirect())
        with opener.open(req, timeout=5) as response:
            raw = response.read(65537)
            if len(raw) > 65536:
                raise ValueError()
            body = json.loads(raw)
    except error.HTTPError as exc:
        status = exc.code
        exc.close()
        if status == 401:
            raise LoginRequired("登录已失效，请重新登录。") from None
        if status == 403:
            raise LoginForbidden("当前账号没有管理权限。") from None
        raise AuthUnavailable("New API 登录服务暂不可用，请稍后重试。") from None
    except (error.URLError, OSError, ValueError, HTTPException):
        raise AuthUnavailable("New API 登录服务暂不可用，请稍后重试。") from None
    if not isinstance(body, dict) or body.get("success") is not True:
        raise AuthUnavailable("New API 登录服务返回异常，请稍后重试。")
    user = body.get("data")
    if (
        not isinstance(user, dict)
        or type(user.get("id")) is not int
        or user["id"] != int(claims["sub"])
        or not isinstance(user.get("username"), str)
        or not user["username"].strip()
        or type(user.get("role")) is not int
        or type(user.get("status")) is not int
    ):
        raise AuthUnavailable("New API 登录服务返回异常，请稍后重试。")
    if user["role"] < 10 or user["status"] != 1:
        raise LoginForbidden("仅启用的 New API 管理员可以访问。")
    return {
        "id": user["id"],
        "username": user["username"],
        "role": user["role"],
        "session_id": claims["sid"],
        "expires_at": min(claims["exp"], int(time.time()) + 900),
    }


def verify_pat(value):
    """Resolve the PAT's real administrator on every request, without caching.

    The credential is compared verbatim with users.access_token. Model keys and
    request-supplied identities never grant access or determine the audit actor.
    """
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= 256
        or any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in value)
    ):
        raise LoginRequired("请提供有效的 New API 管理员 PAT。")
    try:
        with psycopg.connect(
            connect_timeout=8,
            row_factory=dict_row,
            options="-c default_transaction_read_only=on -c statement_timeout=5000",
        ) as conn:
            row = conn.execute(
                """SELECT id,username,role FROM users WHERE access_token=%s
                   AND role>=10 AND status=1 AND deleted_at IS NULL LIMIT 1""",
                (value,),
            ).fetchone()
    except psycopg.Error:
        raise AuthUnavailable("管理员认证服务暂不可用，请稍后重试。") from None
    if row is None:
        raise LoginRequired("无效的 New API 管理员 PAT，或账号已停用。")
    return {key: row[key] for key in ("id", "username", "role")}
