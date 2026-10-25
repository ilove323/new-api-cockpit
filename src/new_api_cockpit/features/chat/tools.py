"""Four fixed, inert file tools. Workspace owns file scope and transactions.

run_context contains run_id/call_id and optionally deadline (absolute monotonic),
abort (check()), and stop_event (is_set()). It may be a dict or attribute DTO.
Cancellation/deadline errors escape to the runner; local tool errors are JSON.
"""

import copy
import json
import re
import time

from . import config, schemas as validation
from .errors import ChatError

_MESSAGES = {
    "TOOL_UNKNOWN": "不支持此工具。",
    "TOOL_INVALID_ARGUMENTS": "工具参数无效。",
    "TOOL_FILE_NOT_FOUND": "文件不存在或不属于当前聊天。",
    "TOOL_FILE_DELETED": "附件已被用户删除，不能继续读取或引用原件。",
    "TOOL_LINE_TOO_LONG": "单行内容过长，请使用分行版本。",
    "TOOL_RESULT_LIMIT": "工具结果超过大小限制。",
    "TOOL_STORAGE_LIMIT": "文件存储额度不足。",
    "TOOL_TIMEOUT": "工具执行超时。",
    "TOOL_OUTPUT_INVALID": "文件生成失败：PDF/DOCX 的 content 用 Markdown；XLSX 用 sheets/rows JSON；图片用 width/height/elements JSON。不要写二进制、base64、HTML 或改名伪装文件。请检查格式与内容大小。",
}


def schemas():
    """Fresh OpenAI tool objects, exactly the documented schema whitelist."""
    definitions = [
        (
            "list_files",
            "列举当前聊天已提交的文件，不读取其他聊天或个人设置。",
            {
                "kind": {"type": "string", "enum": ["all", "upload", "output"]},
                "offset": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": config.FILES_PER_CONVERSATION,
                },
            },
            ["kind", "offset"],
        ),
        (
            "read_file",
            "分行读取当前聊天的文本或 PDF/Word/Excel 提取正文；图片需在消息中引用以视觉读取。",
            {
                "file_id": {"type": "string", "format": "uuid"},
                "start_line": {"type": "integer", "minimum": 1},
                "max_lines": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": config.TOOL_READ_MAX_LINES,
                },
            },
            ["file_id", "start_line", "max_lines"],
        ),
        (
            "search_files",
            "在当前聊天文件中检索字面子串；不是正则或命令。",
            {
                "query": {"type": "string", "minLength": 1, "maxLength": 200},
                "file_ids": {
                    "type": "array",
                    "items": {"type": "string", "format": "uuid"},
                    "maxItems": 20,
                    "uniqueItems": True,
                },
                "case_sensitive": {"type": "boolean"},
            },
            ["query", "file_ids", "case_sensitive"],
        ),
        (
            "write_file",
            "在当前聊天生成真实文件并加入右侧输出内容，返回下载文件ID，不覆盖原件。"
            "txt/md/csv等文本直接写content；pdf/docx由服务器将Markdown排版为真正PDF/Word，支持标题、段落、列表、加粗、Markdown表格（最多8列、每格200字）。"
            'xlsx的content是JSON字符串，例如 {"sheets":[{"name":"统计","rows":[["项目","金额"],["A",12],["合计",{"formula":"=SUM(B2:B2)"}]]}]}；'
            "字符串单元格均按文本保存，公式只能显式formula对象，支持本表SUM/AVERAGE/MIN/MAX/COUNT/COUNTA/ROUND/ROUNDUP/ROUNDDOWN/IF/ABS与四则运算，由Excel打开计算。"
            'png/jpg/jpeg/webp为服务器绘图，content是JSON字符串，例如 {"width":800,"height":500,"background":"#FFFFFF","elements":[{"type":"text","x":30,"y":20,"text":"标题","size":28,"color":"#172033"},{"type":"rect","box":[30,90,200,300],"fill":"#2563EB"}]}；'
            "元素支持text(x,y,text,size,color)、rect/ellipse(box:[x1,y1,x2,y2],fill,outline,width)、line(box,outline,width)，颜色#RRGGBB，64..4096像素且总像素不超过800万。"
            "图片输出是实际像素的图表/示意绘图，不是摄影文生图接口；不伪称已调用生图模型。"
            "禁止执行代码、访问外链/文件路径、宏或改扩展名。真实二进制上限4MiB；内容描述最多64KiB。只有工具成功才告诉用户已生成。",
            {
                "name": {"type": "string", "minLength": 1, "maxLength": 120},
                "content": {"type": "string", "maxLength": config.TOOL_WRITE_BYTES},
            },
            ["name", "content"],
        ),
    ]
    return [
        {
            "type": "function",
            "function": {
                "name": name,
                "description": desc,
                "parameters": {
                    "type": "object",
                    "properties": props,
                    "required": required,
                    "additionalProperties": False,
                },
            },
        }
        for name, desc, props, required in definitions
    ]


