"""Three wire protocols around one portable, owner-scoped Agent transcript.

Only documented fields are replayable. Public reasoning text is not confused
with opaque provider state; no state is decrypted, guessed, or logged here.
"""

import json
import re

from . import config
from .errors import ChatError
from .transport import _SSE, _bad_stream, _strict_json

PROTOCOLS = frozenset({"chat_completions", "responses", "anthropic"})
ID = re.compile(r"[A-Za-z0-9_.:/-]{1,256}")


def _string(value, maximum, *, empty=True):
    if (
        not isinstance(value, str)
        or len(value.encode("utf-8")) > maximum
        or (not empty and not value)
        or any(ord(c) < 32 and c not in "\r\n\t" for c in value)
    ):
        raise _bad_stream()
    return value


def _id(value):
    if not isinstance(value, str) or not ID.fullmatch(value):
        raise _bad_stream()
    return value


def _bounded(value, maximum=config.PROTOCOL_STATE_BYTES):
    if len(json.dumps(value, ensure_ascii=False, allow_nan=False).encode()) > maximum:
        raise _bad_stream()
    return value


def _arguments(value):
    raw = _string(value, config.TOOL_ARGUMENT_BYTES)
    if not isinstance(_strict_json(raw), dict):
        raise _bad_stream()
    return raw


def validate_window(value):
    """A native compact result is an entire input window, not one hidden item.

    This text-only client accepts documented message/function/reasoning/compact
    items. Unknown server tools and media fail closed, never disappear silently.
    Return the original JSON structure after validation, preserving its order.
    """
    if not isinstance(value, list) or not 1 <= len(value) <= 8192:
        raise _bad_stream()
    for item in value:
        if not isinstance(item, dict):
            raise _bad_stream()
        kind = item.get("type")
        if kind == "compaction":
            if set(item) - {"type", "id", "encrypted_content"}:
                raise _bad_stream()
            if "id" in item:
                _id(item["id"])
            _string(
                item.get("encrypted_content"),
                config.EXTENDED_CONTEXT_BYTES,
                empty=False,
            )
        elif kind == "function_call_output":
            if set(item) - {"type", "id", "call_id", "output", "status"}:
                raise _bad_stream()
            if "id" in item:
                _id(item["id"])
            _id(item.get("call_id"))
            _string(item.get("output"), config.TOOL_RESULT_BYTES)
            if item.get("status", "completed") not in {
                "completed",
                "incomplete",
                "in_progress",
            }:
                raise _bad_stream()
        elif kind == "message" or kind is None and "role" in item:
            if set(item) - {"type", "id", "role", "content", "status", "phase"}:
                raise _bad_stream()
            if "id" in item:
                _id(item["id"])
            if item.get("role") not in {"user", "assistant", "system", "developer"}:
                raise _bad_stream()
            if item.get("status", "completed") not in {
                "completed",
                "incomplete",
                "in_progress",
            }:
                raise _bad_stream()
            if item.get("phase", "final_answer") not in {"commentary", "final_answer"}:
                raise _bad_stream()
            blocks = item.get("content")
            if isinstance(blocks, str):
                _string(blocks, config.EXTENDED_CONTEXT_BYTES)
            elif isinstance(blocks, list) and len(blocks) <= 64:
                for block in blocks:
                    if not isinstance(block, dict):
                        raise _bad_stream()
                    btype = block.get("type")
                    field = "refusal" if btype == "refusal" else "text"
                    allowed = {"type", field} | (
                        {"annotations", "logprobs"} if btype == "output_text" else set()
                    )
                    if (
                        btype not in {"input_text", "output_text", "refusal"}
                        or set(block) - allowed
                    ):
                        raise _bad_stream()
                    _string(block.get(field), config.EXTENDED_CONTEXT_BYTES)
                    for extra in ("annotations", "logprobs"):
                        if extra in block and not isinstance(block[extra], list):
                            raise _bad_stream()
            else:
                raise _bad_stream()
        else:
            validate_state("responses", [item])
    return _bounded(value, config.EXTENDED_CONTEXT_BYTES)


