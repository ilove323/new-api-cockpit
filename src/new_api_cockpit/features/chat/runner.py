"""Bounded, request-owned runs. No detached queue, retry, or database."""

from dataclasses import dataclass, field
import json
import queue
import re
import threading
import time
from uuid import uuid4

from . import config, context, tools, transport, web_tools, working_context
from .errors import ChatError
from .fs import canonical_json
from .recovery import TERMINAL
from .workspace import Workspace


@dataclass(repr=False)
class ActiveRun:
    scope: object
    lease: object
    run_id: str
    body: dict = field(repr=False)
    access: object = field(repr=False)
    messages: list = field(repr=False)
    policy: dict
    initial_events: list
    started: float = field(default_factory=time.monotonic)
    events: queue.Queue = field(
        default_factory=lambda: queue.Queue(config.QUEUE_EVENTS)
    )
    done: threading.Event = field(default_factory=threading.Event)
    stop_event: threading.Event = field(default_factory=threading.Event)
    abort: object = field(default_factory=transport.AbortControl)
    text_bytes: int = 0
    reasoning_bytes: int = 0
    context_plan: dict | None = field(default=None, repr=False)
    working_window: list | None = field(default=None, repr=False)
    history_events: list = field(default_factory=list, repr=False)
    compaction_calls: int = 0
    producer: object = None
    monitor: object = None
    last_published: int = 0
    abort_reason: str | None = None
    abort_lock: threading.Lock = field(default_factory=threading.Lock)

    @property
    def deadline(self):
        return self.started + config.RUN_DEADLINE_SECONDS


