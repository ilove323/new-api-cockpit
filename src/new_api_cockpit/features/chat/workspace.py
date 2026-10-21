"""The four bounded text-file tools; no raw filesystem path is exposed."""

from functools import wraps
import re
import time
from uuid import uuid4

from . import config
from .errors import ChatError
from .fs import canonical_json
from .schemas import identifier, integer, text, id_list


_LINE_END = re.compile(rb"\r\n|[\n\r\v\f\x1c-\x1e]|\xc2\x85|\xe2\x80[\xa8\xa9]")


def _line_spans(raw):
    """UTF-8 splitlines(keepends=True) spans without allocating every line."""
    start = 0
    for match in _LINE_END.finditer(raw):
        yield start, match.end()
        start = match.end()
    if start < len(raw):
        yield start, len(raw)


def tool(function):
    @wraps(function)
    def guarded(*args, **kwargs):
        try:
            result = function(*args, **kwargs)
            if len(canonical_json(result)) > config.TOOL_RESULT_BYTES:
                return {
                    "ok": False,
                    "code": "TOOL_RESULT_TOO_LARGE",
                    "message": "结果超过大小限制。",
                }
            return result
        except ChatError as exc:
            if exc.code.startswith("CHAT_STORAGE_"):
                raise
            return {"ok": False, "code": exc.code, "message": str(exc)}

    return guarded


