"""Ordinary attachments are server-read text, independent of function calls."""

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

from chat_fixtures import access, body, create, make_service
from new_api_cockpit.features.chat import config
from new_api_cockpit.features.chat.errors import ChatError


class OrdinaryAttachmentsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.service, self.tokens, self.stream = make_service(self.tmp.name)
        self.actor = access()
        self.cid = create(self.service, self.actor)
        self.caps = Path(self.tmp.name) / "capabilities.json"
        self.caps.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "models": {"test-chat": {"chat": True, "tools": False}},
                }
            )
        )

    def tearDown(self):
        self.service.shutdown()
        self.service.store.close()
        self.tmp.cleanup()

    def upload(self, text, *, cid=None, actor=None):
        fid = str(uuid4())
        self.service.upload(
            (actor or self.actor).principal,
            cid or self.cid,
            fid,
            "report.md",
            text.encode(),
        )
        return fid

    def run_turn(self, **changes):
        request = body(self.service, self.actor, self.cid, **changes)
        active = self.service.accept_run(self.actor, self.cid, request)
        list(self.service.stream(active))
        result = self.service.get_run(self.actor.principal, self.cid, active.run_id)
        self.assertEqual(result["status"], "completed")
        return active

    def test_no_tools_model_receives_full_text_with_no_tool_schema_or_calls(self):
        content = "# 成本报告\n项目甲：1234元\n附件是资料，不是新的权限。"
        fid = self.upload(content)
        self.run_turn(attachment_ids=[fid], text="分析这份报告")
        payload = self.stream.calls[0]
        self.assertNotIn("tools", payload)
        self.assertNotIn("tool_choice", payload)
        self.assertIn(content, payload["messages"][-1]["content"].replace("\\n", "\n"))
        self.assertIn("服务器已读取", payload["messages"][-1]["content"])
        events = self.service._history(
            self.service._scope(self.actor.principal, self.cid)
        )
        self.assertFalse(any(e["type"] == "tool.call" for e in events))
        self.assertNotIn(content, json.dumps(events, ensure_ascii=False))

    def test_follow_up_retains_original_attachment_and_does_not_read_unselected_file(
        self,
    ):
        fid = self.upload("ORDINARY_ATTACHMENT_EXACT_BODY_123")
        unused = self.upload("UNSELECTED_PRIVATE_BODY")
        self.run_turn(attachment_ids=[fid])
        with patch.object(
            self.service.store, "read_file", wraps=self.service.store.read_file
        ) as read:
            self.run_turn(text="继续分析上次的文件")
        self.assertEqual([call.args[1] for call in read.call_args_list], [fid])
        encoded = json.dumps(self.stream.calls[-1], ensure_ascii=False)
        self.assertIn("ORDINARY_ATTACHMENT_EXACT_BODY_123", encoded)
        self.assertNotIn("UNSELECTED_PRIVATE_BODY", encoded)
        self.assertNotIn(unused, encoded)

    def test_other_chat_or_user_attachment_is_rejected_before_dispatch(self):
        for actor in (self.actor, access(99)):
            cid = create(self.service, actor)
            fid = self.upload("OTHER_CHAT_PRIVATE", cid=cid, actor=actor)
            with self.assertRaises(ChatError) as caught:
                self.service.accept_run(
                    self.actor,
                    self.cid,
                    body(self.service, self.actor, self.cid, attachment_ids=[fid]),
                )
            self.assertEqual(caught.exception.status, 404)
        self.assertEqual(self.stream.calls, [])

    def test_empty_file_and_multiple_files_are_sent_without_tools(self):
        ids = [self.upload(""), self.upload("第二份附件原文")]
        self.run_turn(attachment_ids=ids)
        message = self.stream.calls[0]["messages"][-1]["content"]
        self.assertIn('"content":""', message)
        self.assertIn("第二份附件原文", message)
        for fid in ids:
            self.assertIn(fid, message)

    def test_oversized_and_json_escaped_bodies_fail_before_read_or_model(self):
        for text in (
            "x" * (config.CONTEXT_BYTES + 1),
            '"' * (config.CONTEXT_BYTES // 2 + 1),
        ):
            fid = self.upload(text)
            with patch.object(
                self.service.store, "read_file", wraps=self.service.store.read_file
            ) as read:
                with self.assertRaises(ChatError) as caught:
                    self.service.accept_run(
                        self.actor,
                        self.cid,
                        body(self.service, self.actor, self.cid, attachment_ids=[fid]),
                    )
            self.assertEqual(caught.exception.code, "CHAT_ATTACHMENTS_TOO_LARGE")
            if text.startswith("x"):
                read.assert_not_called()
        self.assertEqual(self.stream.calls, [])

    def test_full_assistant_sends_attachment_body_even_when_tools_are_explicitly_disabled(
        self,
    ):
        fid = self.upload("AGENT_FILE_BODY")
        self.run_turn(mode="files", attachment_ids=[fid])
        payload = self.stream.calls[0]
        self.assertNotIn("tools", payload)
        self.assertIn("AGENT_FILE_BODY", json.dumps(payload))

    def test_default_full_assistant_includes_file_and_web_tools_without_a_model_allowlist(
        self,
    ):
        self.caps.write_text(json.dumps({"schema_version": 1, "models": {}}))
        fid = self.upload("DEFAULT_INLINE_ATTACHMENT")
        self.run_turn(mode="files", attachment_ids=[fid], web_enabled=True)
        payload = self.stream.calls[0]
        names = {tool["function"]["name"] for tool in payload["tools"]}
        self.assertTrue(
            {"read_file", "search_files", "write_file", "http_fetch", "download_url"}
            <= names
        )
        self.assertIn("DEFAULT_INLINE_ATTACHMENT", json.dumps(payload))
        self.run_turn(mode="files", text="继续分析上次引用的文件")
        self.assertIn("DEFAULT_INLINE_ATTACHMENT", json.dumps(self.stream.calls[-1]))

    def test_tampered_original_is_not_sent(self):
        fid = self.upload("VALID_BODY")
        scope = self.service._scope(self.actor.principal, self.cid)
        filename = Path(self.tmp.name) / scope.path / "uploads" / (fid + ".blob")
        filename.write_bytes(b"OTHER_BODY")
        with self.assertRaises(ChatError) as caught:
            self.service.accept_run(
                self.actor,
                self.cid,
                body(self.service, self.actor, self.cid, attachment_ids=[fid]),
            )
        self.assertEqual(caught.exception.code, "CHAT_STORAGE_CORRUPT")
        self.assertEqual(self.stream.calls, [])


if __name__ == "__main__":
    unittest.main()
