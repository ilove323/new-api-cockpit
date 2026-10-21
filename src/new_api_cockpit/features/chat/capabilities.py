"""Explicit protocol capabilities, intersected with the current owner's catalog."""

import hashlib
import json
from pathlib import Path

from . import config
from .errors import ChatError
from .protocols import PROTOCOLS, wire_body


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError()
        result[key] = value
    return result


def canonical(value):
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode()


class Capabilities:
    def __init__(self, filename=""):
        self.filename = filename

    def load(self):
        if not self.filename:
            return {}
        try:
            with Path(self.filename).open("rb") as file:
                raw = file.read(65537)
            if len(raw) > 65536:
                raise ValueError()
            body = json.loads(raw, object_pairs_hook=_pairs)
            if (
                not isinstance(body, dict)
                or set(body) != {"schema_version", "models"}
                or type(body["schema_version"]) is not int
                or body["schema_version"] not in {1, 2}
                or not isinstance(body["models"], dict)
                or len(body["models"]) > 10000
            ):
                raise ValueError()
            for name, policy in body["models"].items():
                if (
                    not isinstance(name, str)
                    or not 1 <= len(name.encode()) <= 256
                    or any(ord(c) < 32 or ord(c) == 127 for c in name)
                ):
                    raise ValueError()
                old = {
                    "chat",
                    "tools",
                    "output_token_parameter",
                    "stream_usage",
                    "max_input_bytes",
                }
                new = {
                    "protocol",
                    "max_output_tokens",
                    "compaction",
                    "reasoning_effort",
                    "reasoning",
                    "thinking",
                }
                if not isinstance(policy, dict) or set(policy) - (
                    old | new if body["schema_version"] == 2 else old
                ):
                    raise ValueError()
                for key in ("chat", "tools", "stream_usage"):
                    if key in policy and type(policy[key]) is not bool:
                        raise ValueError()
                if policy.get("output_token_parameter", "max_tokens") not in {
                    "max_tokens",
                    "max_completion_tokens",
                }:
                    raise ValueError()
                protocol = policy.get("protocol", "chat_completions")
                if protocol not in PROTOCOLS:
                    raise ValueError()
                maximum = policy.get("max_input_bytes", config.CONTEXT_BYTES)
                ceiling = (
                    config.EXTENDED_CONTEXT_BYTES
                    if body["schema_version"] == 2
                    else config.CONTEXT_BYTES
                )
                if type(maximum) is not int or not 1 <= maximum <= ceiling:
                    raise ValueError()
                maximum = policy.get(
                    "max_output_tokens", config.OUTPUT_TOKENS_PER_REQUEST
                )
                if (
                    type(maximum) is not int
                    or not 1 <= maximum <= config.MAX_OUTPUT_TOKENS
                ):
                    raise ValueError()
                compaction = policy.get("compaction", "summary")
                if (
                    compaction not in {"none", "summary", "native"}
                    or compaction == "native"
                    and protocol != "responses"
                ):
                    raise ValueError()
                wire_body(
                    protocol,
                    {
                        "model": name,
                        "messages": [{"role": "user", "content": "validate"}],
                        "max_tokens": maximum,
                        **{
                            key: policy[key]
                            for key in (
                                "reasoning_effort",
                                "reasoning",
                                "thinking",
                                "stream_options",
                            )
                            if key in policy
                        },
                    },
                )
                if protocol != "chat_completions" and policy.get("stream_usage", False):
                    raise ValueError()
            return body["models"]
        except (OSError, ValueError, TypeError, UnicodeError, ChatError):
            raise ChatError(
                "聊天模型能力配置无效。", 503, "CHAT_CONFIG_INVALID"
            ) from None

    def catalog(self, rows):
        policies = self.load()
        result = []
        for row in rows:
            name, advertised = row["id"], row.get("protocols", [])
            policy = policies.get(name, {})
            # Trust only explicit catalog metadata, never a model-name heuristic.
            inferred = next(
                (
                    p
                    for label, p in (
                        ("responses", "responses"),
                        ("anthropic", "anthropic"),
                        ("chat", "chat_completions"),
                    )
                    if label in advertised
                ),
                None,
            )
            protocol = policy.get("protocol", inferred or "chat_completions")
            chat = policy.get("chat", bool(inferred) or "protocol" in policy)
            effective = {
                "protocol": protocol,
                "chat": chat,
                "tools": chat and policy.get("tools", False),
                "output_token_parameter": policy.get(
                    "output_token_parameter", "max_tokens"
                ),
                "stream_usage": policy.get("stream_usage", False),
                "max_input_bytes": policy.get("max_input_bytes", config.CONTEXT_BYTES),
                "max_output_tokens": policy.get(
                    "max_output_tokens", config.OUTPUT_TOKENS_PER_REQUEST
                ),
                "compaction": policy.get("compaction", "summary"),
                **{
                    key: policy[key]
                    for key in ("reasoning_effort", "reasoning", "thinking")
                    if key in policy
                },
            }
            revision = (
                "sha256:"
                + hashlib.sha256(
                    canonical(
                        {
                            "id": name,
                            "protocols": sorted(advertised),
                            "policy": effective,
                        }
                    )
                ).hexdigest()
            )
            result.append(
                {
                    "id": name,
                    "protocol": protocol,
                    "chat_enabled": chat,
                    "tools_enabled": effective["tools"],
                    "reason": ""
                    if chat
                    else "协议未确认或已禁用；需要站点明确配置模型协议。",
                    "capability_revision": revision,
                    "candidate_limit": config.MAX_CANDIDATES,
                    "native_candidates": protocol == "chat_completions",
                    "compaction": effective["compaction"],
                    "max_input_bytes": effective["max_input_bytes"],
                    "policy": effective,
                }
            )
        return result

    def select(self, rows, model, revision, mode):
        entry = next((row for row in self.catalog(rows) if row["id"] == model), None)
        if entry is None or not entry["chat_enabled"]:
            raise ChatError(
                "所选模型不可用或协议未确认。", 409, "CHAT_MODEL_UNSUPPORTED"
            )
        if entry["capability_revision"] != revision:
            raise ChatError(
                "模型能力已变化，请刷新后再提交。", 409, "CHAT_CAPABILITY_CHANGED"
            )
        if mode == "files" and not entry["tools_enabled"]:
            raise ChatError("该模型未确认支持文件工具。", 409, "CHAT_TOOLS_UNSUPPORTED")
        return entry