def _usage(input_tokens, output_tokens, total=None):
    values = (input_tokens, output_tokens)
    if any(type(v) is not int or not 0 <= v <= 9007199254740991 for v in values):
        return None
    total = sum(values) if total is None else total
    if type(total) is not int or not 0 <= total <= 9007199254740991:
        return None
    return {
        "type": "usage",
        "prompt_tokens": input_tokens,
        "completion_tokens": output_tokens,
        "total_tokens": total,
    }


def validate_state(protocol, value):
    """Validate persisted replay DTOs, never arbitrary upstream objects."""
    if protocol == "chat_completions":
        if not isinstance(value, dict) or set(value) - {
            "reasoning_content",
            "reasoning",
        }:
            raise _bad_stream()
        for text in value.values():
            _string(text, config.REASONING_BYTES)
    elif protocol == "responses":
        if not isinstance(value, list) or len(value) > 32:
            raise _bad_stream()
        ids = set()
        for item in value:
            if not isinstance(item, dict):
                raise _bad_stream()
            kind = item.get("type")
            allowed = {
                "reasoning": {
                    "type",
                    "id",
                    "summary",
                    "content",
                    "encrypted_content",
                    "status",
                },
                "message": {"type", "id", "role", "content", "status", "phase"},
                "function_call": {
                    "type",
                    "id",
                    "call_id",
                    "name",
                    "arguments",
                    "status",
                },
            }.get(kind)
            if allowed is None or set(item) - allowed:
                raise _bad_stream()
            if "id" in item:
                iid = _id(item["id"])
                if iid in ids:
                    raise _bad_stream()
                ids.add(iid)
            if "status" in item and item["status"] not in {
                "completed",
                "incomplete",
                "in_progress",
            }:
                raise _bad_stream()
            if kind == "reasoning":
                if "encrypted_content" in item:
                    _string(item["encrypted_content"], config.PROTOCOL_STATE_BYTES)
                for key, expected in (
                    ("summary", "summary_text"),
                    ("content", "reasoning_text"),
                ):
                    blocks = item.get(key, [])
                    if not isinstance(blocks, list) or len(blocks) > 64:
                        raise _bad_stream()
                    for block in blocks:
                        if (
                            not isinstance(block, dict)
                            or set(block) != {"type", "text"}
                            or block["type"] != expected
                        ):
                            raise _bad_stream()
                        _string(block["text"], config.REASONING_BYTES)
            elif kind == "message":
                if item.get("role") != "assistant" or item.get(
                    "phase", "final_answer"
                ) not in {"commentary", "final_answer"}:
                    raise _bad_stream()
                blocks = item.get("content")
                if not isinstance(blocks, list) or len(blocks) > 64:
                    raise _bad_stream()
                for block in blocks:
                    if not isinstance(block, dict):
                        raise _bad_stream()
                    field = (
                        "text"
                        if block.get("type") == "output_text"
                        else "refusal"
                        if block.get("type") == "refusal"
                        else None
                    )
                    if field is None or set(block) != {"type", field}:
                        raise _bad_stream()
                    _string(block[field], config.RUN_TEXT_BYTES)
            else:
                _id(item.get("call_id"))
                _id(item.get("name"))
                _arguments(item.get("arguments"))
    elif protocol == "anthropic":
        if not isinstance(value, list) or len(value) > 32:
            raise _bad_stream()
        calls = set()
        for block in value:
            if not isinstance(block, dict):
                raise _bad_stream()
            kind = block.get("type")
            if kind == "text" and set(block) == {"type", "text"}:
                _string(block["text"], config.RUN_TEXT_BYTES)
            elif kind == "thinking" and set(block) == {"type", "thinking", "signature"}:
                _string(block["thinking"], config.REASONING_BYTES)
                _string(block["signature"], config.PROTOCOL_STATE_BYTES, empty=False)
            elif kind == "redacted_thinking" and set(block) == {"type", "data"}:
                _string(block["data"], config.PROTOCOL_STATE_BYTES, empty=False)
            elif kind == "tool_use" and set(block) == {"type", "id", "name", "input"}:
                cid = _id(block["id"])
                _id(block["name"])
                if cid in calls or not isinstance(block["input"], dict):
                    raise _bad_stream()
                calls.add(cid)
                _bounded(block["input"], config.TOOL_ARGUMENT_BYTES)
            else:
                raise _bad_stream()
    else:
        raise _bad_stream()
    return _bounded(value)


