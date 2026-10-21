"""Pure, deliberately restricted single-file Skill parsing; never executes resources."""

from dataclasses import dataclass
import hashlib
import json
import re

from . import config
from .errors import ChatError


@dataclass(frozen=True)
class ParsedSkill:
    normalized_content: str
    name: str
    description: str
    sha256: str
    size_bytes: int


def normalize_text(content, *, strip_bom=True):
    """Normalize one leading BOM and newlines without trimming user text."""
    if not isinstance(content, str):
        raise ChatError()
    if strip_bom:
        content = content.removeprefix("\ufeff")
    content = content.replace("\r\n", "\n").replace("\r", "\n")
    try:
        content.encode("utf-8", errors="strict")
    except UnicodeError:
        raise ChatError("文本不是有效的 UTF-8。") from None
    if any(ord(c) < 32 and c not in "\n\t" for c in content):
        raise ChatError("文本包含不支持的控制字符。")
    return content


def _format(field, line, reason):
    return ChatError(
        "Skill 格式无效。",
        422,
        "CHAT_SKILL_FORMAT_INVALID",
        details={"field": field, "line": line, "reason": reason},
    )


def parse_skill(content):
    """Parse exactly two ordered frontmatter fields, not general YAML."""
    try:
        content = normalize_text(content)
    except ChatError as exc:
        if not isinstance(content, str):
            raise
        raise _format("content", 1, "invalid_text") from exc
    raw = content.encode("utf-8")
    if len(raw) > config.SKILL_FILE_BYTES:
        raise ChatError(
            "Skill 超出大小限制。",
            413,
            "CHAT_SKILL_TOO_LARGE",
            details={"limit_bytes": config.SKILL_FILE_BYTES},
        )
    lines = content.split("\n", 4)
    if len(lines) != 5 or lines[0] != "---":
        raise _format("frontmatter", 1, "invalid_delimiter")
    if not lines[1].startswith("name: "):
        raise _format("name", 2, "field_required")
    name = lines[1][6:]
    if len(name) > config.SKILL_NAME_CHARS or not re.fullmatch(
        r"[a-z0-9]+(?:-[a-z0-9]+)*", name
    ):
        raise _format("name", 2, "invalid_name")
    if not lines[2].startswith("description: "):
        raise _format("description", 3, "field_required")
    encoded_description = lines[2][13:]
    if not encoded_description.startswith('"') or not encoded_description.endswith('"'):
        raise _format("description", 3, "invalid_json_string")
    try:
        description = json.loads(encoded_description)
    except (ValueError, RecursionError):
        raise _format("description", 3, "invalid_json_string") from None
    if not isinstance(description, str):
        raise _format("description", 3, "invalid_json_string")
    if (
        not 1 <= len(description) <= config.SKILL_DESCRIPTION_CHARS
        or not description.strip()
        or any(ord(c) < 32 for c in description)
    ):
        raise _format("description", 3, "invalid_description")
    try:
        description.encode("utf-8", errors="strict")
    except UnicodeError:
        raise _format("description", 3, "invalid_description") from None
    if lines[3] != "---":
        raise _format("frontmatter", 4, "invalid_delimiter")
    if not lines[4].strip():
        raise _format("body", 5, "empty_body")
    return ParsedSkill(
        content, name, description, hashlib.sha256(raw).hexdigest(), len(raw)
    )
