"""Opt-in Memory calls have shared run accounting, CAS, audit and no hidden retries."""

import json
from pathlib import Path
import tempfile
import unittest

from chat_fixtures import access, body, create, make_service
from new_api_cockpit.features.chat.errors import ChatError

FACT = "我长期偏好使用中文沟通。"


class MemoryRuntimeV2Test(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.service, self.tokens, self.original = make_service(self.tmp.name)
        self.actor, self.calls = access(), []
        self.cid = create(self.service, self.actor)
        self.mode = "valid"
        self.service.runner.stream_chat = self.stream

    def tearDown(self):
        self.service.shutdown()
        self.service.store.close()
        self.tmp.cleanup()

    def view(self):
        return self.service.user_context.read_context(self.actor.principal)

    def memory(self):
        return next(p for p in self.view()["profiles"] if p["name"] == "MEMORY.md")

    def write_memory(self, text):
        current = self.view()
        self.service.user_context.update_profile(
            self.actor.principal,
            "MEMORY.md",
            current["context_revision"],
            self.memory()["revision"],
            text,
        )

    def stream(self, key, payload, deadline, abort):
        self.calls.append(payload)
        if len(self.calls) % 2 == 1:
            yield {"type": "text", "text": "正常回答已完成。"}
        else:
            if self.mode == "edit":
                self.write_memory("并发手工编辑，必须保留。")
            elif self.mode == "cancel":
                abort.abort()
                abort.check()
            source = next(
                e["data"]
                for e in reversed(
                    self.service._history(
                        self.service._scope(self.actor.principal, self.cid)
                    )
                )
                if e["type"] == "run.accepted"
            )
            value = {
                "v": 1,
                "operations": [
                    {
                        "op": "add",
                        "text": FACT,
                        "sources": [
                            {"message_id": source["message_id"], "excerpt": FACT}
                        ],
                    }
                ],
            }
            yield {
                "type": "text",
                "text": "not JSON"
                if self.mode == "invalid"
                else json.dumps(value, ensure_ascii=False),
            }
        yield {"type": "finish", "reason": "stop"}
        yield {
            "type": "usage",
            "prompt_tokens": 10,
            "completion_tokens": 5,
            "total_tokens": 15,
        }

    def turn(self, **changes):
        request = body(self.service, self.actor, self.cid, text=FACT, **changes)
        active = self.service.accept_run(self.actor, self.cid, request)
        output = "".join(self.service.stream(active))
        return (
            active,
            self.service.get_run(self.actor.principal, self.cid, active.run_id),
            output,
            request,
        )

    def test_default_does_not_extract_or_write_memory(self):
        _, state, _, _ = self.turn()
        self.assertEqual(state["status"], "completed")
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(state["aux_model_requests"], 0)
        self.assertEqual(self.memory()["content"], "")

    def test_acknowledgement_has_no_auxiliary_call_or_memory_write(self):
        request = body(
            self.service, self.actor, self.cid, text="好的，谢谢！", auto_memory=True
        )
        # This is not in the exact acknowledgement set; no broad substring
        # matching is allowed to suppress user facts.
        from new_api_cockpit.features.chat.auto_memory import needs_memory_check

        self.assertTrue(needs_memory_check(request["text"]))
        request["text"] = "继续。"
        active = self.service.accept_run(self.actor, self.cid, request)
        output = "".join(self.service.stream(active))
        state = self.service.get_run(self.actor.principal, self.cid, active.run_id)
        self.assertEqual(state["status"], "completed", output)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(state["aux_model_requests"], 0)
        self.assertEqual(self.memory()["content"], "")
        self.assertNotIn("CHAT_MEMORY_UNCHANGED", output)

    def test_opt_in_one_aux_call_usage_intent_audit_and_owner_undo(self):
        manual = "# 手工记忆\n保留原始格式。\n"
        self.write_memory(manual)
        active, state, output, request = self.turn(auto_memory=True)
        self.assertEqual(state["status"], "completed", output)
        self.assertTrue(state.get("memory_updated"), output)
        self.assertEqual(
            (
                state["model_requests"],
                state["main_model_requests"],
                state["aux_model_requests"],
            ),
            (2, 1, 1),
        )
        self.assertEqual(state["usage"]["total_tokens"], 30)
        self.assertTrue(state["usage_complete"])
        content = self.memory()["content"]
        self.assertTrue(content.startswith(manual))
        self.assertIn(FACT, content)
        scope = self.service._scope(self.actor.principal, self.cid)
        events = self.service._history(scope)
        intent = next(e for e in events if e["type"] == "memory.proposed")
        audit = next(e for e in events if e["type"] == "memory.updated")
        self.assertLess(intent["seq"], audit["seq"])
        self.assertEqual(
            intent["data"]["intent"]["after_sha256"],
            audit["data"]["audit"]["after"]["sha256"],
        )
        purposes = [
            e["data"].get("purpose", "main")
            for e in events
            if e["type"] == "run.dispatching"
        ]
        self.assertEqual(purposes, ["main", "memory"])
        self.assertTrue(
            self.service.accept_run(self.actor, self.cid, request)["replayed"]
        )
        self.assertEqual(len(self.calls), 2)
        revision = self.service.get_conversation(self.actor.principal, self.cid)[
            "revision"
        ]
        payload = {
            "context_revision": self.view()["context_revision"],
            "expected_revision": revision,
        }
        with self.assertRaises(ChatError):
            self.service.undo_memory(access(99, 100), self.cid, active.run_id, payload)
        result = self.service.undo_memory(self.actor, self.cid, active.run_id, payload)
        self.assertEqual(
            next(p for p in result["context"]["profiles"] if p["name"] == "MEMORY.md")[
                "content"
            ],
            manual,
        )
        self.assertTrue(
            self.service.get_run(self.actor.principal, self.cid, active.run_id)[
                "memory_reverted"
            ]
        )
        self.assertEqual(len(self.calls), 2)
        with self.assertRaises(ChatError):
            self.service.undo_memory(
                self.actor,
                self.cid,
                active.run_id,
                {
                    "context_revision": self.view()["context_revision"],
                    "expected_revision": result["conversation"]["revision"],
                },
            )
        for path in Path(self.tmp.name).rglob("*"):
            if path.is_file():
                self.assertNotIn(b"testfixtureonly" * 4, path.read_bytes())
                self.assertNotIn(b"synthetic-session-only", path.read_bytes())

    def test_parallel_manual_update_wins_without_retry_or_answer_loss(self):
        self.mode = "edit"
        _, state, output, _ = self.turn(auto_memory=True)
        self.assertEqual(state["status"], "completed", output)
        self.assertEqual(self.memory()["content"], "并发手工编辑，必须保留。")
        self.assertEqual(len(self.calls), 2)
        self.assertFalse(state.get("memory_updated", False))
        self.assertIn("run.notice", output)
        self.assertIn("memory.proposed", output)
        self.assertNotIn("event: memory.updated", output)

    def test_invalid_auxiliary_output_preserves_normal_answer_and_usage(self):
        self.mode = "invalid"
        _, state, output, _ = self.turn(auto_memory=True)
        self.assertEqual(state["status"], "completed", output)
        self.assertEqual(self.memory()["content"], "")
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(state["usage"]["total_tokens"], 30)
        self.assertIn("CHAT_MEMORY_PROPOSAL_INVALID", output)
        self.assertIn("正常回答已完成。", output)

    def test_cancel_during_aux_does_not_write_or_hide_partial_usage(self):
        self.mode = "cancel"
        _, state, output, _ = self.turn(auto_memory=True)
        self.assertEqual(state["status"], "cancelled", output)
        self.assertEqual(self.memory()["content"], "")
        self.assertEqual(state["model_requests"], 2)
        self.assertEqual(state["usage"]["total_tokens"], 15)
        self.assertFalse(state["usage_complete"])
        self.assertEqual(len(self.calls), 2)

    def test_later_manual_edit_blocks_undo(self):
        active, state, output, _ = self.turn(auto_memory=True)
        self.assertTrue(state.get("memory_updated"), output)
        self.write_memory("之后的手工修改")
        revision = self.service.get_conversation(self.actor.principal, self.cid)[
            "revision"
        ]
        with self.assertRaises(ChatError):
            self.service.undo_memory(
                self.actor,
                self.cid,
                active.run_id,
                {
                    "context_revision": self.view()["context_revision"],
                    "expected_revision": revision,
                },
            )
        self.assertEqual(self.memory()["content"], "之后的手工修改")
        self.assertEqual(len(self.calls), 2)
