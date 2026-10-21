"""Single bounded ChatCompletions SSE POST, using only the Python stdlib.

The iterator is tentative until consumed through [DONE]. Never retry after any
error, even if no text was yielded. ``deadline`` is absolute time.monotonic().
No request credentials are persisted/logged; no redirects, proxies or SDKs.
"""

import codecs
import errno
from http.client import HTTPException, HTTPResponse
import io
import json
import math
import os
import re
import select
import socket
import ssl
import threading
import time
from urllib.parse import urlsplit

from . import config
from .errors import ChatError
from .resolver import resolve_address


def _close(sock):
    try:
        sock.shutdown(socket.SHUT_RDWR)
    except (OSError, ValueError):
        pass
    try:
        sock.close()
    except (OSError, ValueError):
        pass


class AbortControl:
    """Sticky cancellation gate shared by registration and nonblocking sends.

    abort(reason='CHAT_CANCELLED') can also latch CHAT_DEADLINE. Once latched,
    register() immediately closes a new socket and raises; sendall()/guard()
    cannot initiate any further I/O. No mutex is held during select/waits.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._reason = None
        self._sockets = set()

    @property
    def aborted(self):
        with self._lock:
            return self._reason is not None

    def _check_locked(self):
        if self._reason:
            raise ChatError(
                "本轮已停止。",
                504 if self._reason == "CHAT_DEADLINE" else 409,
                self._reason,
            )

    def check(self):
        with self._lock:
            self._check_locked()

    def register(self, sock):
        with self._lock:
            if self._reason:
                _close(sock)
                self._check_locked()
            self._sockets.add(sock)
        return sock

    def unregister(self, sock):
        with self._lock:
            self._sockets.discard(sock)

    def abort(self, reason="CHAT_CANCELLED"):
        if reason not in {"CHAT_CANCELLED", "CHAT_DEADLINE"}:
            raise ValueError("unsupported abort reason")
        with self._lock:
            if self._reason is not None:
                return
            self._reason = reason
            sockets, self._sockets = self._sockets, set()
        for sock in sockets:
            _close(sock)

    def guard(self, operation, *args):
        """Only for nonblocking socket operations, never blocking callbacks."""
        with self._lock:
            self._check_locked()
            return operation(*args)

    def sendall(self, sock, data, deadline):
        view = memoryview(data)
        while view:
            _remaining(deadline)
            try:
                sent = self.guard(sock.send, view)
                if not sent:
                    raise OSError("closed")
                view = view[sent:]
            except (BlockingIOError, ssl.SSLWantWriteError):
                _wait(sock, False, deadline, self)
            except ssl.SSLWantReadError:
                _wait(sock, True, deadline, self)


def _remaining(deadline):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise ChatError("本轮已超过截止时间。", 504, "CHAT_DEADLINE")
    return remaining


def _wait(sock, reading, deadline, abort):
    while True:
        abort.check()
        delay = min(0.05, _remaining(deadline))
        readable, writable, _ = select.select(
            [sock] if reading else [], [] if reading else [sock], [], delay
        )
        if readable or writable:
            abort.check()
            return


def _bad_stream():
    return ChatError(
        "上游流格式无效或未完整结束。", 502, "CHAT_UPSTREAM_INTERRUPTED", uncertain=True
    )


def _strict_json(value):
    def pairs(items):
        result = {}
        for key, item in items:
            if key in result:
                raise ValueError()
            result[key] = item
        return result

    def invalid(_):
        raise ValueError()

    def finite(value):
        result = float(value)
        if not math.isfinite(result):
            raise ValueError()
        return result

    return json.loads(
        value, object_pairs_hook=pairs, parse_constant=invalid, parse_float=finite
    )


class _SSE:
    def __init__(self, tools):
        self.tools_allowed = tools
        self.decoder = codecs.getincrementaldecoder("utf-8")("strict")
        self.line, self.data = [], []
        self.frame_bytes = 0
        self.skip_lf = False
        self.first = True
        self.done = False
        self.finish = None
        self.calls = []
        self.text_bytes = 0
        self.usage_seen = False

    def feed(self, raw):
        for char in self.decoder.decode(raw, final=False):
            if self.first:
                self.first = False
                if char == "\ufeff":
                    continue
            self.frame_bytes += len(char.encode("utf-8"))
            if self.frame_bytes > getattr(
                self, "max_frame_bytes", config.SSE_FRAME_BYTES
            ):
                raise _bad_stream()
            if char == "\n" and self.skip_lf:
                self.skip_lf = False
                continue
            self.skip_lf = char == "\r"
            if char in "\r\n":
                line, self.line = "".join(self.line), []
                if not line:
                    data, self.data = "\n".join(self.data), []
                    self.frame_bytes = 0
                    if data:
                        yield from self._frame(data)
                        if self.done:
                            return
                elif line.startswith("data:"):
                    value = line[5:]
                    self.data.append(value[1:] if value.startswith(" ") else value)
                elif line == "data":
                    self.data.append("")
                # comments and other SSE fields are inert, but still bounded.
            else:
                self.line.append(char)

    def _validate_calls(self):
        ids = set()
        for call in self.calls:
            if (
                not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", call["id"])
                or call["id"] in ids
                or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", call["name"])
            ):
                raise _bad_stream()
            ids.add(call["id"])
            if not isinstance(_strict_json(call["arguments"]), dict):
                raise _bad_stream()

    def _frame(self, data):
        if data == "[DONE]":
            if self.finish is None:
                raise _bad_stream()
            self._validate_calls()
            self.done = True
            return
        chunk = _strict_json(data)
        if not isinstance(chunk, dict) or "error" in chunk:
            raise _bad_stream()
        choices = chunk.get("choices")
        if not isinstance(choices, list) or len(choices) > 1:
            raise _bad_stream()
        if choices:
            choice = choices[0]
            if (
                not isinstance(choice, dict)
                or type(choice.get("index")) is not int
                or choice["index"] != 0
                or self.finish is not None
            ):
                raise _bad_stream()
            delta = choice.get("delta")
            if not isinstance(delta, dict) or "function_call" in delta:
                raise _bad_stream()
            if delta.get("role") not in (None, "assistant"):
                raise _bad_stream()
            text = delta.get("content")
            if text is not None:
                if not isinstance(text, str):
                    raise _bad_stream()
                self.text_bytes += len(text.encode("utf-8"))
                if self.text_bytes > config.RUN_TEXT_BYTES:
                    raise _bad_stream()
                if text:
                    yield {"type": "text", "text": text}
            calls = delta.get("tool_calls")
            if calls is not None:
                if (
                    not self.tools_allowed
                    or not isinstance(calls, list)
                    or len(calls) > 4
                ):
                    raise _bad_stream()
                for part in calls:
                    if not isinstance(part, dict) or type(part.get("index")) is not int:
                        raise _bad_stream()
                    index = part["index"]
                    if not 0 <= index <= len(self.calls) or index >= 4:
                        raise _bad_stream()
                    if part.get("type", "function") != "function":
                        raise _bad_stream()
                    if index == len(self.calls):
                        self.calls.append({"id": "", "name": "", "arguments": ""})
                    function = part.get("function", {})
                    if not isinstance(function, dict):
                        raise _bad_stream()
                    event = {"type": "tool_delta", "index": index}
                    for field, value in (
                        ("id", part.get("id")),
                        ("name", function.get("name")),
                        ("arguments", function.get("arguments")),
                    ):
                        if value is not None:
                            if not isinstance(value, str):
                                raise _bad_stream()
                            self.calls[index][field] += value
                            limit = (
                                config.TOOL_ARGUMENT_BYTES
                                if field == "arguments"
                                else 128
                            )
                            if len(self.calls[index][field].encode()) > limit:
                                raise _bad_stream()
                            event[field] = value
                    if any(event.get(field) for field in ("id", "name", "arguments")):
                        yield event
            finish = choice.get("finish_reason")
            if finish is not None:
                if finish not in ("stop", "tool_calls", "length", "content_filter"):
                    raise _bad_stream()
                if (finish == "tool_calls") != bool(self.calls) and finish in (
                    "stop",
                    "tool_calls",
                ):
                    raise _bad_stream()
                self._validate_calls()
                self.finish = finish
                yield {"type": "finish", "reason": finish}
        usage = chunk.get("usage")
        if usage is not None:
            if self.usage_seen or not isinstance(usage, dict):
                raise _bad_stream()
            clean = {
                k: usage.get(k)
                for k in ("prompt_tokens", "completion_tokens", "total_tokens")
            }
            if any(
                type(v) is not int or v < 0 or v > 9007199254740991
                for v in clean.values()
            ):
                raise _bad_stream()
            self.usage_seen = True
            yield {"type": "usage", **clean}
        elif not choices:
            raise _bad_stream()


class _SocketReader(io.RawIOBase):
    """Enforce idle/deadline even inside HTTP headers and chunk-size lines."""

    def __init__(self, sock, deadline, abort):
        super().__init__()
        self.sock, self.deadline, self.abort = sock, deadline, abort
        self.last_valid = time.monotonic()
        self.total = 0

    def readable(self):
        return True

    def readinto(self, buffer):
        while True:
            self.abort.check()
            remaining = _remaining(self.deadline)
            idle = config.UPSTREAM_IDLE_SECONDS - (time.monotonic() - self.last_valid)
            if idle <= 0:
                raise ChatError(
                    "上游长时间没有有效数据。",
                    504,
                    "CHAT_UPSTREAM_INTERRUPTED",
                    uncertain=True,
                )
            try:
                count = self.sock.recv_into(buffer)
                self.total += count
                if self.total > config.UPSTREAM_BYTES_PER_REQUEST:
                    raise _bad_stream()
                return count
            except (BlockingIOError, ssl.SSLWantReadError):
                select.select([self.sock], [], [], min(0.05, idle, remaining))
            except ssl.SSLWantWriteError:
                select.select([], [self.sock], [], min(0.05, idle, remaining))


class _ResponseSocket:
    def __init__(self, reader):
        self.reader = reader

    def makefile(self, mode):
        return io.BufferedReader(self.reader, buffer_size=8192)


def _request(key, body):
    if (
        not isinstance(key, str)
        or not 1 <= len(key) <= 4096
        or any(not 33 <= ord(c) <= 126 for c in key)
    ):
        raise ChatError("模型凭据无效。")
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
    }
    if not isinstance(body, dict) or set(body) - allowed:
        raise ChatError("模型请求字段无效。")
    body = dict(body)
    if (
        not isinstance(body.get("model"), str)
        or not body["model"]
        or len(body["model"].encode()) > 256
        or not isinstance(body.get("messages"), list)
        or not body["messages"]
        or body.get("stream", True) is not True
        or type(body.get("n", 1)) is not int
        or body.get("n", 1) != 1
    ):
        raise ChatError("模型请求无效。")
    if (
        len(
            json.dumps(
                body["messages"],
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode()
        )
        > config.CONTEXT_BYTES
    ):
        raise ChatError("模型上下文超过大小限制。", 413, "CHAT_CONTEXT_TOO_LARGE")
    body["stream"] = True
    if not {"max_tokens", "max_completion_tokens"} & set(body):
        body["max_tokens"] = config.OUTPUT_TOKENS_PER_REQUEST
    limits = [body[k] for k in ("max_tokens", "max_completion_tokens") if k in body]
    if (
        len(limits) != 1
        or type(limits[0]) is not int
        or not 1 <= limits[0] <= config.OUTPUT_TOKENS_PER_REQUEST
    ):
        raise ChatError("模型输出上限无效。")
    if "stream_options" in body:
        options = body["stream_options"]
        if (
            not isinstance(options, dict)
            or set(options) != {"include_usage"}
            or options["include_usage"] is not True
        ):
            raise ChatError("流用量选项无效。")
    tools = body.get("tools", [])
    if (
        not isinstance(tools, list)
        or len(tools) > 4
        or (not tools and "tool_choice" in body)
    ):
        raise ChatError("工具配置无效。")
    if tools and body.get("tool_choice", "auto") != "auto":
        raise ChatError("工具配置无效。")
    raw = json.dumps(
        body, ensure_ascii=False, separators=(",", ":"), allow_nan=False
    ).encode()
    if len(raw) > config.JSON_REQUEST_BYTES:
        raise ChatError("模型请求超过大小限制。", 413, "CHAT_REQUEST_TOO_LARGE")
    return _fixed_request(raw, bool(tools), key, "chat_completions")


def _protocol_request(key, body, protocol, context_limit=config.EXTENDED_CONTEXT_BYTES):
    from .protocols import validate_window, wire_body

    if (
        not isinstance(key, str)
        or not 1 <= len(key) <= 4096
        or any(not 33 <= ord(c) <= 126 for c in key)
    ):
        raise ChatError("模型凭据无效。")
    if not isinstance(body, dict):
        raise ChatError("模型请求无效。")
    messages = (
        body.get("input")
        if protocol == "responses_compact"
        else body.get("messages")
        if "_window" not in body
        else {"messages": body.get("messages"), "_window": body["_window"]}
    )
    try:
        if (
            len(
                json.dumps(
                    messages, ensure_ascii=False, allow_nan=False, separators=(",", ":")
                ).encode()
            )
            > context_limit
        ):
            raise ChatError("模型上下文超过大小限制。", 413, "CHAT_CONTEXT_TOO_LARGE")
    except (TypeError, ValueError, UnicodeError):
        raise ChatError("模型消息无效。") from None
    if protocol == "responses_compact":
        if set(body) != {"model", "input"}:
            raise ChatError("原生压缩请求无效。")
        wire_body(
            "responses",
            {
                "model": body["model"],
                "messages": [{"role": "user", "content": "validate"}],
            },
        )
        validate_window(body["input"])
        prepared = body
    else:
        prepared = wire_body(protocol, body)
    raw = json.dumps(
        prepared, ensure_ascii=False, separators=(",", ":"), allow_nan=False
    ).encode()
    if len(raw) > config.MODEL_REQUEST_BYTES:
        raise ChatError("模型请求超过大小限制。", 413, "CHAT_REQUEST_TOO_LARGE")
    tools = bool(prepared.get("tools"))
    return _fixed_request(raw, tools, key, protocol)


def _fixed_request(raw, tools, key, protocol):
    raw_url = os.environ.get("NEW_API_INTERNAL_URL", "http://new-api:3000")
    if any(ord(c) <= 32 or ord(c) == 127 for c in raw_url):
        raise ChatError("固定上游地址配置无效。", 503, "CHAT_CONFIG_INVALID")
    base = urlsplit(raw_url)
    if (
        base.scheme not in {"http", "https"}
        or not base.hostname
        or base.username is not None
        or base.password is not None
        or base.query
        or base.fragment
        or any(ord(c) <= 32 or ord(c) == 127 for c in base.geturl())
    ):
        raise ChatError("固定上游地址配置无效。", 503, "CHAT_CONFIG_INVALID")
    host = base.hostname.encode("idna").decode("ascii")
    port = (
        base.port if base.port is not None else (443 if base.scheme == "https" else 80)
    )
    if not 1 <= port <= 65535:
        raise ChatError("固定上游地址配置无效。", 503, "CHAT_CONFIG_INVALID")
    path = (
        base.path.rstrip("/")
        + {
            "chat_completions": "/v1/chat/completions",
            "responses": "/v1/responses",
            "anthropic": "/v1/messages",
            "responses_compact": "/v1/responses/compact",
        }[protocol]
    )
    authority = ("[" + host + "]") if ":" in host else host
    authority += ":" + str(port)
    auth = (
        f"x-api-key: {key}\r\nanthropic-version: 2023-06-01\r\n"
        if protocol == "anthropic"
        else f"Authorization: Bearer {key}\r\n"
    )
    headers = (
        f"POST {path} HTTP/1.1\r\nHost: {authority}\r\n"
        + auth
        + "Content-Type: application/json\r\n"
        + f"Accept: {'application/json' if protocol == 'responses_compact' else 'text/event-stream'}\r\nAccept-Encoding: identity\r\n"
        f"Content-Length: {len(raw)}\r\nConnection: close\r\n\r\n"
    ).encode("ascii")
    return base.scheme, host, port, headers + raw, bool(tools)


def _stream(
    key, request_body, deadline, abort, *, protocol="chat_completions", legacy=False
):
    """Yield text/tool_delta/finish/usage dictionaries; no transparent retries."""
    sock, response = None, None
    sent = False
    valid_deadline = type(deadline) in (int, float) and math.isfinite(deadline)
    try:
        if not valid_deadline:
            raise ChatError("运行截止时间无效。")
        abort.check()
        _remaining(deadline)
        scheme, host, port, payload, tools = (
            _request(key, request_body)
            if legacy
            else _protocol_request(key, request_body, protocol)
        )
        connect_deadline = min(
            deadline, time.monotonic() + config.CONNECT_TIMEOUT_SECONDS
        )
        family, kind, socket_protocol, address = resolve_address(
            host, port, connect_deadline, abort
        )
        abort.check()
        _remaining(deadline)
        sock = abort.register(socket.socket(family, kind, socket_protocol))
        sock.setblocking(False)
        result = abort.guard(sock.connect_ex, address)
        if result not in (0, errno.EISCONN):
            if result not in (errno.EINPROGRESS, errno.EWOULDBLOCK, errno.EALREADY):
                raise OSError("connect failed")
            _wait(sock, False, connect_deadline, abort)
            if sock.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR):
                raise OSError("connect failed")
        if scheme == "https":
            wrapped = ssl.create_default_context().wrap_socket(
                sock, server_hostname=host, do_handshake_on_connect=False
            )
            abort.unregister(sock)
            sock = abort.register(wrapped)
            sock.setblocking(False)
            while True:
                try:
                    abort.guard(sock.do_handshake)
                    break
                except ssl.SSLWantReadError:
                    _wait(sock, True, connect_deadline, abort)
                except ssl.SSLWantWriteError:
                    _wait(sock, False, connect_deadline, abort)
        abort.check()
        _remaining(deadline)
        sent = True  # Conservative dispatch boundary; never retry any exception.
        abort.sendall(sock, payload, deadline)
        reader = _SocketReader(sock, deadline, abort)
        response = HTTPResponse(_ResponseSocket(reader))
        response.begin()
        if (
            response.status != 200
            or response.headers.get_content_type()
            != (
                "application/json"
                if protocol == "responses_compact"
                else "text/event-stream"
            )
            or response.headers.get("Content-Encoding", "identity").lower()
            != "identity"
            or response.headers.get_content_charset("utf-8").lower()
            not in {"utf-8", "utf8"}
        ):
            raise ChatError(
                "上游拒绝请求或返回了不支持的响应。",
                502,
                "CHAT_UPSTREAM_ERROR",
                uncertain=True,
            )
        if (
            response.length is not None
            and response.length > config.UPSTREAM_BYTES_PER_REQUEST
        ):
            raise _bad_stream()
        if protocol == "responses_compact":
            from .protocols import _usage, validate_window

            raw_result = bytearray()
            while True:
                abort.check()
                _remaining(deadline)
                chunk = response.read1(8192)
                if not chunk:
                    break
                raw_result.extend(chunk)
                if len(raw_result) > config.MODEL_REQUEST_BYTES:
                    raise _bad_stream()
            value = _strict_json(raw_result)
            if (
                not isinstance(value, dict)
                or value.get("object") != "response.compaction"
                or "error" in value
            ):
                raise _bad_stream()
            output = validate_window(value.get("output"))
            usage = value.get("usage")
            clean_usage = (
                _usage(
                    usage.get("input_tokens"),
                    usage.get("output_tokens"),
                    usage.get("total_tokens"),
                )
                if isinstance(usage, dict)
                else None
            )
            yield {"type": "compacted", "output": output, "usage": clean_usage}
            return
        if legacy:
            parser = _SSE(tools)
        else:
            from .protocols import parser as make_parser

            parser = make_parser(protocol, tools, request_body.get("n", 1))
        while not parser.done:
            abort.check()
            _remaining(deadline)
            raw = response.read1(8192)
            if not raw:
                parser.decoder.decode(b"", final=True)
                raise _bad_stream()
            for event in parser.feed(raw):
                abort.check()
                _remaining(deadline)
                reader.last_valid = time.monotonic()
                yield event
        abort.check()
        _remaining(deadline)
    except ChatError as exc:
        if valid_deadline and time.monotonic() >= deadline:
            raise ChatError(
                "本轮已超过截止时间。", 504, "CHAT_DEADLINE", uncertain=sent
            ) from None
        if exc.code == "CHAT_DEADLINE" and not sent and not abort.aborted:
            raise ChatError("连接固定上游超时。", 504, "CHAT_UPSTREAM_ERROR") from None
        exc.uncertain = exc.uncertain or sent
        raise
    except (
        OSError,
        HTTPException,
        ValueError,
        TypeError,
        UnicodeError,
        RecursionError,
    ):
        try:
            abort.check()
        except ChatError as exc:
            exc.uncertain = sent
            raise
        if valid_deadline and time.monotonic() >= deadline:
            raise ChatError(
                "本轮已超过截止时间。", 504, "CHAT_DEADLINE", uncertain=sent
            ) from None
        raise ChatError(
            "上游连接失败或流未完整结束。",
            502,
            "CHAT_UPSTREAM_INTERRUPTED" if sent else "CHAT_UPSTREAM_ERROR",
            uncertain=sent,
        ) from None
    finally:
        if response is not None:
            response.close()
        if sock is not None:
            abort.unregister(sock)
            _close(sock)


def stream_chat(key, request_body, deadline, abort):
    """Original single-choice Chat interface, kept for existing callers."""
    yield from _stream(key, request_body, deadline, abort, legacy=True)


def stream_model(key, request_body, deadline, abort, *, protocol="chat_completions"):
    """Bounded protocol-aware stream; one actual HTTP request, never retries."""
    yield from _stream(key, request_body, deadline, abort, protocol=protocol)


def compact_model(key, request_body, deadline, abort):
    """One explicitly enabled native compaction call; no summary fallback."""
    result = list(
        _stream(key, request_body, deadline, abort, protocol="responses_compact")
    )
    if len(result) != 1 or result[0]["type"] != "compacted":
        raise _bad_stream()
    return result[0]
