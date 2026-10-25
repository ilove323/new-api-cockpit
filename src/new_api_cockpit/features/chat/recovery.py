"""Validation and deterministic projections; recovery never dispatches a model."""

from dataclasses import dataclass, field
import hashlib
import re

from . import config
from .errors import ChatError
from .fs import canonical_json, strict_json
from .schemas import (
    identifier,
    integer,
    object_fields,
    text,
    title,
    id_list,
    run_options,
    validate_options,
)

TERMINAL = frozenset({"completed", "failed", "cancelled", "interrupted"})
FIELDS = {
    "conversation.created": {"owner_user_id", "instance_id", "title"},
    "conversation.renamed": {"title"},
    "conversation.skills.updated": {"skill_ids"},
    "file.added": {"file"},
    "run.accepted": {
        "client_request_id",
        "message_id",
        "text",
        "attachment_ids",
        "token_id",
        "model",
        "mode",
        "capability_revision",
        "context_snapshot",
        "context_from_run_ids",
    },
    "run.dispatching": {"request_index", "assistant_message_id"},
    "assistant.delta": {"message_id", "text"},
    "assistant.reasoning": {"message_id", "text", "kind", "field"},
    "assistant.protocol": {"message_id", "protocol", "state"},
    "candidate.selected": {"candidate_index"},
    "context.compacted": {"mode", "covered_run_ids", "source_sha256", "checkpoint"},
    "context.selected": {"mode", "covered_run_ids", "source_sha256", "checkpoint"},
    "memory.proposed": {"intent"},
    "memory.updated": {"audit"},
    "memory.reverted": {"after_sha256", "context_revision", "memory_revision"},
    "run.notice": {"code", "message"},
    "assistant.message": {
        "message_id",
        "text",
        "tool_calls",
        "finish_reason",
        "complete",
    },
    "tool.call": {"call_id", "name", "arguments"},
    "tool.result": {"call_id", "name", "ok", "result"},
    "run.usage": {
        "request_index",
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
    },
    "run.terminal": {
        "status",
        "reason",
        "error_code",
        "uncertain",
        "model_requests",
        "tool_calls",
        "usage",
        "usage_complete",
    },
}
OPTIONAL_FIELDS = {
    "run.accepted": {"options", "protocol", "attachment_mode"},
    "run.dispatching": {"purpose", "candidate_index", "candidate_message_ids"},
    "assistant.delta": {"candidate_index"},
    "assistant.message": {"candidate_index"},
    "assistant.reasoning": {"candidate_index"},
    "assistant.protocol": {"candidate_index"},
    "run.usage": {"purpose"},
}
FILE_FIELDS = {
    "id",
    "kind",
    "name",
    "mime",
    "size_bytes",
    "sha256",
    "created_at",
    "logical_name",
    "version",
    "source_run_id",
    "download_path",
}
HASH_FIELDS = (
    "text",
    "attachment_ids",
    "token_id",
    "model",
    "mode",
    "capability_revision",
    "context_revision",
    "skill_ids",
)
STAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$")


def request_digest(payload):
    options = run_options(payload.get("options", payload))
    return hashlib.sha256(
        canonical_json(
            {
                **{key: payload[key] for key in HASH_FIELDS},
                **({"options": options} if options else {}),
            }
        )
    ).hexdigest()


def _hash(value):
    if not isinstance(value, str) or not re.fullmatch("[a-f0-9]{64}", value):
        raise ChatError("记录哈希无效。")


