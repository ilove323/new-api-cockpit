"""v2 protocol/candidate integration through owner storage, real local HTTP only."""

import json
from pathlib import Path
import tempfile
import unittest

from chat_fixtures import access, body, create, make_service
from new_api_cockpit.features.chat import schemas
from new_api_cockpit.features.chat.capabilities import Capabilities
from new_api_cockpit.features.chat.errors import ChatError
from new_api_cockpit.features.chat.recovery import request_digest
from test_chat_contracts import request_body
from test_chat_protocols_v2 import CALL, REASONING, anthropic, responses, MESSAGE
from test_chat_transport import frame, upstream


class CapabilitiesV2Test(unittest.TestCase):
    def test_catalog_protocols_are_metadata_not_model_name(self):
        entries = Capabilities().catalog(
            [
                {"id": "gpt-suggestive", "protocols": []},
                {"id": "opaque-1", "protocols": ["responses"]},
                {"id": "opaque-2", "protocols": ["anthropic"]},
                {"id": "opaque-3", "protocols": ["chat", "responses"]},
            ]
        )
        self.assertFalse(entries[0]["chat_enabled"])
        self.assertEqual(
            [e["protocol"] for e in entries[1:]],
            ["responses", "anthropic", "responses"],
        )
        self.assertTrue(all(not e["tools_enabled"] for e in entries))

    def test_explicit_extended_protocol_limits_reasoning_and_native_compact(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "caps.json"
            policy = {
                "protocol": "responses",
                "tools": True,
                "max_input_bytes": 1048576,
                "max_output_tokens": 8192,
                "reasoning": {"effort": "high", "summary": "auto"},
                "compaction": "native",
            }
            value = {"schema_version": 2, "models": {"opaque": policy}}
            path.write_text(json.dumps(value))
            caps = Capabilities(str(path))
            row = caps.catalog([{"id": "opaque", "protocols": []}])[0]
            self.assertTrue(row["chat_enabled"])
            self.assertEqual(row["policy"]["max_input_bytes"], 1048576)
            self.assertEqual(row["compaction"], "native")
            for updates in (
                {"protocol": "anthropic"},
                {"stream_usage": True},
                {"max_output_tokens": 32769},
                {"max_input_bytes": 2097153},
                {"reasoning": {"summary": "hidden"}},
                {"protocol": "chat_completions", "compaction": "native"},
            ):
                bad = {**policy, **updates}
                path.write_text(
                    json.dumps({"schema_version": 2, "models": {"opaque": bad}})
                )
                with self.subTest(updates=updates), self.assertRaises(ChatError):
                    caps.load()

    def test_options_strict_and_idempotence_includes_all_nondefault_options(self):
        old = request_body()
        default = {
            **old,
            "candidate_count": 1,
            "auto_memory": False,
            "web_enabled": False,
            "context_strategy": "recent",
        }
        self.assertEqual(
            request_digest(old), request_digest(schemas.parse_run(default))
        )
        for option in (
            {"candidate_count": 2},
            {"auto_memory": True},
            {"context_strategy": "adaptive"},
            {"mode": "files", "web_enabled": True},
        ):
            self.assertNotEqual(
                request_digest(old),
                request_digest(schemas.parse_run({**old, **option})),
            )
        for option in (
            {"candidate_count": True},
            {"candidate_count": 5},
            {"candidate_count": 2, "mode": "files"},
            {"web_enabled": True},
            {"auto_memory": 1},
            {"context_strategy": "infinite"},
        ):
            with self.subTest(option=option), self.assertRaises(ChatError):
                schemas.parse_run({**old, **option})


class RuntimeV2Test(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.service, self.tokens, self.fake = make_service(self.tmp.name)
        self.actor = access()
        self.cid = create(self.service, self.actor)

    def tearDown(self):
        self.service.shutdown()
        self.service.store.close()
        self.tmp.cleanup()

    def configure(self, protocol):
        self.service.runner.stream_chat = None
        self.tokens.list_models = lambda *_: [
            {
                "id": "test-chat",
                "protocols": [{"chat_completions": "chat"}.get(protocol, protocol)],
            }
        ]
        Path(self.tmp.name, "capabilities.json").write_text(
            json.dumps(
                {
                    "schema_version": 2,
                    "models": {
                        "test-chat": {
                            "protocol": protocol,
                            "chat": True,
                            "tools": True,
                            "stream_usage": protocol == "chat_completions",
                        }
                    },
                }
            )
        )

    def turn(self, **changes):
        request = body(self.service, self.actor, self.cid, **changes)
        active = self.service.accept_run(self.actor, self.cid, request)
        stream = "".join(self.service.stream(active))
        state = self.service.get_run(self.actor.principal, self.cid, active.run_id)
        return active, state, stream, request

    def test_three_native_protocols_execute_tool_preserving_reasoning_state(self):
        for protocol in ("chat_completions", "responses", "anthropic"):
            with self.subTest(protocol=protocol):
                self.cid = create(self.service, self.actor)
                self.configure(protocol)
                call = {**CALL, "arguments": '{"kind":"all","offset":0}'}
                if protocol == "responses":
                    sequence = [responses([REASONING, call]), responses([MESSAGE])]
                elif protocol == "anthropic":
                    sequence = [
                        anthropic(
                            [
                                {
                                    "type": "thinking",
                                    "thinking": "实际返回的公开思考",
                                    "signature": "signature-unchanged",
                                },
                                {
                                    "type": "redacted_thinking",
                                    "data": "opaque-unchanged",
                                },
                                {
                                    "type": "tool_use",
                                    "id": "call_1",
                                    "name": "list_files",
                                    "input": {"kind": "all", "offset": 0},
                                },
                            ],
                            reason="tool_use",
                        ),
                        anthropic(),
                    ]
                else:
                    sequence = [
                        frame({"reasoning_content": "原始公开内容"})
                        + frame(
                            {
                                "tool_calls": [
                                    {
                                        "index": 0,
                                        "type": "function",
                                        "id": "call_1",
                                        "function": {
                                            "name": "list_files",
                                            "arguments": '{"kind":"all","offset":0}',
                                        },
                                    }
                                ]
                            }
                        )
                        + frame(finish="tool_calls")
                        + b"data: [DONE]\n\n",
                        frame({"content": "回答"})
                        + frame(finish="stop")
                        + b"data: [DONE]\n\n",
                    ]

                def script(handler):
                    raw = sequence[len(handler.server.requests) - 1]
                    handler.wfile.write(
                        f"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nContent-Length: {len(raw)}\r\n\r\n".encode()
                        + raw
                    )
                    handler.wfile.flush()

                with upstream(script=script) as server:
                    active, state, stream, _ = self.turn(mode="files")
                self.assertEqual(state["status"], "completed", stream)
                self.assertEqual(state["model_requests"], 2)
                self.assertEqual(state["main_model_requests"], 2)
                self.assertEqual(state["tool_calls"], 1)
                self.assertEqual(len(server.requests), 2)
                second = json.loads(server.requests[1][2])
                if protocol == "responses":
                    self.assertIn(REASONING, second["input"])
                    self.assertIn(call, second["input"])
                    self.assertEqual(
                        second["input"][-1]["type"], "function_call_output"
                    )
                elif protocol == "anthropic":
                    blocks = next(
                        m["content"]
                        for m in second["messages"]
                        if m["role"] == "assistant"
                    )
                    self.assertEqual(blocks[0]["signature"], "signature-unchanged")
                    self.assertEqual(blocks[1]["data"], "opaque-unchanged")
                    self.assertEqual(
                        second["messages"][-1]["content"][0]["type"], "tool_result"
                    )
                else:
                    assistant = next(
                        m for m in second["messages"] if m["role"] == "assistant"
                    )
                    self.assertEqual(assistant["reasoning_content"], "原始公开内容")
                events = self.service._history(
                    self.service._scope(self.actor.principal, self.cid)
                )
                self.assertTrue(any(e["type"] == "assistant.reasoning" for e in events))
                self.assertTrue(any(e["type"] == "assistant.protocol" for e in events))
                reopened = self.service.store.get_run(
                    self.service._scope(self.actor.principal, self.cid), active.run_id
                )
                self.assertEqual(reopened["status"], "completed")

    def test_chat_candidates_one_call_and_explicit_owner_selection_persisted(self):
        self.configure("chat_completions")
        raw = (
            frame(
                choices=[
                    {
                        "index": i,
                        "delta": {
                            "reasoning_content": "推理" + str(i),
                            "content": "候选" + str(i),
                        },
                        "finish_reason": None,
                    }
                    for i in range(3)
                ]
            )
            + frame(
                choices=[
                    {"index": i, "delta": {}, "finish_reason": "stop"} for i in range(3)
                ]
            )
            + frame(
                choices=[],
                usage={"prompt_tokens": 4, "completion_tokens": 6, "total_tokens": 10},
            )
            + b"data: [DONE]\n\n"
        )
        with upstream(body=raw) as server:
            active, state, stream, request = self.turn(candidate_count=3)
        self.assertEqual(state["status"], "completed", stream)
        self.assertEqual(state["model_requests"], 1)
        self.assertEqual(state["candidate_count"], 3)
        self.assertIsNone(state["selected_candidate"])
        self.assertEqual(len(server.requests), 1)
        scope = self.service._scope(self.actor.principal, self.cid)
        events = self.service._history(scope)
        self.assertEqual(
            [
                e["data"]["candidate_index"]
                for e in events
                if e["type"] == "assistant.message"
            ],
            [0, 1, 2],
        )
        with self.assertRaises(ChatError):
            self.service.accept_run(
                self.actor, self.cid, {**request, "candidate_count": 2}
            )
        with self.assertRaises(ChatError) as pending:
            self.service.accept_run(
                self.actor, self.cid, body(self.service, self.actor, self.cid)
            )
        self.assertEqual(pending.exception.code, "CHAT_CANDIDATE_REQUIRED")
        revision = self.service.get_conversation(self.actor.principal, self.cid)[
            "revision"
        ]
        with self.assertRaises(ChatError):
            self.service.select_candidate(
                access(13).principal,
                self.cid,
                active.run_id,
                {"expected_revision": revision, "candidate_index": 1},
            )
        result = self.service.select_candidate(
            self.actor.principal,
            self.cid,
            active.run_id,
            {"expected_revision": revision, "candidate_index": 1},
        )
        self.assertEqual(result["run"]["selected_candidate"], 1)
        self.assertEqual(self.service._history(scope)[-1]["type"], "candidate.selected")
        with self.assertRaises(ChatError):
            self.service.select_candidate(
                self.actor.principal,
                self.cid,
                active.run_id,
                {"expected_revision": revision, "candidate_index": 0},
            )
        self.assertNotIn(
            KEY_MARKER := "sk-" + "testfixtureonly" * 4,
            json.dumps(events, ensure_ascii=False),
        )
        self.assertTrue(KEY_MARKER)

    def test_responses_multiple_candidates_separate_paid_request_count(self):
        self.configure("responses")
        with upstream(body=responses()) as server:
            active, state, stream, request = self.turn(candidate_count=4)
        self.assertEqual(state["status"], "completed", stream)
        self.assertEqual(state["model_requests"], 4)
        self.assertEqual(state["usage"]["total_tokens"], 80)
        self.assertTrue(state["usage_complete"])
        self.assertEqual(len(server.requests), 4)
        self.assertTrue(
            self.service.accept_run(self.actor, self.cid, request)["replayed"]
        )
        self.assertEqual(len(server.requests), 4)
        events = self.service._history(
            self.service._scope(self.actor.principal, self.cid)
        )
        self.assertEqual(
            [
                e["data"]["candidate_index"]
                for e in events
                if e["type"] == "assistant.message"
            ],
            [0, 1, 2, 3],
        )

    def test_native_state_protocol_mismatch_fails_without_tool_execution(self):
        self.configure("responses")

        def wrong(*args):
            yield {
                "type": "replay",
                "protocol": "anthropic",
                "value": [{"type": "text", "text": "bad"}],
            }
            yield {"type": "finish", "reason": "stop"}

        self.service.runner.stream_chat = wrong
        _, state, _, _ = self.turn(mode="files")
        self.assertEqual(state["status"], "failed")
        self.assertEqual(state["model_requests"], 1)
        self.assertEqual(state["tool_calls"], 0)


if __name__ == "__main__":
    unittest.main()
