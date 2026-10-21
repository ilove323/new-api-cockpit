"""Owner-only token adapter injected into independent feature modules."""

from contextlib import contextmanager

from . import json_client, personal_tokens
from .personal_tokens import TokenError

MAX_TOKENS = 2000
PROTOCOLS = {"openai": "chat", "openai-response": "responses", "anthropic": "anthropic"}


class ModelKey(str):
    def __repr__(self):
        return "<ModelKey redacted>"


class TokenProvider:
    def __init__(self, upstream=None):
        self.upstream = upstream or json_client.upstream

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

    @contextmanager
    def lease_key(self, access, token_id, *, group=None):
        key = ModelKey(
            personal_tokens.key_for(
                access.principal.as_user(),
                access.credential,
                {"token_id": token_id},
                upstream=self.upstream,
                group=group,
            )
        )
        try:
            yield key
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