def validate_file(value, scope):
    object_fields(value, FILE_FIELDS)
    fid = identifier(value["id"])
    if value["kind"] not in {"upload", "output"}:
        raise ChatError("文件类型无效。")
    name = value["name"]
    if (
        not isinstance(name, str)
        or not 1 <= len(name) <= 120
        or any(ord(c) < 32 or c in "/\\\x00" for c in name)
        or name in {".", ".."}
    ):
        raise ChatError("文件名无效。")
    # Old text entries stay compatible. Both binary uploads and generated
    # outputs must match their extension; original bytes are hash-verified.
    from .media import mime_for

    if value["mime"] != mime_for(name, output=value["kind"] == "output"):
        raise ChatError("文件类型无效。")
    integer(value["size_bytes"], 0, config.UPLOAD_FILE_BYTES)
    _hash(value["sha256"])
    if not isinstance(value["created_at"], str) or not STAMP.fullmatch(
        value["created_at"]
    ):
        raise ChatError("时间无效。")
    if value["kind"] == "upload":
        if any(
            value[k] is not None for k in ("logical_name", "version", "source_run_id")
        ):
            raise ChatError("上传版本无效。")
    else:
        if value["logical_name"] != name:
            raise ChatError("输出版本无效。")
        integer(value["version"], 1)
        identifier(value["source_run_id"])
    if (
        value["download_path"]
        != f"/cockpit/api/chat/conversations/{scope.cid}/files/{fid}/download"
    ):
        raise ChatError("文件路径无效。")


