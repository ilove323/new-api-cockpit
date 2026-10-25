"""Validate sessions and PATs through New API; SQL can only narrow PAT grants.

JWT payload parsing only filters transport/type. Session authorization requires
New API /api/user/self; Cockpit never signs credentials or reads Redis sessions.
"""

import base64
import hashlib
import json
import time
from urllib import request  # noqa: F401 -- retained transport patch point

import psycopg
from psycopg.rows import dict_row

from .integrations.new_api import account
from .integrations.new_api.account import NoRedirect  # noqa: F401

COOKIE_NAME = "cockpit_access"
COOKIE_PATH = "/cockpit"
MAX_TOKEN_LENGTH = 3500


class LoginRequired(ValueError):
    pass


class LoginForbidden(ValueError):
    pass


class AuthUnavailable(RuntimeError):
    def __init__(self, message, *, status=503, retry_after=None):
        super().__init__(message)
        self.status, self.retry_after = status, retry_after


class VerifiedPat(dict):
    """Only the public actor is serializable; grants stay request-local."""

    def __init__(self, user, scopes=None):
        super().__init__((key, user[key]) for key in ("id", "username", "role"))
        self.scopes = scopes


def _upstream_user(credential):
    try:
        return account.read("/api/user/self", credential)
    except account.AccountError as exc:
        if exc.status == 401:
            raise LoginRequired("登录凭据无效、已过期或已撤销。") from None
        if exc.status == 403:
            raise LoginForbidden("凭据权限不足或账号不可用。") from None
        raise AuthUnavailable(
            "New API 认证服务暂不可用，请稍后重试。",
            status=429 if exc.status == 429 else 503,
            retry_after=exc.retry_after,
        ) from None


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


def verify_session(token, *, allow_user=False):
    claims = session_claims(token)
    user = _upstream_user(token)
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
    if user["status"] != 1 or user["role"] < 1:
        raise LoginForbidden("当前 New API 账号不可用。")
    if not allow_user and user["role"] < 10:
        raise LoginForbidden("仅启用的 New API 管理员可以访问。")
    return {
        "id": user["id"],
        "username": user["username"],
        "role": user["role"],
        "session_id": claims["sid"],
        "expires_at": min(claims["exp"], int(time.time()) + 900),
    }


def verify_pat(value):
    """New API decides validity, including legacy retirement; no SQL fallback."""
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= 256
        or any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in value)
        or value.startswith("sk-")
        or "." in value
    ):
        raise LoginRequired("请提供有效的 New API 管理员 PAT。")
    user = _upstream_user(value)
    if (
        type(user.get("id")) is not int
        or user["id"] <= 0
        or not isinstance(user.get("username"), str)
        or not user["username"].strip()
        or type(user.get("role")) is not int
        or type(user.get("status")) is not int
    ):
        raise AuthUnavailable("New API 身份响应无效。")
    if user["status"] != 1 or user["role"] < 10:
        raise LoginForbidden("仅启用的 New API 管理员可以访问。")
    if not value.startswith("nap_"):
        return VerifiedPat(user)
    try:
        with psycopg.connect(
            connect_timeout=8,
            row_factory=dict_row,
            options="-c default_transaction_read_only=on -c statement_timeout=5000",
        ) as conn:
            row = conn.execute(
                """SELECT scopes FROM user_access_tokens
                   WHERE token_hash=%s AND user_id=%s
                   AND (expires_at<=0 OR expires_at>%s) LIMIT 1""",
                (
                    hashlib.sha256(value.encode()).hexdigest(),
                    user["id"],
                    int(time.time()),
                ),
            ).fetchone()
    except psycopg.Error:
        raise AuthUnavailable("管理员认证服务暂不可用，请稍后重试。") from None
    if row is None:
        raise LoginRequired("PAT 已撤销、过期或不属于当前账号。")
    try:
        scopes = json.loads(row["scopes"])
        if (
            not isinstance(scopes, list)
            or len(scopes) > 256
            or any(
                not isinstance(scope, str) or not 1 <= len(scope) <= 128
                for scope in scopes
            )
        ):
            raise ValueError()
    except (KeyError, TypeError, ValueError):
        raise LoginForbidden("PAT 权限数据无效。") from None
    return VerifiedPat(user, frozenset(scopes))
