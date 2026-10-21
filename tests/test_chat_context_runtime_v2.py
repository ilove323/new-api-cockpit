"""Local runtime compaction, bounded auxiliary billing and canonical-history checks."""

import json
from pathlib import Path
import tempfile
import unittest

from chat_fixtures import access, body, create, make_service
from new_api_cockpit.features.chat import protocols
from test_chat_protocols_v2 import WINDOW
from test_chat_transport import upstream as model_upstream
from test_chat_web_tools_v2 import upstream as web_upstream, URL

SUMMARY = {
    "goal": ["继续当前用户任务"],
    "constraints": ["无新增权限"],
    "decisions": [],
    "progress": ["旧原文仍在当前聊天"],
    "pending": ["继续处理"],
    "refs": [],
}


class ContextRuntimeV2Test(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.service, self.tokens, self.original = make_service(self.tmp.name)
        self.actor, self.calls, self.main_count = access(), [], 0
        self.cid = create(self.service, self.actor)
        self.mode = "normal"
        self.service.runner.stream_chat = self.stream
        self.configure()

    def tearDown(self):
        self.service.shutdown()
        self.service.store.close()
        self.tmp.cleanup()

    def configure(
        self, protocol="chat_completions", compaction="summary", budget=60000
    ):
        self.protocol = protocol
        label = "chat" if protocol == "chat_completions" else protocol
        self.tokens.list_models = lambda *_: [{"id": "test-chat", "protocols": [label]}]
        Path(self.tmp.name, "capabilities.json").write_text(
            json.dumps(
                {
                    "schema_version": 2,
                    "models": {
                        "test-chat": {
                            "protocol": protocol,
                            "tools": True,
                            "max_input_bytes": budget,
                            "compaction": compaction,
                        }
                    },
                }
            )
        )

    def scope(self):
        return self.service._scope(self.actor.principal, self.cid)

    def history(self):
        return self.service._history(self.scope())

    def stream(self, key, payload, deadline, abort):
        self.calls.append(json.loads(json.dumps(payload, ensure_ascii=False)))
        if payload["messages"][0]["content"].startswith("为当前聊天生成可读工作检查点"):
            text = (
                "invalid-summary"
                if self.mode == "invalid_summary"
                else json.dumps(SUMMARY, ensure_ascii=False)
            )
            yield {"type": "text", "text": text}
        else:
            self.main_count += 1
            if self.mode == "read_files" and self.main_count <= 2:
                yield {
                    "type": "tool_delta",
                    "index": 0,
                    "id": "read_" + str(self.main_count),
                    "name": "read_file",
                    "arguments": json.dumps(
                        {
                            "file_id": self.fid,
                            "start_line": 1 if self.main_count == 1 else 201,
                            "max_lines": 200,
                        }
                    ),
                }
                yield {"type": "finish", "reason": "tool_calls"}
                yield {
                    "type": "usage",
                    "prompt_tokens": 10,
                    "completion_tokens": 5,
                    "total_tokens": 15,
                }
                return
            elif self.mode == "download" and self.main_count == 1:
                yield {
                    "type": "tool_delta",
                    "index": 0,
                    "id": "download_1",
                    "name": "download_url",
                    "arguments": json.dumps({"url": URL, "name": "source.md"}),
                }
                yield {"type": "finish", "reason": "tool_calls"}
                yield {
                    "type": "usage",
                    "prompt_tokens": 10,
                    "completion_tokens": 5,
                    "total_tokens": 15,
                }
                return
            else:
                text = f"answer-{self.main_count}::" + (
                    "a" * 10000
                    if self.main_count <= 3 and self.mode == "normal"
                    else "简短结果"
                )
                yield {"type": "text", "text": text}
        yield {"type": "finish", "reason": "stop"}
        yield {
            "type": "usage",
            "prompt_tokens": 10,
            "completion_tokens": 5,
            "total_tokens": 15,
        }

    def turn(self, text="continue", **changes):
        request = body(
            self.service,
            self.actor,
            self.cid,
            text=text,
            context_strategy="adaptive",
            **changes,
        )
        active = self.service.accept_run(self.actor, self.cid, request)
        output = "".join(self.service.stream(active))
        state = self.service.get_run(self.actor.principal, self.cid, active.run_id)
        return active, state, output, request

    def seed(self):
        for index in range(3):
            _, state, output, _ = self.turn(f"user-{index}::" + "u" * 6000)
            self.assertEqual(state["status"], "completed", output)
            self.assertEqual(state["aux_model_requests"], 0)

    def test_threshold_summary_preserves_archive_persists_and_reuses_without_paid_read(
        self,
    ):
        self.seed()
        before = self.history()
        active, state, output, _ = self.turn("fourth-user::" + "u" * 6000)
        self.assertEqual(state["status"], "completed", output)
        self.assertEqual(
            (state["model_requests"], state["aux_model_requests"]), (2, 1), output
        )
        self.assertEqual(state["usage"]["total_tokens"], 30)
        events = self.history()
        self.assertEqual(events[: len(before)], before)
        compacted = next(
            e
            for e in events
            if e["run_id"] == active.run_id and e["type"] == "context.compacted"
        )
        self.assertEqual(compacted["data"]["mode"], "summary")
        self.assertTrue(compacted["data"]["covered_run_ids"])
        checkpoint = self.service.store.read_working_context(
            self.scope(), compacted["data"]["checkpoint"]
        )
        self.assertEqual(checkpoint["summary"], SUMMARY)
        final_prompt = json.dumps(self.calls[-1], ensure_ascii=False)
        self.assertIn("模型生成工作摘要", final_prompt)
        self.assertIn("user-1::", final_prompt)
        self.assertIn("user-2::", final_prompt)
        self.assertIn("fourth-user::", final_prompt)
        self.assertNotIn("user-0::", final_prompt)
        calls = len(self.calls)
        self.service.events(self.actor.principal, self.cid, {})
        self.service.get_run(self.actor.principal, self.cid, active.run_id)
        self.assertEqual(len(self.calls), calls)
        follow, later, later_output, _ = self.turn("small next question")
        self.assertEqual(later["status"], "completed", later_output)
        self.assertEqual(later["aux_model_requests"], 0, later_output)
        self.assertTrue(
            any(
                e["type"] == "context.selected" and e["run_id"] == follow.run_id
                for e in self.history()
            )
        )
        self.assertEqual(len(self.calls), calls + 1)

    def test_native_compaction_entire_returned_window_reaches_next_main_request(self):
        self.configure(protocol="responses", compaction="native")
        self.seed()
        response = {
            "object": "response.compaction",
            "output": WINDOW,
            "usage": {"input_tokens": 19, "output_tokens": 4, "total_tokens": 23},
        }
        with model_upstream(
            body=json.dumps(response, ensure_ascii=False).encode(),
            content_type="application/json",
        ) as server:
            active, state, output, _ = self.turn("fourth-user::" + "u" * 6000)
        self.assertEqual(state["status"], "completed", output)
        self.assertEqual(len(server.requests), 1)
        self.assertEqual(server.requests[0][0], "POST /v1/responses/compact HTTP/1.1")
        self.assertEqual(
            (state["main_model_requests"], state["aux_model_requests"]), (1, 1)
        )
        self.assertEqual(state["usage"]["total_tokens"], 38)
        self.assertEqual(self.calls[-1]["_window"], WINDOW)
        wire = protocols.wire_body("responses", self.calls[-1])
        self.assertEqual(wire["input"][1:3], WINDOW)
        saved = next(
            e
            for e in self.history()
            if e["run_id"] == active.run_id and e["type"] == "context.compacted"
        )
        checkpoint = self.service.store.read_working_context(
            self.scope(), saved["data"]["checkpoint"]
        )
        self.assertEqual(checkpoint["window"], WINDOW)

    def test_failed_native_compaction_one_request_no_summary_fallback(self):
        self.configure(protocol="responses", compaction="native")
        self.seed()
        before = self.history()
        with model_upstream(status=503, body=b"private-upstream-error") as server:
            active, state, output, request = self.turn("fourth-user::" + "u" * 6000)
        self.assertEqual(state["status"], "failed", output)
        self.assertEqual(
            (
                state["model_requests"],
                state["main_model_requests"],
                state["aux_model_requests"],
            ),
            (1, 0, 1),
        )
        self.assertEqual(len(server.requests), 1)
        self.assertEqual(self.main_count, 3)
        self.assertEqual(self.history()[: len(before)], before)
        self.assertNotIn("private-upstream-error", output)
        self.assertTrue(
            self.service.accept_run(self.actor, self.cid, request)["replayed"]
        )
        self.assertEqual(len(server.requests), 1)

    def test_invalid_summary_stops_after_one_aux_call_without_answer_or_fallback(self):
        self.seed()
        self.mode = "invalid_summary"
        before = len(self.calls)
        _, state, output, _ = self.turn("fourth-user::" + "u" * 6000)
        self.assertEqual(state["status"], "failed", output)
        self.assertEqual(state["model_requests"], 1)
        self.assertEqual(state["aux_model_requests"], 1)
        self.assertEqual(len(self.calls), before + 1)
        self.assertEqual(self.main_count, 3)
        self.assertEqual(state["error_code"], "CHAT_CONTEXT_COMPACTION_INVALID")

    def test_active_agent_growth_compresses_old_whole_tool_pair(self):
        self.configure(budget=45000)
        self.mode = "read_files"
        entry, _ = self.service.upload(
            self.actor.principal,
            self.cid,
            __import__("uuid").uuid4().__str__(),
            "source.txt",
            (("x" * 89 + "\n") * 400).encode(),
        )
        self.fid = entry["id"]
        active, state, output, _ = self.turn(
            "read all the source", mode="files", attachment_ids=[self.fid]
        )
        self.assertEqual(state["status"], "completed", output)
        self.assertEqual(state["main_model_requests"], 3)
        self.assertEqual(state["aux_model_requests"], 1, output)
        self.assertEqual(state["tool_calls"], 2)
        self.assertEqual(state["usage"]["total_tokens"], 60)
        compacted = next(
            e
            for e in self.history()
            if e["run_id"] == active.run_id and e["type"] == "context.compacted"
        )
        checkpoint = self.service.store.read_working_context(
            self.scope(), compacted["data"]["checkpoint"]
        )
        self.assertTrue(checkpoint["active_only"])
        final = self.calls[-1]["messages"]
        self.assertIn("模型生成工作摘要", json.dumps(final, ensure_ascii=False))
        self.assertEqual(
            next(m for m in final if m.get("tool_call_id"))["tool_call_id"], "read_2"
        )
        self.assertEqual(len(self.calls[-1]["tools"]), 5)

    def test_web_agent_download_single_outer_tool_and_followup_retains_whole_pair(self):
        self.mode = "download"
        with web_upstream(body=b"# downloaded owner text") as server:
            active, state, output, _ = self.turn(
                "download this URL", mode="files", web_enabled=True
            )
        self.assertEqual(state["status"], "completed", output)
        self.assertEqual(state["tool_calls"], 1)
        self.assertEqual(state["main_model_requests"], 2)
        self.assertEqual(len(server.requests), 1)
        self.assertEqual(len(self.calls[0]["tools"]), 8)
        events = self.history()
        calls = [
            e["data"]
            for e in events
            if e["run_id"] == active.run_id and e["type"] == "tool.call"
        ]
        self.assertEqual([e["name"] for e in calls], ["download_url"])
        self.assertNotIn("content", calls[0]["arguments"])
        files = self.service.files(self.actor.principal, self.cid)["files"]
        self.assertEqual(len(files), 1)
        data, info = self.service.download(
            self.actor.principal, self.cid, files[0]["id"]
        )
        self.assertEqual(data, b"# downloaded owner text")
        self.assertEqual(info["name"], "source.md")
        self.mode = "short"
        _, later, later_output, _ = self.turn("what did you download?")
        self.assertEqual(later["status"], "completed", later_output)
        messages = self.calls[-1]["messages"]
        self.assertTrue(
            any(
                m["role"] == "tool" and m["tool_call_id"] == "download_1"
                for m in messages
            )
        )
        self.assertTrue(
            any(
                m.get("tool_calls", [{}])[0].get("id") == "download_1"
                for m in messages
                if m.get("tool_calls")
            )
        )