def validate_event(event, scope, sequence):
    object_fields(
        event, {"v", "seq", "type", "conversation_id", "run_id", "at", "data"}
    )
    if (
        type(event["v"]) is not int
        or event["v"] != 1
        or type(event["seq"]) is not int
        or event["seq"] != sequence
        or event["conversation_id"] != scope.cid
    ):
        raise ChatError("记录序列无效。")
    kind, data, rid = event["type"], event["data"], event["run_id"]
    if (
        kind not in FIELDS
        or not isinstance(event["at"], str)
        or not STAMP.fullmatch(event["at"])
    ):
        raise ChatError("记录格式无效。")
    object_fields(data, FIELDS[kind], OPTIONAL_FIELDS.get(kind, ()))
    if rid is not None:
        identifier(rid)
    if kind == "conversation.created":
        if (
            sequence != 1
            or rid is not None
            or type(data["owner_user_id"]) is not int
            or data["owner_user_id"] != scope.user_id
            or data["instance_id"] != scope.instance_id
        ):
            raise ChatError("记录归属无效。")
        title(data["title"])
    elif sequence == 1:
        raise ChatError("记录首行无效。")
    elif kind == "conversation.renamed":
        title(data["title"])
    elif kind == "conversation.skills.updated":
        if rid is not None:
            raise ChatError("聊天 Skill 设置不能属于模型运行。")
        id_list(data["skill_ids"], config.SKILLS_PER_RUN)
    elif kind == "file.added":
        validate_file(data["file"], scope)
        if data["file"]["source_run_id"] != rid:
            raise ChatError("文件运行归属无效。")
    else:
        identifier(rid)
        if kind == "run.accepted":
            if (
                "attachment_mode" in data
                and data["attachment_mode"] != "server-text-v1"
            ):
                raise ChatError("附件正文格式无效。")
            identifier(data["client_request_id"])
            identifier(data["message_id"])
            text(data["text"], config.USER_TEXT_BYTES)
            id_list(data["attachment_ids"], config.ATTACHMENTS_PER_TURN)
            coverage = data["context_from_run_ids"]
            options = data.get("options", {})
            maximum = (
                config.CONTEXT_COVERAGE_RUNS
                if isinstance(options, dict)
                and options.get("context_strategy") == "adaptive"
                else config.CONTEXT_HISTORY_RUNS
            )
            id_list(coverage, maximum)
            if len(canonical_json(coverage)) > config.CONTEXT_COVERAGE_BYTES:
                raise ChatError(
                    "上下文来源元信息超过大小限制。", 413, "CHAT_CONTEXT_METADATA_LIMIT"
                )
            integer(data["token_id"], 1, 2147483647)
            text(data["model"], 256)
            if data["mode"] not in {"chat", "files"}:
                raise ChatError("模式无效。")
            if not re.fullmatch(
                r"sha256:[a-f0-9]{64}", str(data["capability_revision"])
            ):
                raise ChatError("能力版本无效。")
            snap = object_fields(
                data["context_snapshot"],
                {"file", "sha256", "context_revision", "skill_ids"},
            )
            if snap["file"] != f"contexts/{rid}.json":
                raise ChatError("快照路径无效。")
            _hash(snap["sha256"])
            integer(snap["context_revision"])
            id_list(snap["skill_ids"], config.SKILLS_PER_RUN)
            if "protocol" in data:
                from .protocols import PROTOCOLS

                if data["protocol"] not in PROTOCOLS:
                    raise ChatError("运行协议无效。")
            if "options" in data:
                from .schemas import OPTION_DEFAULTS

                object_fields(data["options"], set(), set(OPTION_DEFAULTS))
                validate_options({**data["options"], "mode": data["mode"]})
        elif kind == "run.dispatching":
            integer(data["request_index"], 1, config.MAX_TOTAL_MODEL_REQUESTS)
            identifier(data["assistant_message_id"])
            if data.get("purpose", "main") not in {
                "main",
                "summary",
                "native_compact",
                "memory",
            }:
                raise ChatError("模型调用用途无效。")
            if "candidate_message_ids" in data:
                id_list(data["candidate_message_ids"], config.MAX_CANDIDATES)
                if (
                    not data["candidate_message_ids"]
                    or data["candidate_message_ids"][0] != data["assistant_message_id"]
                ):
                    raise ChatError("候选消息标识无效。")
        elif kind in {
            "assistant.delta",
            "assistant.message",
            "assistant.reasoning",
            "assistant.protocol",
        }:
            identifier(data["message_id"])
            if kind == "assistant.reasoning":
                text(data["text"], config.REASONING_BYTES, empty=True)
                if data["kind"] not in {"raw", "summary"} or data["field"] not in {
                    "reasoning_content",
                    "reasoning",
                    "thinking",
                }:
                    raise ChatError("公开推理事件无效。")
            elif kind == "assistant.protocol":
                from .protocols import validate_state

                validate_state(data["protocol"], data["state"])
            else:
                text(data["text"], config.RUN_TEXT_BYTES, empty=True)
            if kind == "assistant.message":
                if (
                    type(data["complete"]) is not bool
                    or not isinstance(data["tool_calls"], list)
                    or len(data["tool_calls"]) > 4
                ):
                    raise ChatError("助手消息无效。")
                text(data["finish_reason"], 80, empty=True)
                for call in data["tool_calls"]:
                    object_fields(call, {"id", "name", "arguments"})
                    text(call["id"], 200)
                    text(call["name"], 80)
                    if (
                        not isinstance(call["arguments"], dict)
                        or len(canonical_json(call["arguments"]))
                        > config.TOOL_ARGUMENT_BYTES
                    ):
                        raise ChatError("工具参数无效。")
        elif kind in {"tool.call", "tool.result"}:
            text(data["call_id"], 200)
            text(data["name"], 80)
            key = "arguments" if kind == "tool.call" else "result"
            maximum = (
                config.TOOL_ARGUMENT_BYTES
                if key == "arguments"
                else config.TOOL_RESULT_BYTES
            )
            if (
                not isinstance(data[key], dict)
                or len(canonical_json(data[key])) > maximum
            ):
                raise ChatError("工具事件无效。")
            if kind == "tool.result" and type(data["ok"]) is not bool:
                raise ChatError("工具结果无效。")
        elif kind == "run.usage":
            integer(data["request_index"], 1, config.MAX_TOTAL_MODEL_REQUESTS)
            if data.get("purpose", "main") not in {
                "main",
                "summary",
                "native_compact",
                "memory",
            }:
                raise ChatError("模型调用用途无效。")
            for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
                integer(data[key])
        elif kind == "run.terminal":
            if data["status"] not in TERMINAL or any(
                type(data[k]) is not bool for k in ("uncertain", "usage_complete")
            ):
                raise ChatError("终态无效。")
            text(data["reason"], 256, empty=True)
            if data["error_code"] is not None:
                text(data["error_code"], 80)
            integer(data["model_requests"], 0, config.MAX_TOTAL_MODEL_REQUESTS)
            integer(data["tool_calls"], 0, config.MAX_TOOL_CALLS)
            if data["usage"] is not None:
                object_fields(
                    data["usage"],
                    {"prompt_tokens", "completion_tokens", "total_tokens"},
                )
                for value in data["usage"].values():
                    integer(value)
        elif kind == "run.notice":
            text(data["code"], 80)
            text(data["message"], 1024)
        elif kind == "candidate.selected":
            integer(data["candidate_index"], 0, config.MAX_CANDIDATES - 1)
        elif kind in {"context.compacted", "context.selected"}:
            if data["mode"] not in {"summary", "native"}:
                raise ChatError("压缩模式无效。")
            id_list(data["covered_run_ids"], config.CONTEXT_COVERAGE_RUNS)
            if (
                len(canonical_json(data["covered_run_ids"]))
                > config.CONTEXT_COVERAGE_BYTES
            ):
                raise ChatError(
                    "上下文来源元信息超过大小限制。", 413, "CHAT_CONTEXT_METADATA_LIMIT"
                )
            _hash(data["source_sha256"])
            reference = object_fields(
                data["checkpoint"], {"file", "sha256", "size_bytes"}
            )
            match = re.fullmatch(
                r"contexts/([a-f0-9-]{36})\.working\.json", str(reference["file"])
            )
            if not match:
                raise ChatError("工作上下文路径无效。")
            identifier(match.group(1))
            _hash(reference["sha256"])
            integer(reference["size_bytes"], 1, config.MODEL_REQUEST_BYTES)
        elif kind == "memory.proposed":
            intent = object_fields(
                data["intent"],
                {
                    "v",
                    "profile",
                    "before",
                    "after",
                    "before_sha256",
                    "after_sha256",
                    "source_message_ids",
                    "expected_context_revision",
                    "expected_memory_revision",
                },
            )
            if (
                type(intent["v"]) is not int
                or intent["v"] != 1
                or intent["profile"] != "MEMORY.md"
            ):
                raise ChatError("自动记忆意向无效。")
            for side in ("before", "after"):
                text(intent[side], config.MEMORY_BYTES, empty=True)
                if (
                    hashlib.sha256(intent[side].encode()).hexdigest()
                    != intent[side + "_sha256"]
                ):
                    raise ChatError("自动记忆意向哈希无效。")
            id_list(intent["source_message_ids"], 32)
            integer(intent["expected_context_revision"])
            integer(intent["expected_memory_revision"])
        elif kind == "memory.updated":
            from .auto_memory import validate_audit

            validate_audit(data["audit"], scope.principal)
            id_list(data["audit"]["source_ids"], 32)
        elif kind == "memory.reverted":
            _hash(data["after_sha256"])
            integer(data["context_revision"])
            integer(data["memory_revision"])
        if "candidate_index" in data:
            integer(data["candidate_index"], 0, config.MAX_CANDIDATES - 1)
    if len(canonical_json(event)) > config.EVENT_BYTES:
        raise ChatError("记录超过大小限制。", 413, "CHAT_REQUEST_TOO_LARGE")
    return event


