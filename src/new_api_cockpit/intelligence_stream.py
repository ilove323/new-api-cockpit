"""Bounded SSE decoding for the stateless drawing test, without tool execution."""

import codecs
import json


class DrawingStream:
    """Consume arbitrary UTF-8/HTTP chunks; only completed protocol messages count."""

    def __init__(self, endpoint):
        if endpoint not in {"openai", "anthropic"}:
            raise ValueError("Unsupported drawing protocol")
        self.endpoint = endpoint
        self.decoder = codecs.getincrementaldecoder("utf-8-sig")("strict")
        self.line = []
        self.data = []
        self.skip_lf = False
        self.done = False
        self.reason = None
        self.started = False
        self.active = None
        self.blocks = 0

    def feed(self, raw):
        if self.done:
            return
        for char in self.decoder.decode(raw):
            if char == "\n" and self.skip_lf:
                self.skip_lf = False
                continue
            self.skip_lf = char == "\r"
            if char not in "\r\n":
                self.line.append(char)
                continue
            line, self.line = "".join(self.line), []
            if not line:
                data, self.data = "\n".join(self.data), []
                if data:
                    yield from self._frame(data)
                    if self.done:
                        return
                else:
                    yield {"type": "heartbeat"}
            elif line == "data":
                self.data.append("")
            elif line.startswith("data:"):
                value = line[5:]
                self.data.append(value[1:] if value.startswith(" ") else value)

    def end(self):
        try:
            self.decoder.decode(b"", final=True)
        except UnicodeError:
            if self.endpoint != "openai" or self.reason is None:
                raise
        if self.endpoint == "openai" and self.reason is not None:
            self.done = True
        if not self.done:
            raise ValueError("Incomplete drawing stream")

    def _frame(self, data):
        if self.endpoint == "openai" and self.reason is not None:
            # The drawing is complete, but consume the normal accounting tail
            # before closing New API. No later usage frame can change its text.
            if data == "[DONE]":
                self.done = True
            return
        if self.endpoint == "openai" and data == "[DONE]":
            if self.reason is None:
                raise ValueError("Missing finish reason")
            self.done = True
            return
        value = json.loads(data)
        if not isinstance(value, dict) or "error" in value:
            raise ValueError("Invalid drawing event")
        if self.endpoint == "openai":
            yield from self._openai(value)
        else:
            yield from self._anthropic(value)

    @staticmethod
    def _text(value):
        if not isinstance(value, str):
            raise ValueError("Invalid text delta")
        if value:
            yield {"type": "text", "text": value}

    def _openai(self, value):
        choices = value.get("choices")
        if not isinstance(choices, list) or len(choices) > 1:
            raise ValueError("Invalid drawing choices")
        if not choices:  # Optional usage-only trailer.
            return
        choice = choices[0]
        if (
            not isinstance(choice, dict)
            or type(choice.get("index")) is not int
            or choice["index"] != 0
            or self.reason
        ):
            raise ValueError("Invalid drawing choice")
        delta = choice.get("delta")
        if not isinstance(delta, dict):
            raise ValueError("Invalid drawing delta")
        if delta.get("reasoning_content") or delta.get("reasoning"):
            # Report activity, never forward or retain the reasoning itself.
            yield {"type": "status", "stage": "thinking"}
        if delta.get("content") is not None:
            yield from self._text(delta["content"])
        reason = choice.get("finish_reason")
        if reason is not None:
            if reason not in {
                "stop",
                "length",
                "content_filter",
                "tool_calls",
                "function_call",
            }:
                raise ValueError("Unknown drawing finish reason")
            self.reason = reason

    def _anthropic(self, value):
        kind = value.get("type")
        if kind == "ping":
            yield {"type": "heartbeat"}
            return
        if kind == "message_start":
            message = value.get("message")
            if (
                self.started
                or not isinstance(message, dict)
                or message.get("role") != "assistant"
            ):
                raise ValueError("Invalid message start")
            self.started = True
            return
        if not self.started:
            raise ValueError("Missing message start")
        if kind == "content_block_start":
            block, index = value.get("content_block"), value.get("index")
            if (
                self.active is not None
                or type(index) is not int
                or index != self.blocks
                or not isinstance(block, dict)
            ):
                raise ValueError("Invalid content block")
            self.active = block.get("type")
            self.blocks += 1
            if self.active == "text":
                yield from self._text(block.get("text", ""))
            elif self.active in {"thinking", "redacted_thinking"}:
                yield {"type": "status", "stage": "thinking"}
        elif kind in {"content_block_delta", "content_block_stop"}:
            if self.active is None or value.get("index") != self.blocks - 1:
                raise ValueError("Invalid block index")
            if kind == "content_block_stop":
                self.active = None
                return
            delta = value.get("delta")
            if not isinstance(delta, dict):
                raise ValueError("Invalid block delta")
            if delta.get("type") == "text_delta":
                if self.active != "text":
                    raise ValueError("Text outside text block")
                yield from self._text(delta.get("text"))
            elif delta.get("type") == "thinking_delta":
                if self.active != "thinking":
                    raise ValueError("Thinking outside thinking block")
                yield {"type": "status", "stage": "thinking"}
        elif kind == "message_delta":
            delta = value.get("delta")
            if not isinstance(delta, dict):
                raise ValueError("Invalid message delta")
            reason = delta.get("stop_reason")
            if reason is not None:
                if not isinstance(reason, str) or self.reason is not None:
                    raise ValueError("Invalid stop reason")
                self.reason = reason
        elif kind == "message_stop":
            if self.active is not None or self.reason is None:
                raise ValueError("Incomplete message")
            self.done = True
