"""TEST ONLY: real Flask/ChatService with synthetic identity and model events.

Started by chat-integration.cjs, never by the runtime. No real New API, database,
browser account, credential or model request is involved. Web renders templates;
chat owns a fresh, resolved temporary data directory and 53 empty conversations.
"""

import argparse
from contextlib import ExitStack
import importlib
import ipaddress
import json
import os
from pathlib import Path
import signal
import socket
import sys
import threading
import time
import traceback
from unittest.mock import patch


PREFIX = "CHAT_BROWSER_FIXTURE "
MEMORY = "浏览器整合测试：偏好中文，先结论后依据。仅为合成资料。"
OUTPUT = "# 合成报告\n\n浏览器离线文件助手生成的新输出。\n上传原件不被覆盖；不调用真实模型。\n"


def emit(**value):
    print(PREFIX + json.dumps(value, ensure_ascii=False), flush=True)


def only_loopback_connect(original):
    def guarded(sock, address):
        if isinstance(address, tuple):
            try:
                allowed = ipaddress.ip_address(address[0]).is_loopback
            except ValueError:
                allowed = False
            if not allowed:
                raise AssertionError("Offline fixture forbids non-loopback connections")
        else:
            raise AssertionError("Offline fixture forbids non-IP connections")
        return original(sock, address)

    return guarded


