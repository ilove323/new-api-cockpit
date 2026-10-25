"""Independent offline review regressions; verified after integration repairs.

Only synthetic adapters and temporary owner-scoped storage are used. The
compaction repro uses the real local read_file tool, not a mocked tool result.
"""

import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

from chat_fixtures import access, body, create, make_service
from new_api_cockpit.features.chat import config, recovery, working_context as wc
from new_api_cockpit.features.chat.errors import ChatError


SUMMARY = {
    "goal": ["read all five lines"],
    "constraints": [],
    "decisions": [],
    "progress": [],
    "pending": [],
    "refs": [],
}


class ChatV2CoreReviewRegressionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.service, self.tokens, self.fake = make_service(self.tmp.name)
        self.addCleanup(self.service.store.close)
        self.addCleanup(self.service.shutdown)
        self.actor = access()
        self.cid = create(self.service, self.actor)
        self.enterContext(
            patch("socket.socket.connect", side_effect=AssertionError("no network"))
        )

    def configure(self, budget):
        Path(self.tmp.name, "capabilities.json").write_text(
            json.dumps(
                {
                    "schema_version": 2,
                    "models": {
                        "test-chat": {
                            "tools": True,
                            "max_input_bytes": budget,
                            "compaction": "summary",
                        }
                    },
                }
            )
        )

    def turn(self, **changes):
        request = body(
            self.service,
            self.actor,
            self.cid,
            context_strategy="adaptive",
            **changes,
        )
        active = self.service.accept_run(self.actor, self.cid, request)
        output = "".join(self.service.stream(active))
        state = self.service.get_run(self.actor.principal, self.cid, active.run_id)
        return active, state, output

    def test_compaction_cap_does_not_abort_a_still_runnable_main_request(self):
        self.configure(21000)
        source, _ = self.service.upload(
            self.actor.principal,
            self.cid,
            str(uuid4()),
            "source.txt",
            (("x" * 7000 + "\n") * 5).encode(),
        )
        main_calls, summary_calls, plans = [], [], []

        def stream(key, payload, deadline, abort):
            is_summary = payload["messages"][0]["content"].startswith(
                "为当前聊天生成可读工作检查点"
            )
            if is_summary:
                summary_calls.append(payload)
                yield {"type": "text", "text": json.dumps(SUMMARY)}
                yield {"type": "finish", "reason": "stop"}
            else:
                main_calls.append(payload)
                number = len(main_calls)
                if number <= 5:
                    yield {
                        "type": "tool_delta",
                        "index": 0,
                        "id": f"read_{number}",
                        "name": "read_file",
                        "arguments": json.dumps(
                            {
                                "file_id": source["id"],
                                "start_line": number,
                                "max_lines": 1,
                            }
                        ),
                    }
                    yield {"type": "finish", "reason": "tool_calls"}
                else:
                    yield {"type": "text", "text": "done"}
                    yield {"type": "finish", "reason": "stop"}
            yield {
                "type": "usage",
                "prompt_tokens": 10,
                "completion_tokens": 5,
                "total_tokens": 15,
            }

        original = self.service.runner._apply_context_plan

        def observe(run, planned):
            plans.append(
                {
                    "compactions": run.compaction_calls,
                    "size": wc.input_bytes(run.messages, run.working_window),
                    "budget": planned["budget"],
                    "required": planned["compact_required"],
                }
            )
            return original(run, planned)

        self.service.runner.stream_chat = stream
        self.service.runner._apply_context_plan = observe
        _, state, _ = self.turn(
            mode="files",
            text="read all five source lines",
            # This regression exercises paged tool reads. Explicitly referenced
            # attachments are now hydrated in full and correctly exceed this budget.
            attachment_ids=[],
        )
        last = plans[-1]
        self.assertEqual(last["compactions"], wc.MAX_COMPACTION_CALLS)
        self.assertTrue(last["required"])
        self.assertLessEqual(last["size"], last["budget"])
        self.assertEqual(len(summary_calls), 3)
        self.assertEqual(
            state["status"],
            "completed",
            f"Runnable final input {last}; got {state['error_code']} after "
            f"{state['main_model_requests']} main and "
            f"{state['aux_model_requests']} auxiliary calls",
        )
        self.assertEqual(len(main_calls), 6)

    def test_full_adaptive_coverage_over_1024_ids_is_a_valid_accepted_event(self):
        self.configure(config.EXTENDED_CONTEXT_BYTES)
        active, state, _ = self.turn()
        self.assertEqual(state["status"], "completed")
        scope = self.service._scope(self.actor.principal, self.cid)
        template = next(
            e for e in self.service._history(scope) if e["type"] == "run.accepted"
        )
        history, ids = [], []
        for _ in range(1025):
            rid = str(uuid4())
            ids.append(rid)
            history.extend(
                [
                    {
                        "type": "run.accepted",
                        "run_id": rid,
                        "data": {"text": "u", "attachment_ids": []},
                    },
                    {
                        "type": "assistant.message",
                        "run_id": rid,
                        "data": {
                            "message_id": str(uuid4()),
                            "text": "a",
                            "tool_calls": [],
                            "complete": True,
                            "finish_reason": "stop",
                        },
                    },
                    {
                        "type": "run.terminal",
                        "run_id": rid,
                        "data": {"status": "completed"},
                    },
                ]
            )
        request = body(self.service, self.actor, self.cid, context_strategy="adaptive")
        planned = wc.plan(
            history,
            self.service.store.read_context(scope, active.run_id),
            request,
            {
                "protocol": "chat_completions",
                "compaction": "summary",
                "max_input_bytes": config.EXTENDED_CONTEXT_BYTES,
            },
        )
        self.assertEqual(planned["included_run_ids"], ids)
        self.assertFalse(planned["compact_required"])
        event = copy.deepcopy(template)
        event["seq"] += 10000
        event["data"]["context_from_run_ids"] = planned["included_run_ids"]
        # The exact server-generated coverage must be persistable. Currently
        # this raises CHAT_INVALID_REQUEST solely because len(ids) == 1025.
        recovery.validate_event(event, scope, event["seq"])

    def test_adaptive_service_remains_usable_past_the_legacy_recent_turn_cap(self):
        self.configure(config.EXTENDED_CONTEXT_BYTES)
        # Reduced legacy cap exercises the same service/store boundary without
        # writing 1025 real fixture runs. Production defaults to 1024.
        with patch.object(config, "CONTEXT_HISTORY_RUNS", 2):
            for _ in range(3):
                _, state, _ = self.turn()
                self.assertEqual(state["status"], "completed")
            _, state, _ = self.turn()
            self.assertEqual(state["status"], "completed")

    def test_adaptive_metadata_bound_is_explicit_and_does_not_dispatch(self):
        self.configure(config.EXTENDED_CONTEXT_BYTES)
        with patch.object(config, "CONTEXT_COVERAGE_RUNS", 2):
            for _ in range(3):
                _, state, _ = self.turn()
                self.assertEqual(state["status"], "completed")
            count = len(self.fake.calls)
            with self.assertRaises(ChatError) as error:
                self.turn()
            self.assertEqual(error.exception.code, "CHAT_CONTEXT_METADATA_LIMIT")
            self.assertEqual(error.exception.status, 413)
            self.assertEqual(len(self.fake.calls), count)

    def test_malformed_options_fail_as_validation_not_attribute_error(self):
        active, state, _ = self.turn()
        scope = self.service._scope(self.actor.principal, self.cid)
        event = copy.deepcopy(
            next(e for e in self.service._history(scope) if e["type"] == "run.accepted")
        )
        for value in (None, [], "adaptive", 1):
            event["data"]["options"] = value
            with self.subTest(value=value), self.assertRaises(ChatError):
                recovery.validate_event(event, scope, event["seq"])


if __name__ == "__main__":
    unittest.main()
