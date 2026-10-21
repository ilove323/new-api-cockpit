"""Strict browser request DTOs. Owner identity is never a request field."""

import re
from uuid import UUID

from . import config
from .errors import ChatError


def object_fields(body, required, optional=()):
    if (
        not isinstance(body, dict)
        or set(body) - set(required) - set(optional)
        or set(required) - set(body)
    ):
        raise ChatError("请求字段无效或缺失。")
    return body


def identifier(value):
    try:
        parsed = UUID(value) if isinstance(value, str) else None
        if parsed is None or parsed.version != 4 or str(parsed) != value:
            raise ValueError()
    except (ValueError, AttributeError):
        raise ChatError("标识格式无效。") from None
    return value


def integer(value, minimum=0, maximum=9007199254740991):
    if type(value) is not int or not minimum <= value <= maximum:
        raise ChatError("数值参数无效。")
    return value


def text(value, maximum, *, empty=False):
    try:
        if (
            not isinstance(value, str)
            or len(value.encode("utf-8")) > maximum
            or (not empty and not value.strip())
            or any(ord(c) < 32 and c not in "\n\t\r" for c in value)
        ):
            raise ValueError()
    except (ValueError, UnicodeError):
        raise ChatError("文本无效或超过大小上限。") from None
    return value


def title(value):
    value = text(value, config.TITLE_CHARS * 4, empty=True).strip()
    if len(value) > config.TITLE_CHARS or any(ord(c) < 32 for c in value):
        raise ChatError("聊天标题过长或含控制字符。")
    return value or "新聊天"


def id_list(value, maximum):
    if not isinstance(value, list) or len(value) > maximum:
        raise ChatError("文件或Skill列表无效。")
    result = [identifier(v) for v in value]
    if len(set(result)) != len(result):
        raise ChatError("列表中存在重复标识。")
    return result


def parse_run(body):
    object_fields(
        body,
        {
            "client_request_id",
            "expected_revision",
            "token_id",
            "model",
            "capability_revision",
            "mode",
            "text",
            "attachment_ids",
            "context_revision",
            "skill_ids",
        },
        {"candidate_count", "web_enabled", "auto_memory", "context_strategy"},
    )
    result = dict(body)
    identifier(result["client_request_id"])
    integer(result["expected_revision"], 1)
    integer(result["context_revision"])
    integer(result["token_id"], 1, 2147483647)
    text(result["text"], config.USER_TEXT_BYTES)
    text(result["model"], 256)
    if any(ord(c) < 32 for c in result["model"]) or not re.fullmatch(
        r"sha256:[a-f0-9]{64}", str(result["capability_revision"])
    ):
        raise ChatError("模型或能力版本无效。")
    result["attachment_ids"] = id_list(
        result["attachment_ids"], config.ATTACHMENTS_PER_TURN
    )
    result["skill_ids"] = id_list(result["skill_ids"], config.SKILLS_PER_RUN)
    if result["mode"] not in ("chat", "files") or (
        result["mode"] == "chat" and result["attachment_ids"]
    ):
        raise ChatError("聊天模式或附件无效。")
    validate_options(result)
    return result


OPTION_DEFAULTS = {
    "candidate_count": 1,
    "web_enabled": False,
    "auto_memory": False,
    "context_strategy": "recent",
}


def validate_options(value):
    count = integer(value.get("candidate_count", 1), 1, config.MAX_CANDIDATES)
    for name in ("web_enabled", "auto_memory"):
        if name in value and type(value[name]) is not bool:
            raise ChatError("聊天扩展选项无效。")
    if value.get("context_strategy", "recent") not in {"recent", "adaptive"}:
        raise ChatError("上下文策略无效。")
    if value.get("mode") == "files" and count != 1:
        raise ChatError("Agent 仅支持单一工具执行分支。")
    if value.get("web_enabled", False) and value.get("mode") != "files":
        raise ChatError("联网工具仅在 Agent 模式提供。")


def run_options(value):
    """Default-equivalent old/new requests retain their original idempotency hash."""
    return {
        key: value[key]
        for key, default in OPTION_DEFAULTS.items()
        if key in value and value[key] != default
    }