def _error(code):
    return {"ok": False, "code": code, "message": _MESSAGES[code]}


def _workspace_error(code):
    if code == "TOOL_STORAGE_LIMIT":
        return _error(code)
    if isinstance(code, str) and code.startswith("CHAT_STORAGE_"):
        raise ChatError("文件存储暂不可用。", 503, code)
    aliases = {
        "CHAT_NOT_FOUND": "TOOL_FILE_NOT_FOUND",
        "CHAT_FILE_DELETED": "TOOL_FILE_DELETED",
        "TOOL_RESULT_TOO_LARGE": "TOOL_RESULT_LIMIT",
        "CHAT_INVALID_REQUEST": "TOOL_INVALID_ARGUMENTS",
    }
    code = aliases.get(code, code) if isinstance(code, str) else "TOOL_RESULT_LIMIT"
    return _error(code if code in _MESSAGES else "TOOL_RESULT_LIMIT")


def _get(context, key, default=None):
    return (
        context.get(key, default)
        if isinstance(context, dict)
        else getattr(context, key, default)
    )


def _check(context):
    abort = _get(context, "abort")
    if abort is not None:
        abort.check()
    stop = _get(context, "stop_event")
    if stop is not None and stop.is_set():
        raise ChatError("本轮已取消。", 409, "CHAT_CANCELLED")
    deadline = _get(context, "deadline")
    if deadline is not None and time.monotonic() >= deadline:
        raise ChatError("本轮已超过截止时间。", 504, "CHAT_DEADLINE")


def _summary_name(name):
    validation.text(name, 480)
    if (
        not 1 <= len(name) <= 120
        or name in {".", ".."}
        or re.search(r"[\\/\x00-\x1f\x7f]", name)
        or "." not in name
        or ("." + name.rsplit(".", 1)[1].lower()) not in config.ALLOWED_EXTENSIONS
    ):
        raise ValueError()
    return name


def _filename(name):
    _summary_name(name)
    if (
        ("." + name.rsplit(".", 1)[1].lower()) not in config.OUTPUT_EXTENSIONS
        or name.strip() != name
        or re.search(r'[:<>"|?*]', name)
        or name.endswith((".", " "))
        or name.startswith(".")
    ):
        raise ValueError()
    return name


def _arguments(name, args):
    spec = next(
        t["function"]["parameters"] for t in schemas() if t["function"]["name"] == name
    )
    validation.object_fields(args, spec["required"])
    if (
        len(json.dumps(args, ensure_ascii=False, allow_nan=False).encode())
        > config.TOOL_ARGUMENT_BYTES
    ):
        raise ValueError()
    if name == "list_files":
        if args["kind"] not in ("all", "upload", "output"):
            raise ValueError()
        validation.integer(args["offset"], 0, config.FILES_PER_CONVERSATION)
    elif name == "read_file":
        validation.identifier(args["file_id"])
        validation.integer(args["start_line"], 1)
        validation.integer(args["max_lines"], 1, config.TOOL_READ_MAX_LINES)
    elif name == "search_files":
        query = validation.text(args["query"], 800)
        if len(query) > 200 or type(args["case_sensitive"]) is not bool:
            raise ValueError()
        validation.id_list(args["file_ids"], 20)
    else:
        _filename(args["name"])
        validation.text(args["content"], config.TOOL_WRITE_BYTES, empty=True)
    # Freeze against caller mutation while Workspace reads these arguments.
    return copy.deepcopy(args)