def _clean_response_items(items):
    """Keep replayable fields; display-only citations are not protocol state."""
    if not isinstance(items, list):
        raise _bad_stream()
    cleaned = []
    for original in items:
        if not isinstance(original, dict):
            raise _bad_stream()
        kind = original.get("type")
        keys = {
            "reasoning": (
                "type",
                "id",
                "summary",
                "content",
                "encrypted_content",
                "status",
            ),
            "message": ("type", "id", "role", "content", "status", "phase"),
            "function_call": ("type", "id", "call_id", "name", "arguments", "status"),
        }.get(kind)
        if keys is None:
            raise _bad_stream()
        item = {
            k: original[k] for k in keys if k in original and original[k] is not None
        }
        if kind == "message":
            blocks = original.get("content")
            if not isinstance(blocks, list):
                raise _bad_stream()
            item["content"] = []
            for block in blocks:
                if not isinstance(block, dict):
                    raise _bad_stream()
                field = (
                    "text"
                    if block.get("type") == "output_text"
                    else "refusal"
                    if block.get("type") == "refusal"
                    else None
                )
                if field is None:
                    raise _bad_stream()
                item["content"].append({"type": block["type"], field: block.get(field)})
        cleaned.append(item)
    return validate_state("responses", cleaned)


class ChatSSE(_SSE):
    """Each candidate has the original strict Chat parser and its own finish."""

    def __init__(self, tools=False, candidates=1):
        super().__init__(tools)
        if (
            type(candidates) is not int
            or not 1 <= candidates <= config.MAX_CANDIDATES
            or tools
            and candidates != 1
        ):
            raise _bad_stream()
        self.candidates = candidates
        self.children = [_SSE(tools) for _ in range(candidates)]
        self.reasoning = [{} for _ in range(candidates)]
        self.state_sent = set()

    def _decorate(self, event, index):
        return dict(event, candidate_index=index) if self.candidates > 1 else event

    def _frame(self, data):
        if data == "[DONE]":
            for child in self.children:
                yield from child._frame(data)
            self.done = True
            return
        value = _strict_json(data)
        if (
            not isinstance(value, dict)
            or "error" in value
            or not isinstance(value.get("choices"), list)
        ):
            raise _bad_stream()
        seen = set()
        for choice in value["choices"]:
            if not isinstance(choice, dict):
                raise _bad_stream()
            index = choice.get("index")
            if (
                type(index) is not int
                or not 0 <= index < self.candidates
                or index in seen
            ):
                raise _bad_stream()
            seen.add(index)
            child = self.children[index]
            delta = choice.get("delta")
            if not isinstance(delta, dict):
                raise _bad_stream()
            fields = [
                k
                for k in ("reasoning_content", "reasoning")
                if delta.get(k) is not None
            ]
            if len(fields) > 1 or child.finish is not None:
                raise _bad_stream()
            for key in fields:
                if self.reasoning[index] and key not in self.reasoning[index]:
                    raise _bad_stream()
                text = _string(delta[key], config.REASONING_BYTES)
                previous = self.reasoning[index].get(key, "")
                self.reasoning[index][key] = _string(
                    previous + text, config.REASONING_BYTES
                )
                if text:
                    yield self._decorate(
                        {
                            "type": "reasoning",
                            "text": text,
                            "kind": "raw",
                            "field": key,
                        },
                        index,
                    )
            single = {"choices": [{**choice, "index": 0}]}
            for event in child._frame(json.dumps(single, ensure_ascii=False)):
                if (
                    event["type"] == "finish"
                    and self.reasoning[index]
                    and index not in self.state_sent
                ):
                    self.state_sent.add(index)
                    yield self._decorate(
                        {
                            "type": "replay",
                            "protocol": "chat_completions",
                            "value": validate_state(
                                "chat_completions", self.reasoning[index]
                            ),
                        },
                        index,
                    )
                yield self._decorate(event, index)
        if value.get("usage") is not None:
            for event in self.children[0]._frame(
                json.dumps({"choices": [], "usage": value["usage"]})
            ):
                yield event
        elif not value["choices"]:
            raise _bad_stream()