@dataclass
class LogState:
    events: list = field(default_factory=list)
    offset: int = 0
    unpublished: bool = False
    storage_state: str = "ok"
    tail: bytes = b""
    commit_seq: int = 0

    @property
    def seq(self):
        return len(self.events)


def scan(raw, scope):
    state = LogState()
    for line in raw.splitlines(keepends=True):
        if not line.endswith(b"\n"):
            state.tail = raw[state.offset :]
            break
        try:
            if len(line) > config.EVENT_BYTES + 1 or line.startswith(b"\xef\xbb\xbf"):
                raise ValueError()
            event = strict_json(line.decode("utf-8"))
            validate_event(event, scope, state.seq + 1)
        except (ValueError, TypeError, KeyError, UnicodeError, RecursionError):
            state.storage_state = "corrupt"
            break
        state.events.append(event)
        state.offset += len(line)
    try:
        project(scope, state.events)
    except (ValueError, TypeError, KeyError):
        # Locate the first invalid transition in O(n log n), not O(n squared).
        low, high = 0, len(state.events)
        while low < high:
            mid = (low + high + 1) // 2
            try:
                project(scope, state.events[:mid])
                low = mid
            except (ValueError, TypeError, KeyError):
                high = mid - 1
        state.events = state.events[:low]
        state.offset = sum(len(line) for line in raw.splitlines(keepends=True)[:low])
        state.storage_state = "corrupt"
    return state


