"""Own-token tutorials: delegate ownership to New API, without PAT provisioning.

Only fixed token-management and model-list endpoints are called. No generation,
database writes, key persistence, automatic retries or user-supplied upstreams.
"""

from http.client import HTTPConnection, HTTPSConnection

from . import personal_tokens
from .integrations.new_api import json_client
from .personal_tokens import TokenError as ManualError

MAX_TOKENS = 2000
PROTOCOLS = {"openai": "chat", "openai-response": "responses", "anthropic": "anthropic"}


def upstream(path, credential, user_id, *, body=None, model_key=False):
    return json_client.upstream(
        path,
        credential,
        user_id,
        body=body,
        model_key=model_key,
        connection_types=(HTTPConnection, HTTPSConnection),
    )


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