class ResponsesSSE(_SSE):
    max_frame_bytes = config.PROTOCOL_FRAME_BYTES

    def __init__(self, tools=False):
        super().__init__(tools)
        self.emitted_text = ""
        self.emitted_reasoning = {"raw": "", "summary": ""}
        self.ended = False

    def _frame(self, data):
        if data == "[DONE]":
            raise _bad_stream()
        event = _strict_json(data)
        if not isinstance(event, dict) or not isinstance(event.get("type"), str):
            raise _bad_stream()
        kind = event["type"]
        if kind == "error" or kind in {"response.failed", "response.cancelled"}:
            raise _bad_stream()
        if kind == "response.output_text.delta" or kind == "response.refusal.delta":
            text = _string(event.get("delta"), config.RUN_TEXT_BYTES)
            self.emitted_text = _string(self.emitted_text + text, config.RUN_TEXT_BYTES)
            if text:
                yield {"type": "text", "text": text}
        elif kind in {
            "response.reasoning_summary_text.delta",
            "response.reasoning_text.delta",
        }:
            mode = "summary" if "summary" in kind else "raw"
            text = _string(event.get("delta"), config.REASONING_BYTES)
            self.emitted_reasoning[mode] = _string(
                self.emitted_reasoning[mode] + text, config.REASONING_BYTES
            )
            if text:
                yield {
                    "type": "reasoning",
                    "kind": mode,
                    "field": "reasoning",
                    "text": text,
                }
        elif kind in {"response.completed", "response.incomplete"}:
            response = event.get("response")
            if not isinstance(response, dict) or response.get("status") != (
                "completed" if kind == "response.completed" else "incomplete"
            ):
                raise _bad_stream()
            items = _clean_response_items(response.get("output"))
            full_text, reasoning, calls = "", {"raw": "", "summary": ""}, []
            for item in items:
                if item["type"] == "message":
                    full_text += "".join(
                        block.get("text", block.get("refusal", ""))
                        for block in item["content"]
                    )
                elif item["type"] == "reasoning":
                    reasoning["summary"] += "".join(
                        b["text"] for b in item.get("summary", [])
                    )
                    reasoning["raw"] += "".join(
                        b["text"] for b in item.get("content", [])
                    )
                else:
                    calls.append(item)
            if not full_text.startswith(self.emitted_text):
                raise _bad_stream()
            if full_text[len(self.emitted_text) :]:
                yield {"type": "text", "text": full_text[len(self.emitted_text) :]}
            for mode in ("raw", "summary"):
                if not reasoning[mode].startswith(self.emitted_reasoning[mode]):
                    raise _bad_stream()
                tail = reasoning[mode][len(self.emitted_reasoning[mode]) :]
                if tail:
                    yield {
                        "type": "reasoning",
                        "kind": mode,
                        "field": "reasoning",
                        "text": tail,
                    }
            if calls and (not self.tools_allowed or len(calls) > 4):
                raise _bad_stream()
            seen = set()
            for index, call in enumerate(calls):
                if call["call_id"] in seen:
                    raise _bad_stream()
                seen.add(call["call_id"])
                yield {
                    "type": "tool_delta",
                    "index": index,
                    "id": call["call_id"],
                    "name": call["name"],
                    "arguments": call["arguments"],
                }
            yield {"type": "replay", "protocol": "responses", "value": items}
            usage = response.get("usage")
            if isinstance(usage, dict):
                clean = _usage(
                    usage.get("input_tokens"),
                    usage.get("output_tokens"),
                    usage.get("total_tokens"),
                )
                if clean:
                    yield clean
            self.done = True
            yield {
                "type": "finish",
                "reason": "length"
                if kind == "response.incomplete"
                else "tool_calls"
                if calls
                else "stop",
            }
        elif not kind.startswith("response."):
            raise _bad_stream()