def project(scope, events, check=False):
    meta = {
        "id": scope.cid,
        "title": f"记录待修复 {scope.cid[:8]}",
        "created_at": "1970-01-01T00:00:00Z",
        "updated_at": "1970-01-01T00:00:00Z",
        "revision": 0,
        "last_seq": 0,
        "message_count": 0,
        "active_run_id": None,
        "last_run_status": None,
        "storage_state": "ok",
        "last_selection": None,
        "loaded_skill_ids": [],
    }
    files, runs, requests, messages, calls, writes = {}, {}, {}, set(), {}, {}
    user_messages = set()
    for event in events:
        kind, data, rid = event["type"], event["data"], event["run_id"]
        meta.update(
            updated_at=event["at"], revision=event["seq"], last_seq=event["seq"]
        )
        if kind == "conversation.created":
            meta.update(title=data["title"], created_at=event["at"])
        elif kind == "conversation.renamed":
            meta["title"] = data["title"]
        elif kind == "conversation.skills.updated":
            if meta["active_run_id"] is not None:
                raise ChatError("聊天运行期间不能修改已载入的 Skills。")
            meta["loaded_skill_ids"] = list(data["skill_ids"])
        elif kind == "run.accepted":
            if (
                rid in runs
                or data["client_request_id"] in requests
                or meta["active_run_id"] is not None
            ):
                raise ChatError("重复运行记录。")
            payload = {
                **data,
                "context_revision": data["context_snapshot"]["context_revision"],
                "skill_ids": data["context_snapshot"]["skill_ids"],
            }
            runs[rid] = {
                "id": rid,
                "client_request_id": data["client_request_id"],
                "status": "accepted",
                "model_requests": 0,
                "tool_calls": 0,
                "last_seq": event["seq"],
                "cancel_requested": False,
                "uncertain": False,
                "usage": None,
                "usage_complete": False,
                "error_code": None,
                "_digest": request_digest(payload),
                "_usage": {},
                "_purposes": {},
                "_candidate_messages": {},
                "_protocol": data.get("protocol", "chat_completions"),
                "selected_candidate": 0
                if data.get("options", {}).get("candidate_count", 1) == 1
                else None,
                "candidate_count": data.get("options", {}).get("candidate_count", 1),
                "main_model_requests": 0,
                "aux_model_requests": 0,
            }
            requests[data["client_request_id"]] = rid
            messages.add(data["message_id"])
            user_messages.add(data["message_id"])
            meta["active_run_id"] = rid
            meta["last_run_status"] = "accepted"
            meta["last_selection"] = {k: data[k] for k in ("token_id", "model", "mode")}
        elif kind == "candidate.selected":
            run = runs.get(rid)
            if (
                run is None
                or run["status"] != "completed"
                or run["candidate_count"] <= 1
                or meta["active_run_id"] is not None
                or next(reversed(runs)) != rid
                or data["candidate_index"] not in run["_candidate_messages"]
            ):
                raise ChatError("候选选择已不可更改。")
            run["selected_candidate"] = data["candidate_index"]
            run["last_seq"] = event["seq"]
        elif kind == "memory.reverted":
            run = runs.get(rid)
            if (
                run is None
                or run["status"] not in TERMINAL
                or not run.get("memory_updated")
                or run.get("memory_reverted")
                or meta["active_run_id"] is not None
            ):
                raise ChatError("自动记忆变更不能撤销。")
            run["memory_reverted"] = True
            run["last_seq"] = event["seq"]
        elif kind == "file.added":
            value = data["file"]
            if value["id"] in files:
                raise ChatError("重复文件。")
            if rid is not None:
                if rid not in runs or runs[rid]["status"] in TERMINAL:
                    raise ChatError("文件运行无效。")
                call = calls.get(rid)
                if call and call["name"] in {"write_file", "download_url"}:
                    writes[(rid, call["call_id"])] = value["id"]
            files[value["id"]] = value
        else:
            if rid not in runs or runs[rid]["status"] in TERMINAL:
                raise ChatError("运行事件无效。")
            run = runs[rid]
            run["last_seq"] = event["seq"]
            if kind == "run.dispatching":
                if data["request_index"] != run["model_requests"] + 1:
                    raise ChatError("派发序列无效。")
                run.update(
                    status="running",
                    model_requests=data["request_index"],
                    uncertain=True,
                )
                purpose = data.get("purpose", "main")
                run["_purposes"][data["request_index"]] = purpose
                category = (
                    "main_model_requests" if purpose == "main" else "aux_model_requests"
                )
                run[category] += 1
                if (
                    run["main_model_requests"] > config.MAX_MODEL_REQUESTS
                    or run["aux_model_requests"] > config.MAX_AUX_MODEL_REQUESTS
                ):
                    raise ChatError("模型调用次数超限。")
                meta["last_run_status"] = "running"
            elif kind.startswith("assistant."):
                messages.add(data["message_id"])
                if (
                    kind == "assistant.message"
                    and data["complete"]
                    and not data["tool_calls"]
                    and data["finish_reason"] == "stop"
                ):
                    candidate = data.get("candidate_index", 0)
                    if candidate >= run["candidate_count"]:
                        raise ChatError("候选消息超出数量。")
                    run["_candidate_messages"][candidate] = data["message_id"]
                if kind == "assistant.protocol":
                    if data["protocol"] != run["_protocol"]:
                        raise ChatError("协议状态与运行不匹配。")
            elif kind == "tool.call":
                key = (rid, data["call_id"])
                if key in calls:
                    raise ChatError("重复工具调用。")
                calls[key] = data
                calls[rid] = data
                run["tool_calls"] += 1
            elif kind == "tool.result":
                if (rid, data["call_id"]) not in calls:
                    raise ChatError("工具结果缺少调用。")
            elif kind == "run.usage":
                if data["request_index"] > run["model_requests"]:
                    raise ChatError("用量缺少派发。")
                run["_usage"][data["request_index"]] = {
                    k: data[k]
                    for k in ("prompt_tokens", "completion_tokens", "total_tokens")
                }
            elif kind == "memory.proposed":
                if (
                    run.get("_memory_intent") is not None
                    or not set(data["intent"]["source_message_ids"]) <= user_messages
                ):
                    raise ChatError("自动记忆来源无效。")
                run["_memory_intent"] = data["intent"]
            elif kind == "memory.updated":
                intent, audit = run.get("_memory_intent"), data["audit"]
                if (
                    not intent
                    or run.get("memory_updated")
                    or audit["before"]["sha256"] != intent["before_sha256"]
                    or audit["after"]["sha256"] != intent["after_sha256"]
                    or audit["source_ids"] != intent["source_message_ids"]
                ):
                    raise ChatError("自动记忆缺少匹配的意向记录。")
                run["memory_updated"] = audit["changed"]
            elif kind == "run.terminal":
                run.update(data)
                meta.update(active_run_id=None, last_run_status=data["status"])
    meta["message_count"] = len(messages)
    for run in runs.values():
        if run["status"] not in TERMINAL:
            usage = run["_usage"]
            run["usage"] = (
                {
                    k: sum(v[k] for v in usage.values())
                    for k in ("prompt_tokens", "completion_tokens", "total_tokens")
                }
                if usage
                else None
            )
            run["usage_complete"] = (
                bool(run["model_requests"]) and len(usage) == run["model_requests"]
            )
    return meta, files, runs, requests, writes