def _safe_result(name, result):
    fields = {
        "list_files": ("files", "next_offset", "total"),
        "read_file": (
            "file_id",
            "start_line",
            "end_line",
            "text",
            "truncated",
            "next_line",
        ),
        "search_files": ("matches", "truncated", "scanned_files", "scanned_bytes"),
        "write_file": (
            "file_id",
            "name",
            "version",
            "size_bytes",
            "sha256",
            "download_path",
        ),
    }
    if not isinstance(result, dict):
        raise ValueError()
    if "ok" in result and type(result["ok"]) is not bool:
        raise ValueError()
    if result.get("ok") is False:
        return _workspace_error(result.get("code"))
    clean = {key: result[key] for key in fields[name]}
    if name == "list_files":
        if not isinstance(clean["files"], list) or len(clean["files"]) > 50:
            raise ValueError()
        clean["files"] = [
            {
                key: f[key]
                for key in ("file_id", "name", "kind", "size_bytes", "version")
            }
            for f in clean["files"]
        ]
        for file in clean["files"]:
            validation.identifier(file["file_id"])
            _summary_name(file["name"])
            if file["kind"] not in ("upload", "output"):
                raise ValueError()
            validation.integer(file["size_bytes"], 0, config.UPLOAD_FILE_BYTES)
            if file["kind"] == "output" or file["version"] is not None:
                validation.integer(file["version"], 1)
        validation.integer(clean["total"], 0, config.FILES_PER_CONVERSATION)
        if clean["next_offset"] is not None:
            validation.integer(clean["next_offset"], 0, config.FILES_PER_CONVERSATION)
    elif name == "read_file":
        validation.identifier(clean["file_id"])
        validation.integer(clean["start_line"], 1)
        validation.integer(clean["end_line"], clean["start_line"] - 1)
        validation.text(clean["text"], config.TOOL_RESULT_BYTES, empty=True)
        if type(clean["truncated"]) is not bool:
            raise ValueError()
        if clean["next_line"] is not None:
            validation.integer(clean["next_line"], clean["end_line"] + 1)
    elif name == "search_files":
        if not isinstance(clean["matches"], list) or len(clean["matches"]) > 100:
            raise ValueError()
        clean["matches"] = [
            {k: m[k] for k in ("file_id", "line", "text")} for m in clean["matches"]
        ]
        for match in clean["matches"]:
            validation.identifier(match["file_id"])
            validation.integer(match["line"], 1)
            validation.text(match["text"], 2000, empty=True)
            if len(match["text"]) > 500:
                raise ValueError()
        if type(clean["truncated"]) is not bool:
            raise ValueError()
        validation.integer(clean["scanned_files"], 0, config.FILES_PER_CONVERSATION)
        validation.integer(clean["scanned_bytes"], 0, config.TOOL_SEARCH_SCAN_BYTES)
    else:
        validation.identifier(clean["file_id"])
        _filename(clean["name"])
        validation.integer(clean["version"], 1)
        validation.integer(clean["size_bytes"], 0, config.OUTPUT_FILE_BYTES)
        if not re.fullmatch(r"[a-f0-9]{64}", clean["sha256"]):
            raise ValueError()
        if not re.fullmatch(
            r"/cockpit/api/chat/conversations/[a-f0-9-]{36}/files/"
            + clean["file_id"]
            + r"/download",
            clean["download_path"],
        ):
            raise ValueError()
    clean["ok"] = True
    if (
        len(
            json.dumps(
                clean, ensure_ascii=False, separators=(",", ":"), allow_nan=False
            ).encode()
        )
        > config.TOOL_RESULT_BYTES
    ):
        return _error("TOOL_RESULT_LIMIT")
    return clean


def execute(name, args, workspace, run_context):
    """Validate before dispatch; never getattr a model-selected method name.

    Workspace.write_file owns persistent call_id deduplication (not a process
    cache), so a repeated executor entry cannot create a second output version.
    Workspace must itself bound reads/scans; no unsafe timeout worker is spawned.
    """
    _check(run_context)
    if name not in ("list_files", "read_file", "search_files", "write_file"):
        return _error("TOOL_UNKNOWN")
    try:
        args = _arguments(name, args)
        validation.identifier(_get(run_context, "run_id"))
        call_id = _get(run_context, "call_id")
        if not isinstance(call_id, str) or not re.fullmatch(
            r"[A-Za-z0-9_-]{1,128}", call_id
        ):
            raise ValueError()
    except (ChatError, ValueError, TypeError, KeyError, UnicodeError, RecursionError):
        return _error("TOOL_INVALID_ARGUMENTS")
    _check(run_context)
    started = time.monotonic()
    try:
        if name == "list_files":
            result = workspace.list_files(args["kind"], args["offset"])
        elif name == "read_file":
            result = workspace.read_file(
                args["file_id"], args["start_line"], args["max_lines"]
            )
        elif name == "search_files":
            result = workspace.search_files(
                args["query"], args["file_ids"], args["case_sensitive"]
            )
        else:
            result = workspace.write_file(args["name"], args["content"], call_id)
    except ChatError as exc:
        _check(run_context)
        if exc.code == "TOOL_STORAGE_LIMIT":
            return _error("TOOL_STORAGE_LIMIT")
        if (
            exc.status >= 500
            or exc.code.startswith("CHAT_STORAGE_")
            or exc.code in {"CHAT_CANCELLED", "CHAT_DEADLINE"}
        ):
            raise
        return _workspace_error(exc.code)
    except (OSError, RuntimeError):
        _check(run_context)
        # An unexpected I/O failure is not a recoverable model/tool error.
        raise ChatError("文件存储暂不可用。", 503, "CHAT_STORAGE_UNAVAILABLE") from None
    except (ValueError, TypeError, KeyError):
        _check(run_context)
        return _error("TOOL_RESULT_LIMIT")
    _check(run_context)
    if time.monotonic() - started > (
        config.OUTPUT_RENDER_SECONDS + config.DOCUMENT_PARSE_SECONDS
        if name == "write_file"
        else config.TOOL_DEADLINE_SECONDS
    ):
        return _error("TOOL_TIMEOUT")
    try:
        return _safe_result(name, result)
    except ChatError as exc:
        if exc.status >= 500 or exc.code.startswith("CHAT_STORAGE_"):
            raise
        return _error("TOOL_RESULT_LIMIT")
    except (ValueError, TypeError, KeyError, UnicodeError, RecursionError):
        return _error("TOOL_RESULT_LIMIT")
