"""Synthetic adapters shared by chat integration tests; no network or secrets."""

from contextlib import contextmanager
import json
from pathlib import Path
import threading
import time
from uuid import uuid4

from new_api_cockpit.features.chat.config import Settings
from new_api_cockpit.features.chat.service import ChatService
from new_api_cockpit.platform.identity import Principal, RequestAccess


class FakeTokens:
    def __init__(self):
        self.unavailable = False
        self.key_gate = None
        self.key_entered = threading.Event()

    def list_tokens(self, access):
        return [
            {
                "id": 7,
                "name": "测试KEY",
                "key_masked": "xxxx********xxxx",
                "group": "default",
                "status": 1,
                "expired_time": -1,
                "remain_quota": 100,
                "unlimited_quota": False,
                "usable": True,
                "unavailable_reason": "",
            }
        ]

    def list_models(self, access, token_id):
        if self.unavailable:
            raise ValueError("unavailable test adapter")
        return [{"id": "test-chat", "protocols": ["chat"]}]

    @contextmanager
    def lease_key(self, access, token_id):
        self.key_entered.set()
        if self.key_gate is not None:
            if not self.key_gate.wait(5):
                raise ValueError("test gate timed out")
        yield "sk-" + "testfixtureonly" * 4


class FakeVerifier:
    def revalidate(self, access):
        return access


class FakeStream:
    def __init__(self, mode="text"):
        self.mode = mode
        self.calls = []
        self.entered = threading.Event()
        self.release = threading.Event()

    def __call__(self, key, body, deadline, abort):
        self.calls.append(body)
        self.entered.set()
        number = len(self.calls)
        if self.mode == "blocked":
            while not self.release.wait(0.01):
                abort.check()
                if time.monotonic() >= deadline:
                    raise TimeoutError()
        if self.mode == "loop" or (self.mode == "write" and number % 2 == 1):
            name = "list_files" if self.mode == "loop" else "write_file"
            args = (
                {"kind": "all", "offset": 0}
                if name == "list_files"
                else {"name": "report.md", "content": "# 报告\n仅模拟内容。"}
            )
            yield {
                "type": "tool_delta",
                "index": 0,
                "id": f"call-{number}",
                "name": name,
                "arguments": json.dumps(args, ensure_ascii=False),
            }
            yield {"type": "finish", "reason": "tool_calls"}
        else:
            yield {"type": "text", "text": "你好，离线测试回答。"}
            yield {"type": "finish", "reason": "stop"}
        yield {
            "type": "usage",
            "prompt_tokens": 10,
            "completion_tokens": 5,
            "total_tokens": 15,
        }


def make_service(root, *, mode="text"):
    root = Path(root).resolve()
    caps = root / "capabilities.json"
    caps.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "models": {"test-chat": {"chat": True, "tools": True}},
            }
        )
    )
    settings = Settings(
        data_dir=str(root),
        instance_id="fixture",
        enabled=True,
        capabilities_file=str(caps),
    )
    tokens, stream = FakeTokens(), FakeStream(mode)
    service = ChatService(settings, tokens, FakeVerifier(), stream_chat=stream)
    return service, tokens, stream


def access(uid=12, role=1):
    return RequestAccess(
        Principal(
            uid,
            username=f"user-{uid}",
            role=role,
            session_id=f"session-{uid}",
            expires_at=4102444800,
        ),
        "synthetic-session-only",
    )


def create(service, actor, *, title="新聊天"):
    cid = str(uuid4())
    service.create_conversation(
        actor.principal, {"conversation_id": cid, "title": title}
    )
    return cid


def body(service, actor, cid, **changes):
    return {
        "client_request_id": str(uuid4()),
        "expected_revision": service.get_conversation(actor.principal, cid)["revision"],
        "token_id": 7,
        "model": "test-chat",
        "capability_revision": service.models(actor, 7)["models"][0][
            "capability_revision"
        ],
        "context_revision": service.user_context.read_context(actor.principal)[
            "context_revision"
        ],
        "skill_ids": [],
        "mode": "chat",
        "text": "你好",
        "attachment_ids": [],
        **changes,
    }