class Workspace:
    def __init__(self, store, scope, lease=None, run_id=None):
        store._check_scope(scope)
        self.store, self.scope, self.lease, self.run_id = store, scope, lease, run_id
        if lease is not None:
            lease.validate(store, scope)
        if run_id is not None:
            identifier(run_id)

    @tool
    def list_files(self, kind="all", offset=0):
        if kind not in {"all", "upload", "output"}:
            raise ChatError("文件类型无效。")
        integer(offset, 0, config.FILES_PER_CONVERSATION)
        files = [
            f
            for f in self.store.files(self.scope, lease=self.lease)
            if kind == "all" or f["kind"] == kind
        ]
        result = {"ok": True, "files": [], "next_offset": None, "total": len(files)}
        for entry in files[offset : offset + 50]:
            item = {
                "file_id": entry["id"],
                **{k: entry[k] for k in ("name", "kind", "size_bytes", "version")},
            }
            result["files"].append(item)
            if len(canonical_json(result)) > config.TOOL_RESULT_BYTES - 128:
                result["files"].pop()
                break
        nxt = offset + len(result["files"])
        result["next_offset"] = nxt if nxt < len(files) else None
        return result

    @tool
    def read_file(self, file_id, start_line=1, max_lines=config.TOOL_READ_MAX_LINES):
        identifier(file_id)
        integer(start_line, 1)
        integer(max_lines, 1, config.TOOL_READ_MAX_LINES)
        _, raw = self.store.read_file(self.scope, file_id, lease=self.lease)
        deadline = time.monotonic() + config.TOOL_DEADLINE_SECONDS
        result = {
            "ok": True,
            "file_id": file_id,
            "start_line": start_line,
            "end_line": start_line - 1,
            "text": "",
            "truncated": False,
            "next_line": None,
        }
        for number, (start, end) in enumerate(_line_spans(raw), 1):
            if time.monotonic() >= deadline:
                return {
                    "ok": False,
                    "code": "TOOL_TIMEOUT",
                    "message": "文件读取超时，请缩小行号范围。",
                }
            if number < start_line:
                continue
            if number >= start_line + max_lines:
                result["truncated"] = True
                result["next_line"] = result["end_line"] + 1
                break
            previous = result["text"]
            # Do not decode/copy a 50 MiB single line merely to reject it.
            oversized = end - start > config.TOOL_RESULT_BYTES
            if not oversized:
                result["text"] += raw[start:end].decode("utf-8")
            if (
                oversized
                or len(canonical_json(result)) > config.TOOL_RESULT_BYTES - 128
            ):
                result["text"] = previous
                if not previous:
                    return {
                        "ok": False,
                        "code": "TOOL_LINE_TOO_LONG",
                        "message": "单行过长，请使用分行文本。",
                    }
                result["truncated"] = True
                result["next_line"] = result["end_line"] + 1
                break
            result["end_line"] += 1
        return result

    @tool
    def search_files(self, query, file_ids=None, case_sensitive=False):
        if (
            not isinstance(query, str)
            or not 1 <= len(query) <= 200
            or type(case_sensitive) is not bool
        ):
            raise ChatError("检索参数无效。")
        ids = id_list([] if file_ids is None else file_ids, 20)
        entries = self.store.files(self.scope, lease=self.lease)
        known = {f["id"] for f in entries}
        if set(ids) - known:
            raise ChatError("文件不存在。", 404, "CHAT_NOT_FOUND")
        entries = [f for f in entries if not ids or f["id"] in ids]
        result = {
            "ok": True,
            "matches": [],
            "truncated": False,
            "scanned_files": 0,
            "scanned_bytes": 0,
        }
        needle = query if case_sensitive else query.casefold()
        deadline = time.monotonic() + config.TOOL_DEADLINE_SECONDS
        for entry in entries:
            if (
                time.monotonic() >= deadline
                or result["scanned_bytes"] + entry["size_bytes"]
                > config.TOOL_SEARCH_SCAN_BYTES
            ):
                result["truncated"] = True
                break
            _, raw = self.store.read_file(self.scope, entry["id"], lease=self.lease)
            result["scanned_files"] += 1
            result["scanned_bytes"] += len(raw)
            for number, line in enumerate(raw.decode("utf-8").splitlines(), 1):
                if time.monotonic() >= deadline:
                    result["truncated"] = True
                    return result
                if needle in (line if case_sensitive else line.casefold()):
                    item = {"file_id": entry["id"], "line": number, "text": line[:500]}
                    result["matches"].append(item)
                    if len(canonical_json(result)) > config.TOOL_RESULT_BYTES - 128:
                        result["matches"].pop()
                        result["truncated"] = True
                        return result
                    if len(result["matches"]) >= 100:
                        result["truncated"] = True
                        return result
        return result

    @tool
    def write_file(
        self, name, content, call_id, *, source_tool="write_file", source_arguments=None
    ):
        if self.lease is None or self.run_id is None:
            raise ChatError("文件写入需要本轮运行租约。", 409, "CHAT_CONVERSATION_BUSY")
        self.lease.validate(self.store, self.scope)
        text(call_id, 200)
        text(content, config.TOOL_WRITE_BYTES, empty=True)
        state = self.store._state(self.scope, lease=self.lease)
        _, files, runs, _, writes = self.store._projection(self.scope, state)
        if self.run_id not in runs or runs[self.run_id]["status"] in {
            "completed",
            "failed",
            "cancelled",
            "interrupted",
        }:
            raise ChatError("运行已结束。", 409, "CHAT_CONVERSATION_BUSY")
        key = (self.run_id, call_id)
        expected = {"name": name, "content": content}
        if source_tool == "download_url":
            if (
                not isinstance(source_arguments, dict)
                or set(source_arguments) != {"url", "name"}
                or source_arguments["name"] != name
            ):
                raise ChatError("下载文件来源无效。")
            text(source_arguments["url"], 4096)
            expected = dict(source_arguments)
        elif source_tool != "write_file" or source_arguments is not None:
            raise ChatError("文件写入来源无效。")
        call = next(
            (
                e
                for e in state.events
                if e["run_id"] == self.run_id
                and e["type"] == "tool.call"
                and e["data"]["call_id"] == call_id
            ),
            None,
        )
        if call is not None and (
            call["data"]["name"] != source_tool or call["data"]["arguments"] != expected
        ):
            raise ChatError("工具调用ID内容冲突。", 409, "CHAT_IDEMPOTENCY_CONFLICT")
        if key in writes:
            entry = files[writes[key]]
        else:
            # Runner normally wrote tool.call first. Direct callers still get
            # durable (rid,call_id) deduplication, not an in-memory-only cache.
            if call is None:
                if runs[self.run_id]["tool_calls"] >= config.MAX_TOOL_CALLS:
                    raise ChatError("本轮工具次数已达上限。", 409, "CHAT_RUN_LIMIT")
                self.store.normalize_file(name, content, "output")
                self.store.append(
                    self.scope,
                    "tool.call",
                    {"call_id": call_id, "name": source_tool, "arguments": expected},
                    run_id=self.run_id,
                    lease=self.lease,
                )
            entry = self.store._add_file(
                self.scope,
                name,
                content,
                str(uuid4()),
                "output",
                lease=self.lease,
                run_id=self.run_id,
            )
        return {
            "ok": True,
            "file_id": entry["id"],
            **{
                k: entry[k]
                for k in ("name", "version", "size_bytes", "sha256", "download_path")
            },
        }
