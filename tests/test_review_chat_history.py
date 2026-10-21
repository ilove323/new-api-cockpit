"""Independent history regressions: synthetic files/adapters, no network or secrets.

Run from the repository root:
  .venv/bin/python -m unittest discover -s tests -p test_review_chat_history.py -v
These assert the required behavior, rather than asserting existing defects.
"""

import hashlib
from unittest.mock import patch
from uuid import uuid4

from chat_fixtures import FakeStream, FakeTokens, FakeVerifier, access, body
from test_chat_storage import StorageFixture
from new_api_cockpit.features.chat import config, context
from new_api_cockpit.features.chat.fs import canonical_json, utcnow
from new_api_cockpit.features.chat.recovery import scan
from new_api_cockpit.features.chat.service import ChatService


class ChatHistoryReviewTest(StorageFixture):
    def test_non_object_derived_index_does_not_hide_valid_chats(self):
        """A malformed *derived* cache must not become the membership authority."""
        path = f"site-a/users/{self.a.user_id}/index.json"
        for cache in (None, [], 1, "broken", True):
            with self.subTest(cache=cache):
                self.store.fs.atomic_write(path, canonical_json(cache))
                listing = self.store.list_conversations(self.a)
                self.assertEqual(
                    [row["id"] for row in listing["conversations"]], [self.cid]
                )
                self.assertEqual(listing["total"], 1)
                self.assertEqual(self.store.fs.read_json(path)["v"], 1)

    def test_201_successful_short_rounds_do_not_prevent_the_next_send(self):
        """Seed real valid committed JSONL, not 201 expensive synthetic HTTP runs."""
        stream = FakeStream()
        service = ChatService(
            self.settings,
            FakeTokens(),
            FakeVerifier(),
            store=self.store,
            stream_chat=stream,
        )
        self.addCleanup(service.shutdown)
        actor = access(self.a.user_id)
        request = body(service, actor, self.cid)
        events = self.store.read_events(self.scope)["events"]

        def add(kind, rid, data):
            events.append(
                {
                    "v": 1,
                    "seq": len(events) + 1,
                    "type": kind,
                    "conversation_id": self.cid,
                    "run_id": rid,
                    "at": utcnow(),
                    "data": data,
                }
            )

        for _ in range(201):
            rid, mid = str(uuid4()), str(uuid4())
            snapshot = canonical_json(self.snapshot(rid)) + b"\n"
            self.store.fs.atomic_write(
                f"{self.scope.path}/contexts/{rid}.json", snapshot, exclusive=True
            )
            add(
                "run.accepted",
                rid,
                {
                    "client_request_id": str(uuid4()),
                    "message_id": str(uuid4()),
                    "text": "q",
                    "attachment_ids": [],
                    "token_id": 7,
                    "model": "test-chat",
                    "mode": "chat",
                    "capability_revision": request["capability_revision"],
                    "context_snapshot": {
                        "file": f"contexts/{rid}.json",
                        "sha256": hashlib.sha256(snapshot).hexdigest(),
                        "context_revision": 0,
                        "skill_ids": [],
                    },
                    "context_from_run_ids": [],
                },
            )
            add(
                "run.dispatching",
                rid,
                {
                    "request_index": 1,
                    "assistant_message_id": mid,
                },
            )
            add(
                "assistant.message",
                rid,
                {
                    "message_id": mid,
                    "text": "a",
                    "tool_calls": [],
                    "finish_reason": "stop",
                    "complete": True,
                },
            )
            add(
                "run.terminal",
                rid,
                {
                    "status": "completed",
                    "reason": "completed",
                    "error_code": None,
                    "uncertain": False,
                    "model_requests": 1,
                    "tool_calls": 0,
                    "usage": None,
                    "usage_complete": False,
                },
            )

        raw = b"".join(canonical_json(event) + b"\n" for event in events)
        state = scan(raw, self.scope)
        self.assertEqual(state.storage_state, "ok")
        self.assertEqual(state.seq, 805)
        # Publish a consistent crash-free fixture, including every snapshot.
        with self.store.lease(self.scope), self.store.storage_lock():
            self.store.fs.atomic_write(f"{self.scope.path}/events.jsonl", raw)
            self.store.fs.write_json(
                f"{self.scope.path}/commit.json",
                {
                    "v": 1,
                    "seq": state.seq,
                    "byte_offset": len(raw),
                },
            )
            self.store._write_projections(self.scope, state)

        request = body(service, actor, self.cid)
        snapshot = service.user_context.freeze_context(
            actor.principal, self.cid, str(uuid4()), request["context_revision"], []
        )
        messages, included = context.build_messages(
            service._history(self.scope), snapshot, request, config.CONTEXT_BYTES
        )
        self.assertEqual(len(included), 201)
        with patch.object(config, "CONTEXT_HISTORY_RUNS", 2):
            _, capped = context.build_messages(
                service._history(self.scope), snapshot, request, config.CONTEXT_BYTES
            )
        self.assertEqual(capped, included[-2:])
        self.assertLess(len(canonical_json(messages)), config.CONTEXT_BYTES)
        self.assertEqual(len(service._history(self.scope)), 805)
        self.assertEqual(stream.calls, [])
        with patch.object(config, "CONVERSATIONS_PER_USER", 1):
            accepted = service.accept_run(actor, self.cid, request)
        list(service.stream(accepted))
        self.assertEqual(
            service.get_run(actor.principal, self.cid, accepted.run_id)["status"],
            "completed",
        )
        self.assertEqual(len(stream.calls), 1)
        full_history = service._history(self.scope)
        self.assertEqual(full_history[: len(events)], events)
        self.assertEqual(
            sum(event["type"] == "assistant.message" for event in full_history), 202
        )