class AnthropicSSE(_SSE):
    max_frame_bytes = config.PROTOCOL_FRAME_BYTES

    def __init__(self, tools=False):
        super().__init__(tools)
        self.blocks = []
        self.active = None
        self.partial = ""
        self.started = False
        self.stop_reason = None
        self.input_tokens = None
        self.output_tokens = None

    def _frame(self, data):
        event = _strict_json(data)
        if not isinstance(event, dict):
            raise _bad_stream()
        kind = event.get("type")
        if kind == "ping":
            return
        if kind == "message_start":
            message = event.get("message")
            if (
                self.started
                or not isinstance(message, dict)
                or message.get("role") != "assistant"
            ):
                raise _bad_stream()
            self.started = True
            usage = message.get("usage", {})
            if isinstance(usage, dict):
                parts = [
                    usage.get("input_tokens"),
                    usage.get("cache_creation_input_tokens", 0),
                    usage.get("cache_read_input_tokens", 0),
                ]
                if all(type(n) is int and n >= 0 for n in parts):
                    self.input_tokens = sum(parts)
            return
        if not self.started:
            raise _bad_stream()
        if kind == "content_block_start":
            index, block = event.get("index"), event.get("content_block")
            if (
                self.active is not None
                or type(index) is not int
                or index != len(self.blocks)
                or len(self.blocks) >= 32
                or not isinstance(block, dict)
            ):
                raise _bad_stream()
            btype = block.get("type")
            if btype == "text":
                clean = {
                    "type": "text",
                    "text": _string(block.get("text", ""), config.RUN_TEXT_BYTES),
                }
                if clean["text"]:
                    yield {"type": "text", "text": clean["text"]}
            elif btype == "thinking":
                clean = {
                    "type": "thinking",
                    "thinking": _string(
                        block.get("thinking", ""), config.REASONING_BYTES
                    ),
                    "signature": _string(
                        block.get("signature", ""), config.PROTOCOL_STATE_BYTES
                    ),
                }
                if clean["thinking"]:
                    yield {
                        "type": "reasoning",
                        "kind": "summary",
                        "field": "thinking",
                        "text": clean["thinking"],
                    }
            elif btype == "redacted_thinking":
                clean = {
                    "type": btype,
                    "data": _string(
                        block.get("data"), config.PROTOCOL_STATE_BYTES, empty=False
                    ),
                }
            elif btype == "tool_use" and self.tools_allowed:
                clean = {
                    "type": btype,
                    "id": _id(block.get("id")),
                    "name": _id(block.get("name")),
                    "input": block.get("input", {}),
                }
                if not isinstance(clean["input"], dict):
                    raise _bad_stream()
            else:
                raise _bad_stream()
            self.blocks.append(clean)
            self.active, self.partial = index, ""
        elif kind == "content_block_delta":
            if (
                self.active is None
                or event.get("index") != self.active
                or not isinstance(event.get("delta"), dict)
            ):
                raise _bad_stream()
            block, delta = self.blocks[self.active], event["delta"]
            dtype = delta.get("type")
            if dtype == "text_delta" and block["type"] == "text":
                text = _string(delta.get("text"), config.RUN_TEXT_BYTES)
                block["text"] = _string(block["text"] + text, config.RUN_TEXT_BYTES)
                if text:
                    yield {"type": "text", "text": text}
            elif dtype == "thinking_delta" and block["type"] == "thinking":
                text = _string(delta.get("thinking"), config.REASONING_BYTES)
                block["thinking"] = _string(
                    block["thinking"] + text, config.REASONING_BYTES
                )
                if text:
                    yield {
                        "type": "reasoning",
                        "kind": "summary",
                        "field": "thinking",
                        "text": text,
                    }
            elif dtype == "signature_delta" and block["type"] == "thinking":
                block["signature"] = _string(
                    block["signature"] + delta.get("signature", ""),
                    config.PROTOCOL_STATE_BYTES,
                )
            elif dtype == "input_json_delta" and block["type"] == "tool_use":
                self.partial = _string(
                    self.partial + delta.get("partial_json", ""),
                    config.TOOL_ARGUMENT_BYTES,
                )
            else:
                raise _bad_stream()
        elif kind == "content_block_stop":
            if self.active is None or event.get("index") != self.active:
                raise _bad_stream()
            block = self.blocks[self.active]
            if block["type"] == "tool_use" and self.partial:
                block["input"] = _strict_json(_arguments(self.partial))
            self.active = None
        elif kind == "message_delta":
            delta, usage = event.get("delta"), event.get("usage", {})
            if (
                self.active is not None
                or not isinstance(delta, dict)
                or self.stop_reason is not None
            ):
                raise _bad_stream()
            self.stop_reason = delta.get("stop_reason")
            if self.stop_reason not in {
                "end_turn",
                "stop_sequence",
                "tool_use",
                "max_tokens",
                "refusal",
                "model_context_window_exceeded",
            }:
                raise _bad_stream()
            if isinstance(usage, dict):
                self.output_tokens = usage.get("output_tokens")
        elif kind == "message_stop":
            if self.active is not None or self.stop_reason is None:
                raise _bad_stream()
            blocks = validate_state("anthropic", self.blocks)
            calls = [b for b in blocks if b["type"] == "tool_use"]
            if len(calls) > 4 or (self.stop_reason == "tool_use") != bool(calls):
                raise _bad_stream()
            for index, call in enumerate(calls):
                yield {
                    "type": "tool_delta",
                    "index": index,
                    "id": call["id"],
                    "name": call["name"],
                    "arguments": json.dumps(
                        call["input"], ensure_ascii=False, separators=(",", ":")
                    ),
                }
            yield {"type": "replay", "protocol": "anthropic", "value": blocks}
            usage = _usage(self.input_tokens, self.output_tokens)
            if usage:
                yield usage
            self.done = True
            yield {
                "type": "finish",
                "reason": "tool_calls"
                if calls
                else "length"
                if self.stop_reason in {"max_tokens", "model_context_window_exceeded"}
                else "stop",
            }
        else:
            raise _bad_stream()


