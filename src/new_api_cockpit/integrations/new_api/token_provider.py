"""Owner-only token adapter injected into independent feature modules."""

from contextlib import contextmanager
from collections import OrderedDict
import hashlib
import os
import threading
import time

from . import json_client, personal_tokens
from .personal_tokens import TokenError

MAX_TOKENS = 2000
PROTOCOLS = {"openai": "chat", "openai-response": "responses", "anthropic": "anthropic"}
KEY_REUSE_SECONDS = 20 * 60
KEY_CACHE_ENTRIES = 256


class ModelKey(str):
    def __repr__(self):
        return "<ModelKey redacted>"


class TokenProvider:
    def __init__(self, upstream=None):
        self.upstream = upstream or json_client.upstream
        self._keys = OrderedDict()
        self._key_lock = threading.Lock()

    def list_tokens(self, access, *, group=None):
        entries, seen = [], set()
        user = access.principal.as_user()
        for page in range(1, MAX_TOKENS // 100 + 1):
            result = self.upstream(
                f"/api/token/?p={page}&page_size=100", access.credential, user["id"]
            )
            data = result.get("data")
            if not isinstance(data, dict) or not isinstance(data.get("items"), list):
                raise TokenError("New API 令牌列表响应无效。", 502)
            total = data.get("total")
            if type(total) is not int or not 0 <= total <= MAX_TOKENS:
                raise TokenError("令牌列表过大或分页无效。", 413)
            for row in data["items"]:
                info = personal_tokens.summary(row, user)
                if info["id"] not in seen:
                    seen.add(info["id"])
                    if group is None or info["group"] == group:
                        entries.append(info)
            if len(seen) >= total:
                return entries
            if not data["items"]:
                break
        raise TokenError("令牌列表在查询期间发生变化，请重新加载。", 409)

    def _row(self, access, token_id, group):
        return personal_tokens.owned_row(
            access.principal.as_user(),
            access.credential,
            {"token_id": token_id},
            upstream=self.upstream,
            group=group,
        )

    def routing_groups(self, access, token_id):
        """Resolve current New API routing policy without granting model access."""
        row = self._row(access, token_id, None)

        def names(value):
            if (
                not isinstance(value, list)
                or len(value) > 256
                or any(
                    not isinstance(g, str)
                    or not 1 <= len(g) <= 256
                    or g != g.strip()
                    or any(ord(c) < 32 or ord(c) == 127 for c in g)
                    for g in value
                )
            ):
                raise TokenError("New API 路由分组信息无效。", 502)
            return list(dict.fromkeys(value))

        group = row.get("group")
        if not isinstance(group, str):
            raise TokenError("New API 令牌分组信息无效。", 502)
        if not group:
            user = self.upstream(
                "/api/user/self", access.credential, access.principal.user_id
            ).get("data")
            if not isinstance(user, dict) or user.get("id") != access.principal.user_id:
                raise TokenError("New API 用户信息无效。", 502)
            group = user.get("group")
        if group != "auto":
            return names([group])
        policy = self.upstream(
            "/api/token/auto-groups", access.credential, access.principal.user_id
        ).get("data")
        if not isinstance(policy, dict):
            raise TokenError("New API 自动分组信息无效。", 502)
        inherited = names(policy.get("groups"))
        selected = row.get("auto_groups")
        if selected is None:
            return inherited
        selected = names(selected)
        allowed = self.upstream(
            "/api/user/self/groups", access.credential, access.principal.user_id
        ).get("data")
        maximum = policy.get("max_count")
        if (
            not isinstance(allowed, dict)
            or type(maximum) is not int
            or not 1 <= maximum <= 256
        ):
            raise TokenError("New API 自动分组权限信息无效。", 502)
        return [g for g in selected if g != "auto" and g in allowed][:maximum]

    @contextmanager
    def lease_key(self, access, token_id, *, group=None):
        # Reuse only the secret, never authorization or model policy. Every use
        # still reads the owner's current token; New API validates the actual
        # key (including IP/group/model limits) on every model request.
        row = self._row(access, token_id, group)
        identity = (
            os.environ.get("NEW_API_INTERNAL_URL", "http://new-api:3000"),
            access.principal.user_id,
            token_id,
            hashlib.sha256(access.credential.encode()).digest(),
            hashlib.sha256(str(row.get("key", "")).encode()).digest(),
        )
        with self._key_lock:
            now = time.monotonic()
            for entry, (expiry, _) in list(self._keys.items()):
                if (
                    expiry <= now
                    or entry[:3] == identity[:3]
                    and entry[4] != identity[4]
                ):
                    del self._keys[entry]
            cached = self._keys.get(identity)
            if cached is None:
                key = ModelKey(
                    personal_tokens.key_for(
                        access.principal.as_user(),
                        access.credential,
                        {"token_id": token_id},
                        upstream=self.upstream,
                        group=group,
                    )
                )
                self._keys[identity] = (time.monotonic() + KEY_REUSE_SECONDS, key)
                while len(self._keys) > KEY_CACHE_ENTRIES:
                    self._keys.popitem(last=False)
            else:
                _, key = cached
            self._keys.move_to_end(identity)
        try:
            yield key
        except ValueError as exc:
            if getattr(exc, "status", None) in {401, 403}:
                with self._key_lock:
                    self._keys.pop(identity, None)
            raise
        finally:
            key = None

    def list_models(self, access, token_id, *, group=None):
        row = self._row(access, token_id, group)
        enabled = row.get("model_limits_enabled")
        if type(enabled) is not bool:
            raise TokenError(
                "令牌模型限制信息无效。", 502, reason="model_limits_enabled"
            )
        limits = None
        if enabled:
            raw = row.get("model_limits") or ""
            if not isinstance(raw, str) or len(raw) > 100000:
                raise TokenError("令牌模型限制响应无效。", 502, reason="model_limits")
            limits = {item.strip() for item in raw.split(",") if item.strip()}
        with self.lease_key(access, token_id, group=group) as key:
            reply = self.upstream(
                "/v1/models", key, access.principal.user_id, model_key=True
            )
        rows = reply.get("data")
        if not isinstance(rows, list) or len(rows) > 10000:
            raise TokenError(
                "New API 模型列表响应无效。", 502, reason="model_catalogue"
            )
        entries = {}
        for row in rows:
            if not isinstance(row, dict):
                raise TokenError(
                    "New API 模型响应无效。", 502, reason="model_catalogue"
                )
            name = row.get("id")
            if (
                not isinstance(name, str)
                or not 1 <= len(name) <= 256
                or any(ord(c) < 32 or ord(c) == 127 for c in name)
            ):
                raise TokenError(
                    "New API 模型 ID 无效。", 502, reason="model_catalogue"
                )
            if limits is not None and name not in limits:
                continue
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
            entries[name] = {"id": name, "protocols": protocols}
        return [entries[k] for k in sorted(entries)]