class Runner:
    def __init__(self, store, tokens, verifier, *, stream_chat=None, user_context=None):
        self.store, self.tokens, self.verifier = store, tokens, verifier
        self.stream_chat = stream_chat
        self.user_context = user_context
        self._lock = threading.Lock()
        self.active = {}
        self.stopping = False

    @staticmethod
    def _run_key(scope, rid):
        # Persisted UUIDs are namespaced, not a cross-owner authorization key.
        # Session/username changes must not change the stable history identity.
        return scope.instance_id, scope.user_id, scope.cid, rid

    def start(self, run):
        with self._lock:
            if self.stopping:
                raise ChatError("聊天请求池正在停止。", 503, "CHAT_STORAGE_UNAVAILABLE")
            self.active[self._run_key(run.scope, run.run_id)] = run
        for event in run.initial_events:
            run.events.put_nowait(event)
            run.last_published = event["seq"]
        run.monitor = threading.Thread(
            target=self._watch, args=(run,), name="chat-guard", daemon=True
        )
        run.producer = threading.Thread(
            target=self._execute, args=(run,), name="chat-run", daemon=True
        )
        try:
            run.monitor.start()
            run.producer.start()
        except BaseException:
            run.abort.abort()
            run.stop_event.set()
            self._finish(run, "failed", "CHAT_INTERNAL_ERROR", "start_failed")
            raise ChatError(
                "本轮启动失败，请查看历史状态。",
                503,
                "CHAT_STORAGE_UNAVAILABLE",
                run_id=run.run_id,
            ) from None
        return run

    @staticmethod
    def _abort(run, reason="CHAT_CANCELLED"):
        with run.abort_lock:
            if run.abort_reason is None:
                run.abort_reason = reason
            # The transport accepts only cancellation/deadline, while the run
            # preserves a more specific safe terminal reason (e.g. disk fault).
            run.abort.abort(
                "CHAT_DEADLINE"
                if run.abort_reason == "CHAT_DEADLINE"
                else "CHAT_CANCELLED"
            )

    def _check(self, run):
        if run.stop_event.is_set():
            self._abort(run)
        if time.monotonic() >= run.deadline:
            self._abort(run, "CHAT_DEADLINE")
        run.abort.check()

    def _watch(self, run):
        while not run.done.wait(0.25):
            try:
                if self.store.is_cancelled(run.scope, run.run_id):
                    run.stop_event.set()
                self._check(run)
            except ChatError as exc:
                self._abort(run, exc.code)
                return
            except Exception:
                self._abort(run, "CHAT_STORAGE_UNAVAILABLE")
                return

    def _publish(self, run, event, *, final=False):
        try:
            run.events.put(event, block=not final, timeout=None if final else 1)
            run.last_published = event["seq"]
        except queue.Full:
            self._abort(run, "CHAT_CLIENT_DISCONNECTED")
            if not final:
                raise ChatError(
                    "浏览器未继续接收，本轮已停止。", 409, "CHAT_CLIENT_DISCONNECTED"
                ) from None

    def _pump(self, run, *, final=False):
        target = self.store.get_conversation(run.scope, lease=run.lease)["last_seq"]
        while run.last_published < target:
            page = self.store.read_events(
                run.scope, after_seq=run.last_published, until_seq=target
            )
            if not page["events"]:
                break
            for event in page["events"]:
                self._publish(run, event, final=final)
                if final and run.last_published < event["seq"]:
                    return

    def _emit(self, run, kind, data):
        self.store.append(run.scope, kind, data, run_id=run.run_id, lease=run.lease)
        self._pump(run)

    @staticmethod
    def _calls(parts):
        result, seen = [], set()
        for index in sorted(parts):
            part = parts[index]
            cid, name, raw = part["id"], part["name"], part["arguments"]
            if (
                not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", cid)
                or cid in seen
                or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", name)
                or len(raw.encode()) > config.TOOL_ARGUMENT_BYTES
            ):
                raise ChatError("上游工具调用无效。", 502, "CHAT_UPSTREAM_ERROR")
            try:

                def pairs(items):
                    obj = {}
                    for key, value in items:
                        if key in obj:
                            raise ValueError()
                        obj[key] = value
                    return obj

                arguments = json.loads(raw, object_pairs_hook=pairs)
                if (
                    not isinstance(arguments, dict)
                    or len(canonical_json(arguments)) > config.TOOL_ARGUMENT_BYTES
                ):
                    raise ValueError()
            except (ValueError, TypeError, RecursionError, UnicodeError):
                raise ChatError(
                    "上游工具参数无效。", 502, "CHAT_UPSTREAM_ERROR"
                ) from None
            seen.add(cid)
            result.append({"id": cid, "name": name, "arguments": arguments})
        return result

    def _payload(
        self, run, messages, *, tools_allowed=False, candidates=1, window=None
    ):
        payload = {
            "model": run.body["model"],
            "messages": messages,
            "stream": True,
            run.policy["output_token_parameter"]: run.policy.get(
                "max_output_tokens", config.OUTPUT_TOKENS_PER_REQUEST
            ),
        }
        protocol = run.policy.get("protocol", "chat_completions")
        if protocol == "chat_completions" and run.policy["stream_usage"]:
            payload["stream_options"] = {"include_usage": True}
        for key in ("reasoning_effort", "reasoning", "thinking"):
            if key in run.policy:
                payload[key] = run.policy[key]
        if candidates > 1:
            payload["n"] = candidates
        if window is not None:
            payload["_window"] = window
        if tools_allowed:
            definitions = tools.schemas()
            if run.body.get("web_enabled", False):
                definitions += web_tools.schemas()
            if run.body.get("context_strategy") == "adaptive":
                definitions.append(working_context.history_schema())
            payload.update(tools=definitions, tool_choice="auto")
        return payload

    def _generate(
        self,
        run,
        messages,
        *,
        tools_allowed=False,
        candidates=1,
        candidate_start=0,
        purpose="main",
        publish=True,
    ):
        """One actual request, shared accounting for answer/summary/memory calls."""
        from .protocols import validate_state, wire_body

        self._check(run)
        state = self.store.get_run(run.scope, run.run_id, lease=run.lease)
        category = "main_model_requests" if purpose == "main" else "aux_model_requests"
        maximum = (
            config.MAX_MODEL_REQUESTS
            if purpose == "main"
            else config.MAX_AUX_MODEL_REQUESTS
        )
        if (
            state.get(category, 0) >= maximum
            or state["model_requests"] >= config.MAX_TOTAL_MODEL_REQUESTS
        ):
            raise ChatError("本轮已达模型调用次数上限。", 409, "CHAT_RUN_LIMIT")
        self.verifier.revalidate(run.access)
        self._check(run)
        protocol = run.policy.get("protocol", "chat_completions")
        results = [
            {
                "message_id": str(uuid4()),
                "text": [],
                "pending": [],
                "pending_size": 0,
                "parts": {},
                "finish": None,
                "native": None,
                "reasoning": {},
                "reasoning_pending": {},
            }
            for _ in range(candidates)
        ]
        index, usage = state["model_requests"] + 1, None
        last_flush = 0.0
        multi = run.body.get("candidate_count", 1) > 1

        def data_for(position, **extra):
            value = {"message_id": results[position]["message_id"], **extra}
            if multi:
                value["candidate_index"] = candidate_start + position
            return value

        def flush():
            nonlocal last_flush
            if publish:
                for position, result in enumerate(results):
                    if result["pending"]:
                        self._emit(
                            run,
                            "assistant.delta",
                            data_for(position, text="".join(result["pending"])),
                        )
                    for (kind, field), chunks in result["reasoning_pending"].items():
                        if chunks:
                            self._emit(
                                run,
                                "assistant.reasoning",
                                data_for(
                                    position,
                                    text="".join(chunks),
                                    kind=kind,
                                    field=field,
                                ),
                            )
            for result in results:
                result["pending"].clear()
                result["pending_size"] = 0
                result["reasoning_pending"].clear()
            last_flush = time.monotonic()

        with self.tokens.lease_key(run.access, run.body["token_id"]) as key:
            self._check(run)
            payload = self._payload(
                run,
                messages,
                tools_allowed=tools_allowed,
                candidates=candidates,
                window=run.working_window if purpose == "main" else None,
            )
            wire_body(protocol, payload)
            if (
                working_context.input_bytes(payload["messages"], payload.get("_window"))
                > run.policy["max_input_bytes"]
            ):
                raise ChatError(
                    "模型上下文超过大小限制。", 413, "CHAT_CONTEXT_TOO_LARGE"
                )
            dispatch = {
                "request_index": index,
                "assistant_message_id": results[0]["message_id"],
                "purpose": purpose,
            }
            if multi and purpose == "main":
                dispatch["candidate_index"] = candidate_start
                dispatch["candidate_message_ids"] = [r["message_id"] for r in results]
            self._emit(run, "run.dispatching", dispatch)
            self._check(run)
            source = (
                self.stream_chat(key, payload, run.deadline, run.abort)
                if self.stream_chat is not None
                else transport.stream_model(
                    key, payload, run.deadline, run.abort, protocol=protocol
                )
            )
            try:
                for event in source:
                    self._check(run)
                    kind = event["type"]
                    if kind == "usage":
                        if usage is not None:
                            raise ChatError(
                                "上游重复返回用量。", 502, "CHAT_UPSTREAM_ERROR"
                            )
                        clean = {
                            k: event[k]
                            for k in (
                                "prompt_tokens",
                                "completion_tokens",
                                "total_tokens",
                            )
                        }
                        if all(
                            type(v) is int and 0 <= v <= 9007199254740991
                            for v in clean.values()
                        ):
                            usage = clean
                        continue
                    position = event.get("candidate_index", 0)
                    if type(position) is not int or not 0 <= position < candidates:
                        raise ChatError(
                            "上游候选标识无效。", 502, "CHAT_UPSTREAM_ERROR"
                        )
                    result = results[position]
                    if kind in {"text", "reasoning"}:
                        if result["finish"] is not None:
                            raise ChatError(
                                "上游结束后仍返回正文。", 502, "CHAT_UPSTREAM_ERROR"
                            )
                        chunk = event["text"]
                        size = len(chunk.encode("utf-8"))
                        if kind == "text":
                            if publish:
                                run.text_bytes += size
                                total = run.text_bytes
                            else:
                                total = (
                                    sum(len(s.encode()) for s in result["text"]) + size
                                )
                            if total > config.RUN_TEXT_BYTES:
                                raise ChatError(
                                    "本轮输出已达上限。", 413, "CHAT_REQUEST_TOO_LARGE"
                                )
                            result["text"].append(chunk)
                            result["pending"].append(chunk)
                        else:
                            run.reasoning_bytes += size
                            if run.reasoning_bytes > config.REASONING_BYTES:
                                raise ChatError(
                                    "本轮公开推理已达上限。",
                                    413,
                                    "CHAT_REQUEST_TOO_LARGE",
                                )
                            group = event["kind"], event["field"]
                            result["reasoning"].setdefault(group, []).append(chunk)
                            result["reasoning_pending"].setdefault(group, []).append(
                                chunk
                            )
                        result["pending_size"] += size
                        if (
                            result["pending_size"] >= config.DELTA_FLUSH_BYTES
                            or time.monotonic() - last_flush
                            >= config.DELTA_FLUSH_MS / 1000
                        ):
                            flush()
                    elif kind == "tool_delta":
                        call_position = event["index"]
                        if (
                            type(call_position) is not int
                            or not 0 <= call_position < 4
                            or not tools_allowed
                        ):
                            raise ChatError(
                                "上游工具调用无效。", 502, "CHAT_UPSTREAM_ERROR"
                            )
                        part = result["parts"].setdefault(
                            call_position, {"id": "", "name": "", "arguments": ""}
                        )
                        for name in part:
                            part[name] += event.get(name, "")
                            if len(part[name].encode()) > (
                                config.TOOL_ARGUMENT_BYTES
                                if name == "arguments"
                                else 128
                            ):
                                raise ChatError(
                                    "上游工具参数过大。", 502, "CHAT_UPSTREAM_ERROR"
                                )
                    elif kind == "replay":
                        if (
                            result["native"] is not None
                            or event["protocol"] != protocol
                        ):
                            raise ChatError(
                                "上游协议状态无效。", 502, "CHAT_UPSTREAM_ERROR"
                            )
                        result["native"] = {
                            "protocol": protocol,
                            "value": validate_state(protocol, event["value"]),
                        }
                    elif kind == "finish":
                        if result["finish"] is not None:
                            raise ChatError(
                                "上游完成状态无效。", 502, "CHAT_UPSTREAM_ERROR"
                            )
                        result["finish"] = event["reason"]
                    else:
                        raise ChatError("上游事件无效。", 502, "CHAT_UPSTREAM_ERROR")
                flush()
            finally:
                close = getattr(source, "close", None)
                if close:
                    close()
                if usage is not None:
                    self._emit(
                        run,
                        "run.usage",
                        {"request_index": index, "purpose": purpose, **usage},
                    )
        for position, result in enumerate(results):
            calls = self._calls(result["parts"])
            if result["finish"] not in {"stop", "tool_calls"} or (
                result["finish"] == "tool_calls"
            ) != bool(calls):
                raise ChatError(
                    "模型回答未完整结束。", 502, "CHAT_UPSTREAM_INTERRUPTED"
                )
            result["text"] = "".join(result["text"])
            result["calls"] = calls
            if publish:
                if result["native"] is not None:
                    self._emit(
                        run,
                        "assistant.protocol",
                        data_for(
                            position, protocol=protocol, state=result["native"]["value"]
                        ),
                    )
                self._emit(
                    run,
                    "assistant.message",
                    data_for(
                        position,
                        text=result["text"],
                        tool_calls=calls,
                        finish_reason=result["finish"],
                        complete=True,
                    ),
                )
        return results

    @staticmethod
    def _assistant(result):
        message = {"role": "assistant", "content": result["text"]}
        if result["calls"]:
            message["tool_calls"] = [
                {
                    "id": c["id"],
                    "type": "function",
                    "function": {
                        "name": c["name"],
                        "arguments": context.compact(c["arguments"]),
                    },
                }
                for c in result["calls"]
            ]
        if result["native"] is not None:
            message["_native"] = result["native"]
        return message

    def _native_compact(self, run, planned):
        from .protocols import validate_window

        self._check(run)
        state = self.store.get_run(run.scope, run.run_id, lease=run.lease)
        if (
            state["aux_model_requests"] >= config.MAX_AUX_MODEL_REQUESTS
            or state["model_requests"] >= config.MAX_TOTAL_MODEL_REQUESTS
        ):
            raise ChatError("本轮辅助调用次数已达上限。", 409, "CHAT_RUN_LIMIT")
        validate_window(planned["compact_input"])
        if (
            len(canonical_json(planned["compact_input"]))
            > run.policy["max_input_bytes"]
        ):
            raise ChatError(
                "待压缩输入超过模型上限。", 413, "CHAT_CONTEXT_COMPACTION_LIMIT"
            )
        self.verifier.revalidate(run.access)
        self._check(run)
        with self.tokens.lease_key(run.access, run.body["token_id"]) as key:
            self._check(run)
            index = state["model_requests"] + 1
            self._emit(
                run,
                "run.dispatching",
                {
                    "request_index": index,
                    "assistant_message_id": str(uuid4()),
                    "purpose": "native_compact",
                },
            )
            self._check(run)
            result = transport.compact_model(
                key,
                {"model": run.body["model"], "input": planned["compact_input"]},
                run.deadline,
                run.abort,
            )
            if result["usage"] is not None:
                self._emit(
                    run,
                    "run.usage",
                    {
                        "request_index": index,
                        "purpose": "native_compact",
                        **{
                            k: result["usage"][k]
                            for k in (
                                "prompt_tokens",
                                "completion_tokens",
                                "total_tokens",
                            )
                        },
                    },
                )
            return result

    def _apply_context_plan(self, run, planned):
        if planned.get("compact_required"):
            if run.compaction_calls >= working_context.MAX_COMPACTION_CALLS:
                if (
                    working_context.input_bytes(run.messages, run.working_window)
                    <= planned["budget"]
                ):
                    self._emit(
                        run,
                        "run.notice",
                        {
                            "code": "CHAT_CONTEXT_PROACTIVE_LIMIT",
                            "message": "主动压缩次数已达上限，当前工作输入仍在硬预算内，沿用完整现有窗口继续；未新增辅助收费请求。",
                        },
                    )
                    return
                raise ChatError(
                    "本轮自动压缩次数已达上限，未继续收费。", 409, "CHAT_RUN_LIMIT"
                )
            run.compaction_calls += 1
            result = (
                self._native_compact(run, planned)
                if planned["compact_mode"] == "native"
                else self._generate(
                    run, planned["compact_messages"], purpose="summary", publish=False
                )[0]
            )
            planned = working_context.finalize(planned, result)
            checkpoint = planned["checkpoint"]
            if checkpoint is None:
                checkpoint = {
                    "version": 1,
                    "active_only": True,
                    "mode": planned["compact_mode"],
                    "binding": planned["binding"],
                    "source_sha256": planned["source_sha256"],
                    "messages": planned["messages"],
                    "window": planned["window"],
                }
            reference = self.store.write_working_context(
                run.scope, run.run_id, checkpoint, lease=run.lease
            )
            self._emit(
                run,
                "context.compacted",
                {
                    "mode": planned["compact_mode"],
                    "covered_run_ids": planned["compact_covered_run_ids"],
                    "source_sha256": planned["source_sha256"],
                    "checkpoint": reference,
                },
            )
        elif planned.get("checkpoint") and planned.get("checkpoint_reference"):
            checkpoint = planned["checkpoint"]
            self._emit(
                run,
                "context.selected",
                {
                    "mode": checkpoint["mode"],
                    "covered_run_ids": checkpoint["covered_run_ids"],
                    "source_sha256": checkpoint["source_sha256"],
                    "checkpoint": planned["checkpoint_reference"],
                },
            )
        run.messages, run.working_window = planned["messages"], planned["window"]
        run.context_plan = planned

    def _search_history(self, run, args):
        from . import schemas

        try:
            schemas.object_fields(args, {"query", "offset", "limit", "case_sensitive"})
            history, after, until = [], 0, None
            while True:
                self._check(run)
                page = self.store.read_events(
                    run.scope, after_seq=after, until_seq=until
                )
                history.extend(page["events"])
                if not page["has_more"]:
                    break
                after, until = page["next_after_seq"], page["until_seq"]
            return working_context.search_history(history, **args)
        except ChatError as exc:
            if exc.code in {
                "CHAT_CANCELLED",
                "CHAT_DEADLINE",
                "CHAT_CLIENT_DISCONNECTED",
            } or exc.code.startswith("CHAT_STORAGE_"):
                raise
            return {
                "ok": False,
                "code": "TOOL_INVALID_ARGUMENTS",
                "message": "当前聊天原文检索参数无效或超过有界扫描限制。",
            }

    def _auto_memory(self, run):
        """One opt-in auxiliary call, user-authored evidence only, intent then CAS."""
        from . import auto_memory

        try:
            self._check(run)
            snapshot = self.store.read_context(run.scope, run.run_id)
            current = self.user_context.read_context(run.access.principal)
            if current["context_revision"] != snapshot["context_revision"]:
                raise ChatError(
                    "个人设置已变化，本轮不覆盖。", 409, "CHAT_CONTEXT_CHANGED"
                )
            profile = next(p for p in current["profiles"] if p["name"] == "MEMORY.md")
            accepted = next(
                e["data"] for e in run.initial_events if e["type"] == "run.accepted"
            )
            sources = [{"message_id": accepted["message_id"], "text": accepted["text"]}]
            prompt = auto_memory.build_prompt(sources, profile["content"])
            result = self._generate(run, prompt, purpose="memory", publish=False)[0]
            proposal = auto_memory.parse_proposal(
                result["text"], sources, profile["content"]
            )
            prepared = auto_memory.prepare_update(profile["content"], proposal)
            if prepared["after"] == prepared["before"]:
                self._emit(
                    run,
                    "run.notice",
                    {
                        "code": "CHAT_MEMORY_UNCHANGED",
                        "message": "自动记忆检查完成，未修改永久 MEMORY。",
                    },
                )
                return
            self._emit(
                run,
                "memory.proposed",
                {
                    "intent": {
                        "v": 1,
                        "profile": "MEMORY.md",
                        **prepared,
                        "expected_context_revision": current["context_revision"],
                        "expected_memory_revision": profile["revision"],
                    }
                },
            )
            self._check(run)
            audit = auto_memory.apply_proposal(
                self.user_context,
                run.access.principal,
                current["context_revision"],
                profile["revision"],
                proposal,
                credential=run.access.credential,
            )
            self._emit(run, "memory.updated", {"audit": audit})
        except ChatError as exc:
            if exc.code in {
                "CHAT_CANCELLED",
                "CHAT_CLIENT_DISCONNECTED",
                "CHAT_DEADLINE",
            } or exc.code.startswith("CHAT_STORAGE_"):
                raise
            self._emit(
                run,
                "run.notice",
                {
                    "code": exc.code,
                    "message": "回答已保存；本轮自动记忆未更新，可能是提案不合格、设置已变化或辅助调用未完成。未重试，辅助用量以实际返回为准。",
                },
            )

    def _execute(self, run):
        status, code, reason = "failed", "CHAT_INTERNAL_ERROR", "execution_failed"
        seen_calls = set()
        try:
            workspace = Workspace(
                self.store, run.scope, lease=run.lease, run_id=run.run_id
            )
            if run.context_plan is not None:
                self._apply_context_plan(run, run.context_plan)
            if run.body["mode"] == "chat":
                count = run.body.get("candidate_count", 1)
                if run.policy.get("protocol", "chat_completions") == "chat_completions":
                    self._generate(run, run.messages, candidates=count)
                else:
                    for candidate in range(count):
                        self._generate(run, run.messages, candidate_start=candidate)
                status, code, reason = "completed", None, "completed"
            else:
                for number in range(1, config.MAX_MODEL_REQUESTS + 1):
                    if number > 1 and run.body.get("context_strategy") == "adaptive":
                        binding = {
                            "protocol": run.policy["protocol"],
                            **{
                                key: run.body[key]
                                for key in ("model", "token_id", "capability_revision")
                            },
                        }
                        planned = working_context.plan_active(
                            run.messages, run.policy, binding, window=run.working_window
                        )
                        self._apply_context_plan(run, planned)
                    result = self._generate(run, run.messages, tools_allowed=True)[0]
                    calls = result["calls"]
                    if not calls:
                        status, code, reason = "completed", None, "completed"
                        break
                    count = self.store.get_run(run.scope, run.run_id, lease=run.lease)[
                        "tool_calls"
                    ]
                    if (
                        number == config.MAX_MODEL_REQUESTS
                        or count + len(calls) > config.MAX_TOOL_CALLS
                    ):
                        raise ChatError(
                            "本轮已达工具或请求次数上限。", 409, "CHAT_RUN_LIMIT"
                        )
                    run.messages.append(self._assistant(result))
                    for call in calls:
                        self._check(run)
                        if call["id"] in seen_calls:
                            raise ChatError(
                                "上游重复使用工具调用ID。", 502, "CHAT_UPSTREAM_ERROR"
                            )
                        seen_calls.add(call["id"])
                        self._emit(
                            run,
                            "tool.call",
                            {
                                "call_id": call["id"],
                                "name": call["name"],
                                "arguments": call["arguments"],
                            },
                        )
                        executor = (
                            web_tools.execute
                            if call["name"]
                            in {"web_search", "http_fetch", "download_url"}
                            else tools.execute
                        )
                        output = (
                            self._search_history(run, call["arguments"])
                            if call["name"] == "search_chat_history"
                            and run.body.get("context_strategy") == "adaptive"
                            else executor(
                                call["name"],
                                call["arguments"],
                                workspace,
                                {
                                    "run_id": run.run_id,
                                    "call_id": call["id"],
                                    "stop_event": run.stop_event,
                                    "abort": run.abort,
                                    "deadline": run.deadline,
                                    "web_enabled": run.body.get("web_enabled", False),
                                },
                            )
                        )
                        self._emit(
                            run,
                            "tool.result",
                            {
                                "call_id": call["id"],
                                "name": call["name"],
                                "ok": output["ok"],
                                "result": {
                                    k: v for k, v in output.items() if k != "ok"
                                },
                            },
                        )
                        run.messages.append(
                            {
                                "role": "tool",
                                "tool_call_id": call["id"],
                                "content": context.compact(output),
                            }
                        )
                        if output.get("code") == "TOOL_STORAGE_LIMIT":
                            raise ChatError(
                                "文件存储额度已满。", 507, "CHAT_STORAGE_LIMIT"
                            )
                    if (
                        run.body.get("context_strategy") != "adaptive"
                        and working_context.input_bytes(
                            run.messages, run.working_window
                        )
                        > run.policy["max_input_bytes"]
                    ):
                        raise ChatError(
                            "工具结果超过模型上下文上限。",
                            413,
                            "CHAT_CONTEXT_TOO_LARGE",
                        )
                    self._check(run)
            if status == "completed" and run.body.get("auto_memory", False):
                self._auto_memory(run)
        except ChatError as exc:
            code = run.abort_reason or exc.code
            reason = (
                "cancelled"
                if code in {"CHAT_CANCELLED", "CHAT_CLIENT_DISCONNECTED"}
                else "execution_failed"
            )
            status = "cancelled" if reason == "cancelled" else "failed"
        except Exception:
            # No exception locals/text: they can contain private content or keys.
            status, code, reason = "failed", "CHAT_INTERNAL_ERROR", "execution_failed"
        finally:
            self._finish(run, status, code, reason)

    def _finish(self, run, status, code, reason):
        try:
            if not run.lease.poisoned and not run.lease.closed:
                state = self.store.get_run(run.scope, run.run_id, lease=run.lease)
                if state["status"] not in TERMINAL:
                    self.store.append(
                        run.scope,
                        "run.terminal",
                        {
                            "status": status,
                            "reason": reason,
                            "error_code": code,
                            "uncertain": status != "completed"
                            and state["model_requests"] > 0,
                            "model_requests": state["model_requests"],
                            "tool_calls": state["tool_calls"],
                            "usage": state["usage"],
                            "usage_complete": state["usage_complete"],
                        },
                        run_id=run.run_id,
                        lease=run.lease,
                    )
                    self._pump(run, final=True)
        except Exception:
            # Recovery will publish interrupted once it can own this lease.
            pass
        finally:
            try:
                try:
                    run.abort.abort()
                finally:
                    run.lease.close()
            except Exception:
                # A release fault may leave stale quota records. Fail closed
                # until worker restart; it must never strand the consumer or
                # retain credentials even when cleanup itself is failing.
                with self._lock:
                    self.stopping = True
            finally:
                run.done.set()
                run.access = None
                run.messages = []
                run.context_plan = None
                run.working_window = None
                run.history_events = []
                with self._lock:
                    self.active.pop(self._run_key(run.scope, run.run_id), None)
                if (
                    run.monitor is not None
                    and run.monitor is not threading.current_thread()
                    and run.monitor.is_alive()
                ):
                    run.monitor.join(timeout=1)

    def stream(self, run):
        last_heartbeat = time.monotonic()
        try:
            while not run.done.is_set() or not run.events.empty():
                try:
                    event = run.events.get(timeout=0.25)
                    data = canonical_json(event).decode("utf-8")
                    yield f"id: {event['seq']}\nevent: {event['type']}\ndata: {data}\n\n"
                except queue.Empty:
                    if time.monotonic() - last_heartbeat >= config.HEARTBEAT_SECONDS:
                        yield ": heartbeat\n\n"
                        last_heartbeat = time.monotonic()
        finally:
            if not run.done.is_set():
                run.stop_event.set()
                self._abort(run, "CHAT_CLIENT_DISCONNECTED")

    def cancel_local(self, scope, rid):
        with self._lock:
            run = self.active.get(self._run_key(scope, rid))
            if run:
                run.stop_event.set()
                run.abort.abort()

    def request_stop(self):
        with self._lock:
            self.stopping = True
            runs = list(self.active.values())
        for run in runs:
            run.stop_event.set()
            run.abort.abort()
        return runs

    def shutdown(self, timeout=20):
        deadline = time.monotonic() + timeout
        for run in self.request_stop():
            if (
                run.producer is not None
                and run.producer is not threading.current_thread()
            ):
                run.producer.join(max(0, deadline - time.monotonic()))