def _portable_messages(messages):
    if not isinstance(messages, list) or not messages:
        raise ChatError("模型消息无效。")
    for message in messages:
        if not isinstance(message, dict) or message.get("role") not in {
            "system",
            "user",
            "assistant",
            "tool",
        }:
            raise ChatError("模型消息无效。")
        if set(message) - {"role", "content", "tool_calls", "tool_call_id", "_native"}:
            raise ChatError("模型消息字段无效。")
        _string(message.get("content", ""), config.EXTENDED_CONTEXT_BYTES)
        role = message["role"]
        if "tool_call_id" in message:
            if role != "tool":
                raise ChatError("工具消息无效。")
            _id(message["tool_call_id"])
        elif role == "tool":
            raise ChatError("工具消息无效。")
        if "tool_calls" in message:
            calls = message["tool_calls"]
            if (
                role != "assistant"
                or not isinstance(calls, list)
                or not 1 <= len(calls) <= 4
            ):
                raise ChatError("工具消息无效。")
            seen = set()
            for call in calls:
                if (
                    not isinstance(call, dict)
                    or set(call) != {"id", "type", "function"}
                    or call["type"] != "function"
                ):
                    raise ChatError("工具消息无效。")
                cid = _id(call["id"])
                if cid in seen:
                    raise ChatError("工具消息无效。")
                seen.add(cid)
                fn = call["function"]
                if not isinstance(fn, dict) or set(fn) != {"name", "arguments"}:
                    raise ChatError("工具消息无效。")
                _id(fn["name"])
                _arguments(fn["arguments"])
        if "_native" in message:
            native = message["_native"]
            if (
                role != "assistant"
                or not isinstance(native, dict)
                or set(native) != {"protocol", "value"}
            ):
                raise ChatError("协议状态无效。")
            validate_state(native["protocol"], native["value"])
    return messages