class BrowserStream:
    """Mode comes from tools, not a global alternating-call counter."""

    def __init__(self):
        self.calls = 0

    def __call__(self, key, body, deadline, abort):
        self.calls += 1
        abort.check()
        if time.monotonic() >= deadline:
            raise TimeoutError("Synthetic stream deadline")
        if body["messages"][0]["content"].startswith(
            "You propose durable personal MEMORY facts"
        ):
            # UI auto-memory is on by default. These synthetic tasks have no
            # durable facts; still exercise a real auxiliary runner request.
            emit(kind="model_call", number=self.calls, phase="memory")
            yield {"type": "text", "text": '{"v":1,"operations":[]}'}
            yield {"type": "finish", "reason": "stop"}
            yield {
                "type": "usage",
                "prompt_tokens": 10,
                "completion_tokens": 5,
                "total_tokens": 15,
            }
            return
        files = bool(body.get("tools"))
        after_tool = body["messages"][-1]["role"] == "tool"
        phase = "write" if files and not after_tool else "final" if files else "chat"
        personal = body["messages"][1]["content"]
        emit(
            kind="model_call",
            number=self.calls,
            phase=phase,
            memory_present=MEMORY in personal,
            skill_present="browser-summary" in personal,
            tool_names=[tool["function"]["name"] for tool in body.get("tools", [])],
        )
        if phase == "write":
            yield {
                "type": "tool_delta",
                "index": 0,
                "id": f"browser_call_{self.calls}",
                "name": "write_file",
                "arguments": json.dumps(
                    {"name": "browser-report.md", "content": OUTPUT}, ensure_ascii=False
                ),
            }
            yield {"type": "finish", "reason": "tool_calls"}
        else:
            text = (
                "已生成 browser-report.md，仅包含合成资料。"
                if files
                else "你好，浏览器离线整合回答。"
            )
            yield {"type": "text", "text": text[:4]}
            time.sleep(0.01)
            abort.check()
            yield {"type": "text", "text": text[4:]}
            yield {"type": "finish", "reason": "stop"}
        yield {
            "type": "usage",
            "prompt_tokens": 10,
            "completion_tokens": 5,
            "total_tokens": 15,
        }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--role", choices=("web", "chat"), required=True)
    parser.add_argument("--data-dir", required=True)
    args = parser.parse_args()
    root = Path(args.data_dir).resolve()
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.environ.update(
        COCKPIT_PROCESS_ROLE=args.role,
        CHAT_ENABLED="true",
        NEW_API_INTERNAL_URL="http://127.0.0.1:9",
        PGHOST="127.0.0.1",
        PGPORT="9",
        PGPASSWORD="synthetic-only",
    )
    # Explicit path setup keeps direct invocation predictable, without imports
    # from user's unrelated projects or installing the fixture into the package.
    project = Path(__file__).resolve().parents[2]
    sys.path[:0] = [str(project / "src"), str(project / "tests")]
    from chat_fixtures import access, create, make_service
    from flask import g
    from werkzeug.serving import make_server, WSGIRequestHandler

    host = importlib.import_module("new_api_cockpit.app")
    composition = importlib.import_module("new_api_cockpit.composition")
    locks = importlib.import_module("new_api_cockpit.features.chat.locks")
    from new_api_cockpit.features.chat.errors import ChatError

    actor = access(12)
    service = None
    stream = BrowserStream()
    if args.role == "chat":
        service, _, _ = make_service(root)
        service.runner.stream_chat = stream
        for number in range(1, 54):
            create(service, actor, title=f"离线空聊天 {number:02d}")

    def identity():
        g.auth_kind = "session"
        return actor.principal.as_user()

    def chat_service(_verify):
        if args.role != "chat":
            raise AssertionError("Web fixture must not read or create chat state")
        return service

    original_lock, original_close = locks.FileLock.__init__, locks.FileLock.close
    held, held_guard = {}, threading.Lock()

    def current_request():
        from flask import has_request_context, request

        return (
            {"method": request.method, "path": request.path}
            if has_request_context()
            else {"method": None, "path": None}
        )

    def diagnostic_lock(self, *values, **options):
        try:
            original_lock(self, *values, **options)
            with held_guard:
                held[self.path] = {
                    **current_request(),
                    "fd": self.fd,
                    "thread": threading.current_thread().name,
                }
        except ChatError as error:
            if "BUSY" in error.code:
                with held_guard:
                    owner = held.get(getattr(self, "path", ""))
                emit(
                    kind="lock_failure",
                    code=error.code,
                    lock_path=getattr(self, "path", ""),
                    requester=current_request(),
                    owner=owner,
                    stack=[
                        {
                            "file": Path(frame.filename).name,
                            "line": frame.lineno,
                            "function": frame.name,
                        }
                        for frame in traceback.extract_stack(limit=16)[:-1]
                    ],
                )
            raise

    def diagnostic_close(self):
        with held_guard:
            if held.get(getattr(self, "path", ""), {}).get("fd") == self.fd:
                held.pop(self.path, None)
        return original_close(self)

    class QuietHandler(WSGIRequestHandler):
        def log(self, _type, message, *values):
            # The Node gateway records status/path without request credentials.
            pass

    def stop(_signum, _frame):
        raise KeyboardInterrupt()

    server = None
    try:
        with ExitStack() as stack:
            stack.enter_context(patch.object(host, "request_identity", identity))
            stack.enter_context(
                patch.object(host, "new_api_credential", lambda: actor.credential)
            )
            stack.enter_context(
                patch.object(host, "load_site_name", lambda: "离线浏览器整合测试")
            )
            stack.enter_context(
                patch.object(composition, "get_chat_service", chat_service)
            )
            stack.enter_context(
                patch.object(locks.FileLock, "__init__", diagnostic_lock)
            )
            stack.enter_context(patch.object(locks.FileLock, "close", diagnostic_close))
            stack.enter_context(
                patch.object(
                    socket.socket,
                    "connect",
                    only_loopback_connect(socket.socket.connect),
                )
            )
            stack.enter_context(
                patch.object(
                    socket.socket,
                    "connect_ex",
                    only_loopback_connect(socket.socket.connect_ex),
                )
            )
            server = make_server(
                "127.0.0.1", 0, host.app, threaded=True, request_handler=QuietHandler
            )
            signal.signal(signal.SIGTERM, stop)
            signal.signal(signal.SIGINT, stop)
            emit(
                kind="ready",
                role=args.role,
                port=server.server_port,
                seeded=53 if service else 0,
                data_dir=str(root),
            )
            server.serve_forever(poll_interval=0.05)
    except KeyboardInterrupt:
        pass
    finally:
        if server is not None:
            server.server_close()
        if service is not None:
            service.shutdown(timeout=5)
            service.store.close()
        emit(kind="stopped", role=args.role, model_calls=stream.calls)


if __name__ == "__main__":
    main()