def _wire_body(protocol, body):
    """Convert an internal portable request; never accept arbitrary provider JSON."""
    if protocol not in PROTOCOLS or not isinstance(body, dict):
        raise ChatError("模型协议无效。")
    allowed = {
        "model",
        "messages",
        "stream",
        "max_tokens",
        "max_completion_tokens",
        "stream_options",
        "tools",
        "tool_choice",
        "n",
        "reasoning_effort",
        "reasoning",
        "thinking",
        "_window",
    }
    if set(body) - allowed:
        raise ChatError("模型请求字段无效。")
    model = _string(body.get("model"), 256, empty=False)
    if any(ord(c) < 32 or ord(c) == 127 for c in model):
        raise ChatError("模型名称无效。")
    messages = _portable_messages(body.get("messages"))
    if any(
        m.get("_native", {}).get("protocol", protocol) != protocol for m in messages
    ):
        raise ChatError("不可跨协议重放模型状态。")
    if body.get("stream", True) is not True:
        raise ChatError("模型流式参数无效。")
    maximums = [body[k] for k in ("max_tokens", "max_completion_tokens") if k in body]
    maximum = (
        maximums[0]
        if len(maximums) == 1
        else config.OUTPUT_TOKENS_PER_REQUEST
        if not maximums
        else None
    )
    if type(maximum) is not int or not 1 <= maximum <= config.MAX_OUTPUT_TOKENS:
        raise ChatError("模型输出上限无效。")
    if "stream_options" in body:
        if (
            protocol != "chat_completions"
            or body["stream_options"] != {"include_usage": True}
            or type(body["stream_options"].get("include_usage")) is not bool
        ):
            raise ChatError("流用量选项无效。")
    if "reasoning_effort" in body and (
        protocol != "chat_completions"
        or body["reasoning_effort"]
        not in {"none", "minimal", "low", "medium", "high", "xhigh"}
    ):
        raise ChatError("推理参数无效。")
    if "reasoning" in body:
        reasoning = body["reasoning"]
        if (
            protocol != "responses"
            or not isinstance(reasoning, dict)
            or not reasoning
            or set(reasoning) - {"effort", "summary"}
        ):
            raise ChatError("推理参数无效。")
        if "effort" in reasoning and reasoning["effort"] not in {
            "none",
            "minimal",
            "low",
            "medium",
            "high",
            "xhigh",
        }:
            raise ChatError("推理参数无效。")
        if "summary" in reasoning and reasoning["summary"] not in {
            "auto",
            "concise",
            "detailed",
        }:
            raise ChatError("推理摘要参数无效。")
    if "thinking" in body:
        thinking = body["thinking"]
        if protocol != "anthropic" or not isinstance(thinking, dict):
            raise ChatError("思考参数无效。")
        if thinking not in ({"type": "adaptive"}, {"type": "disabled"}):
            if (
                set(thinking) != {"type", "budget_tokens"}
                or thinking["type"] != "enabled"
                or type(thinking["budget_tokens"]) is not int
                or not 1024 <= thinking["budget_tokens"] < maximum
            ):
                raise ChatError("思考预算无效。")
    window = body.get("_window")
    if "_window" in body and protocol != "responses":
        raise ChatError("原生压缩窗口协议无效。")
    if window is not None:
        validate_window(window)
    n = body.get("n", 1)
    if (
        type(n) is not int
        or not 1 <= n <= config.MAX_CANDIDATES
        or protocol != "chat_completions"
        and n != 1
    ):
        raise ChatError("候选数量无效。")
    tools = body.get("tools", [])
    if (
        not isinstance(tools, list)
        or len(tools) > config.MAX_TOOL_SCHEMAS
        or tools
        and n != 1
    ):
        raise ChatError("工具配置无效。")
    if "tool_choice" in body and (not tools or body["tool_choice"] != "auto"):
        raise ChatError("工具选择无效。")
    for tool in tools:
        if (
            not isinstance(tool, dict)
            or set(tool) != {"type", "function"}
            or tool["type"] != "function"
            or not isinstance(tool["function"], dict)
        ):
            raise ChatError("工具定义无效。")
        fn = tool["function"]
        if set(fn) - {"name", "description", "parameters", "strict"} or not isinstance(
            fn.get("parameters"), dict
        ):
            raise ChatError("工具定义无效。")
        _id(fn.get("name"))
        if "description" in fn:
            _string(fn["description"], 8192)
        if "strict" in fn and type(fn["strict"]) is not bool:
            raise ChatError("工具定义无效。")
    if protocol == "chat_completions":
        clean = {
            k: v
            for k, v in body.items()
            if k not in {"messages", "reasoning", "thinking"}
        }
        clean["stream"] = True
        clean.setdefault("max_tokens", maximum) if not maximums else None
        clean["messages"] = []
        for message in messages:
            item = {
                k: message[k]
                for k in ("role", "content", "tool_calls", "tool_call_id")
                if k in message
            }
            native = message.get("_native")
            if isinstance(native, dict) and native.get("protocol") == protocol:
                item.update(validate_state(protocol, native["value"]))
            clean["messages"].append(item)
        return _bounded(clean, config.MODEL_REQUEST_BYTES)
    common = {"model": model, "stream": True}
    if protocol == "responses":
        inputs = []
        for message in messages:
            role = message["role"]
            native = message.get("_native")
            if (
                role == "assistant"
                and isinstance(native, dict)
                and native.get("protocol") == protocol
            ):
                inputs.extend(validate_state(protocol, native["value"]))
                continue
            if role == "tool":
                inputs.append(
                    {
                        "type": "function_call_output",
                        "call_id": _id(message.get("tool_call_id")),
                        "output": message["content"],
                    }
                )
                continue
            if message.get("content") or not message.get("tool_calls"):
                inputs.append({"role": role, "content": message.get("content", "")})
            for call in message.get("tool_calls", []):
                inputs.append(
                    {
                        "type": "function_call",
                        "call_id": _id(call.get("id")),
                        "name": _id(call.get("function", {}).get("name")),
                        "arguments": _arguments(
                            call.get("function", {}).get("arguments")
                        ),
                    }
                )
        if window is not None:
            # Current system/personal messages remain before the saved window.
            prefix = 0
            while prefix < len(messages) and messages[prefix]["role"] == "system":
                prefix += 1
            inputs[prefix:prefix] = window
        common.update(
            input=inputs,
            max_output_tokens=maximum,
            store=False,
            include=["reasoning.encrypted_content"],
        )
        if "reasoning" in body:
            common["reasoning"] = body["reasoning"]
        if tools:
            common.update(
                tools=[{"type": "function", **t["function"]} for t in tools],
                tool_choice="auto",
            )
    else:
        system, output = [], []
        for message in messages:
            role = message["role"]
            if role == "system":
                system.append(message["content"])
                continue
            native = message.get("_native")
            if (
                role == "assistant"
                and isinstance(native, dict)
                and native.get("protocol") == protocol
            ):
                content = validate_state(protocol, native["value"])
            elif role == "tool":
                content = [
                    {
                        "type": "tool_result",
                        "tool_use_id": _id(message.get("tool_call_id")),
                        "content": message["content"],
                    }
                ]
                role = "user"
            else:
                content = (
                    [{"type": "text", "text": message["content"]}]
                    if message.get("content")
                    else []
                )
                for call in message.get("tool_calls", []):
                    content.append(
                        {
                            "type": "tool_use",
                            "id": _id(call.get("id")),
                            "name": _id(call.get("function", {}).get("name")),
                            "input": _strict_json(
                                _arguments(call.get("function", {}).get("arguments"))
                            ),
                        }
                    )
            if output and output[-1]["role"] == role:
                output[-1]["content"].extend(content)
            else:
                output.append({"role": role, "content": list(content)})
        common.update(messages=output, max_tokens=maximum)
        if system:
            common["system"] = "\n\n".join(system)
        if "thinking" in body:
            common["thinking"] = body["thinking"]
        if tools:
            common.update(
                tools=[
                    {
                        "name": t["function"]["name"],
                        "description": t["function"].get("description", ""),
                        "input_schema": t["function"]["parameters"],
                    }
                    for t in tools
                ],
                tool_choice={"type": "auto"},
            )
    return _bounded(common, config.MODEL_REQUEST_BYTES)


def wire_body(protocol, body):
    try:
        return _wire_body(protocol, body)
    except ChatError as exc:
        if exc.code != "CHAT_UPSTREAM_INTERRUPTED":
            raise
    except (KeyError, TypeError, ValueError, UnicodeError, RecursionError):
        pass
    raise ChatError("模型协议请求无效。") from None


def parser(protocol, tools=False, candidates=1):
    if protocol == "chat_completions":
        return ChatSSE(tools, candidates)
    if candidates != 1:
        raise ChatError("该协议的多候选需要独立模型调用。")
    if protocol == "responses":
        return ResponsesSSE(tools)
    if protocol == "anthropic":
        return AnthropicSSE(tools)
    raise ChatError("模型协议无效。")
