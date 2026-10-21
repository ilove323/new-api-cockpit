"""Pure offline working-context tests: no keys, network, storage or paid calls."""

import copy
import json
import unittest
from unittest.mock import patch
from uuid import uuid4

from new_api_cockpit.features.chat import config, context, protocols
from new_api_cockpit.features.chat import working_context as wc
from new_api_cockpit.features.chat.errors import ChatError


def snapshot():
    return {
        "policy_version": "personal-context-v1",
        "skill_ids": [],
        "skills": [],
        "profiles": [
            {"name": n, "content": "CURRENT_PERSONAL" if n == "MEMORY.md" else ""}
            for n in ("SOUL.md", "IDENTITY.md", "MEMORY.md")
        ],
    }


def binding(protocol="chat_completions"):
    return {
        "protocol": protocol,
        "model": "offline-model",
        "token_id": 7,
        "capability_revision": "sha256:" + "a" * 64,
    }


def request(**changes):
    return {
        **binding(),
        "text": "CURRENT_USER",
        "attachment_ids": [],
        "context_strategy": "adaptive",
        **changes,
    }


def policy(**changes):
    return {
        "protocol": "chat_completions",
        "max_input_bytes": 65536,
        "compaction": "summary",
        **changes,
    }


def event(event_type, rid, **data):
    return {"type": event_type, "run_id": rid, "data": data}


def round_events(
    text="old-user",
    answer="old-answer",
    *,
    status="completed",
    tools=False,
    protocol="chat_completions",
    candidates=1,
):
    rid, mid = str(uuid4()), str(uuid4())
    events = [
        event(
            "run.accepted",
            rid,
            **binding(protocol),
            text=text,
            attachment_ids=[],
            options={"candidate_count": candidates},
            context_snapshot={"private": "OLD_PERSONAL_NOT_REPLAYED"},
        )
    ]
    if tools:
        call = {
            "id": "call_" + uuid4().hex,
            "name": "read_file",
            "arguments": {"line": 1},
        }
        events += [
            event(
                "assistant.message",
                rid,
                message_id=str(uuid4()),
                text="checking",
                tool_calls=[call],
                complete=True,
                finish_reason="tool_calls",
            ),
            event(
                "tool.call",
                rid,
                call_id=call["id"],
                name=call["name"],
                arguments=call["arguments"],
            ),
            event(
                "tool.result",
                rid,
                call_id=call["id"],
                name=call["name"],
                ok=True,
                result={"text": "tool source"},
            ),
        ]
    for index in range(candidates):
        candidate = {"candidate_index": index} if candidates > 1 else {}
        events.append(
            event(
                "assistant.message",
                rid,
                message_id=mid if index == 0 else str(uuid4()),
                text=answer if candidates == 1 else answer + str(index),
                tool_calls=[],
                complete=True,
                finish_reason="stop",
                **candidate,
            )
        )
    events.append(event("run.terminal", rid, status=status))
    return rid, events


def ordered(events):
    result, cid = copy.deepcopy(events), str(uuid4())
    for index, item in enumerate(result, 1):
        item.update(seq=index, conversation_id=cid)
    return result


def summary(**changes):
    return {
        "goal": ["Keep the original user goal"],
        "constraints": ["No additional permissions"],
        "decisions": [],
        "progress": [],
        "pending": ["Continue current work"],
        "refs": [],
        **changes,
    }


def compact_case(protocol="chat_completions", mode="summary", count=4):
    events, ids = [], []
    for number in range(count):
        rid, items = round_events(
            f"user-{number}:" + "u" * 950,
            f"answer-{number}:" + "a" * 950,
            protocol=protocol,
        )
        ids.append(rid)
        events += items
    events = ordered(events)
    return (
        events,
        ids,
        request(protocol=protocol),
        policy(protocol=protocol, compaction=mode, max_input_bytes=6500),
    )


class ProjectionTests(unittest.TestCase):
    def test_full_history_no_recent_cap_no_input_mutation(self):
        history, ids = [], []
        for n in range(6):
            rid, events = round_events(str(n))
            ids.append(rid)
            history += events
        original = copy.deepcopy(history)
        with patch.object(config, "CONTEXT_HISTORY_RUNS", 2):
            result = wc.plan(history, snapshot(), request(), policy())
        self.assertFalse(result["compact_required"])
        self.assertEqual(result["included_run_ids"], ids)
        self.assertEqual(len(result["messages"]), 15)
        encoded = context.compact(result["messages"])
        self.assertNotIn("OLD_PERSONAL_NOT_REPLAYED", encoded)
        self.assertIn("CURRENT_PERSONAL", encoded)
        self.assertEqual(history, original)

    def test_recent_strategy_newest_whole_budget_without_changing_legacy(self):
        first, old = round_events("old" * 100)
        second, recent = round_events("new" * 100, tools=True)
        req = request(context_strategy="recent")
        latest = wc.plan(recent, snapshot(), req, policy())["messages"]
        size = wc.input_bytes(latest)
        result = wc.plan(old + recent, snapshot(), req, policy(max_input_bytes=size))
        self.assertEqual(result["included_run_ids"], [second])
        self.assertEqual(result["messages"], latest)
        self.assertFalse(result["compact_required"])
        self.assertNotIn(first, result["included_run_ids"])
        legacy, ids = context.build_messages(old + recent, snapshot(), req, size)
        self.assertEqual(legacy, result["messages"])
        self.assertEqual(ids, [second])
        self.assertEqual(
            wc.plan(old + recent, snapshot(), req, policy(max_input_bytes=size - 1))[
                "included_run_ids"
            ],
            [],
        )

    def test_failed_cancelled_and_interrupted_keep_user_safe_text_not_tools(self):
        for state in ("failed", "cancelled", "interrupted"):
            rid, events = round_events(
                "user:" + state, "safe partial", status=state, tools=True
            )
            original = copy.deepcopy(events)
            projected = wc.project_rounds(events, request(), policy())
            messages = projected[0]["messages"]
            with self.subTest(state=state):
                self.assertEqual(projected[0]["run_id"], rid)
                self.assertFalse(projected[0]["complete"])
                self.assertIn("safe partial", str(messages))
                self.assertTrue(
                    all(
                        m["content"].startswith(wc.PARTIAL_PREFIX)
                        for m in messages
                        if m["role"] == "assistant"
                    )
                )
                self.assertFalse(
                    any(
                        m["role"] == "tool" or "tool_calls" in m or "_native" in m
                        for m in messages
                    )
                )
                self.assertEqual(events, original)

    def test_partial_deltas_only_when_no_canonical_message_no_reasoning_pollution(self):
        rid, events = round_events(status="cancelled")
        mid = str(uuid4())
        events[1:2] = [
            event("assistant.delta", rid, message_id=mid, text="part one"),
            event("assistant.delta", rid, message_id=mid, text=" part two"),
            event(
                "assistant.reasoning",
                rid,
                message_id=mid,
                text="PUBLIC_REASONING_SEPARATE",
                kind="raw",
                field="thinking",
            ),
        ]
        result = wc.plan(events, snapshot(), request(), policy())
        encoded = context.compact(result["messages"])
        self.assertIn("part one part two", encoded)
        self.assertNotIn("PUBLIC_REASONING_SEPARATE", encoded)
        events.insert(
            -1,
            event(
                "assistant.message",
                rid,
                message_id=mid,
                text="canonical partial",
                complete=False,
                finish_reason="",
                tool_calls=[],
            ),
        )
        encoded = context.compact(
            wc.plan(events, snapshot(), request(), policy())["messages"]
        )
        self.assertIn("canonical partial", encoded)
        self.assertNotIn("part one part two", encoded)

    def test_malformed_incomplete_tools_are_stripped_not_user_semantics(self):
        for change in (
            "missing",
            "duplicate",
            "wrong_name",
            "wrong_args",
            "wrong_finish",
            "no_final",
        ):
            rid, events = round_events(tools=True)
            if change == "missing":
                del events[3]
            elif change == "duplicate":
                events.insert(4, copy.deepcopy(events[3]))
            elif change == "wrong_name":
                events[3]["data"]["name"] = "elsewhere"
            elif change == "wrong_args":
                events[2]["data"]["arguments"] = {"other": 1}
            elif change == "wrong_finish":
                events[1]["data"]["finish_reason"] = "stop"
            else:
                del events[-2]
            with self.subTest(change=change):
                result = wc.plan(events, snapshot(), request(), policy())
                self.assertEqual(result["included_run_ids"], [rid])
                self.assertIn("old-user", str(result["messages"]))
                self.assertFalse(
                    any(
                        m["role"] == "tool" or "tool_calls" in m
                        for m in result["messages"]
                    )
                )

    def test_whole_complete_tool_exchange_preserved(self):
        rid, events = round_events(tools=True)
        result = wc.plan(events, snapshot(), request(), policy())
        self.assertEqual(result["included_run_ids"], [rid])
        self.assertEqual(
            [m["role"] for m in result["messages"]],
            ["system", "user", "user", "assistant", "tool", "assistant", "user"],
        )
        call, tool = result["messages"][3:5]
        self.assertEqual(call["tool_calls"][0]["id"], tool["tool_call_id"])

    def test_selected_candidate_after_terminal_is_only_replayed_answer(self):
        rid, events = round_events(answer="branch-", candidates=3)
        events.append(event("candidate.selected", rid, candidate_index=2))
        for strategy in ("adaptive", "recent"):
            with self.subTest(strategy=strategy):
                result = wc.plan(
                    events, snapshot(), request(context_strategy=strategy), policy()
                )
                encoded = context.compact(result["messages"])
                self.assertIn("branch-2", encoded)
                self.assertNotIn("branch-0", encoded)
                self.assertNotIn("branch-1", encoded)

    def test_unselected_latest_successful_multirun_refuses_both_strategies(self):
        _, events = round_events(candidates=2)
        events += round_events(status="failed")[1]
        for strategy in ("adaptive", "recent"):
            with self.assertRaises(ChatError) as caught:
                wc.plan(
                    events, snapshot(), request(context_strategy=strategy), policy()
                )
            self.assertEqual(caught.exception.code, "CHAT_CANDIDATE_REQUIRED")

    def test_older_unselected_multirun_retains_user_but_no_arbitrary_branch(self):
        _, events = round_events("old unselected user", "UNSELECTED", candidates=2)
        events += round_events()[1]
        encoded = context.compact(
            wc.plan(events, snapshot(), request(), policy())["messages"]
        )
        self.assertIn("old unselected user", encoded)
        self.assertNotIn("UNSELECTED", encoded)

    def test_public_reasoning_is_not_synthetic_answer_and_state_is_message_scoped(self):
        rid, events = round_events()
        mid = events[1]["data"]["message_id"]
        events.insert(
            1,
            event(
                "assistant.protocol",
                rid,
                message_id="different-message",
                protocol="chat_completions",
                state={"reasoning_content": "WRONG"},
            ),
        )
        events.insert(
            1,
            event(
                "assistant.reasoning",
                rid,
                message_id=mid,
                text="VISIBLE_ONLY",
                kind="raw",
                field="reasoning_content",
            ),
        )
        result = wc.plan(events, snapshot(), request(), policy())
        self.assertNotIn("_native", result["messages"][3])
        self.assertNotIn("VISIBLE_ONLY", context.compact(result["messages"]))

    def test_replay_binding_all_four_fields_exact_and_protocol_dto_validated(self):
        for protocol in ("chat_completions", "responses", "anthropic"):
            rid, events = round_events(protocol=protocol)
            mid = events[1]["data"]["message_id"]
            state = {
                "chat_completions": {
                    "reasoning_content": "actually returned reasoning"
                },
                "responses": [
                    {"type": "reasoning", "id": "rs1", "encrypted_content": "opaque"},
                    {
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": "old-answer"}],
                    },
                ],
                "anthropic": [
                    {
                        "type": "thinking",
                        "thinking": "visible",
                        "signature": "signature",
                    },
                    {"type": "text", "text": "old-answer"},
                ],
            }[protocol]
            events.insert(
                1,
                event(
                    "assistant.protocol",
                    rid,
                    message_id=mid,
                    protocol=protocol,
                    state=state,
                ),
            )
            req, pol = (
                request(protocol=protocol, context_strategy="recent"),
                policy(protocol=protocol),
            )
            with self.subTest(protocol=protocol):
                result = wc.plan(events, snapshot(), req, pol)
                self.assertEqual(
                    result["messages"][3]["_native"],
                    {"protocol": protocol, "value": state},
                )
                for key, mismatch in (
                    ("protocol", "other"),
                    ("model", "other"),
                    ("token_id", 8),
                    ("capability_revision", "other"),
                ):
                    changed = copy.deepcopy(events)
                    changed[0]["data"][key] = mismatch
                    projected = wc.plan(changed, snapshot(), req, pol)
                    self.assertNotIn("_native", projected["messages"][3])
                    self.assertEqual(projected["messages"][3]["content"], "old-answer")
                malformed = copy.deepcopy(events)
                malformed[1]["data"]["state"] = {"unexpected": "unsafe"}
                self.assertNotIn(
                    "_native", wc.plan(malformed, snapshot(), req, pol)["messages"][3]
                )

    def test_legacy_protocol_absence_and_incomplete_native_tools_not_replayed(self):
        rid, events = round_events()
        mid = events[1]["data"]["message_id"]
        events.insert(
            1,
            event(
                "assistant.protocol",
                rid,
                message_id=mid,
                protocol="chat_completions",
                state={"reasoning": "opaque"},
            ),
        )
        del events[0]["data"]["protocol"]
        self.assertNotIn(
            "_native", wc.plan(events, snapshot(), request(), policy())["messages"][3]
        )
        rid, events = round_events(protocol="responses")
        mid = events[1]["data"]["message_id"]
        events.insert(
            1,
            event(
                "assistant.protocol",
                rid,
                message_id=mid,
                protocol="responses",
                state=[
                    {
                        "type": "function_call",
                        "call_id": "orphan",
                        "name": "read_file",
                        "arguments": "{}",
                    },
                ],
            ),
        )
        result = wc.plan(
            events,
            snapshot(),
            request(protocol="responses"),
            policy(protocol="responses"),
        )
        self.assertNotIn("_native", result["messages"][3])

    def test_current_and_historical_attachment_summary_only_and_scope_guard(self):
        fid = str(uuid4())
        rid, history = round_events()
        history[0]["data"]["attachment_ids"] = [fid]
        history.insert(
            0,
            event(
                "file.added",
                None,
                file={"id": fid, "name": "data.txt", "size_bytes": 10},
            ),
        )
        result = wc.plan(
            history,
            snapshot(),
            request(
                attachments=[
                    {
                        "id": fid,
                        "name": "data.txt",
                        "size_bytes": 10,
                        "path": "/private/not-sent",
                        "content": "NO_BODY",
                    }
                ],
                attachment_ids=[fid],
            ),
            policy(),
        )
        encoded = context.compact(result["messages"])
        self.assertEqual(encoded.count(fid), 2)
        self.assertNotIn("NO_BODY", encoded)
        self.assertNotIn("/private", encoded)
        mixed = ordered(history)
        mixed[-1]["conversation_id"] = "another-chat"
        with self.assertRaises(ChatError):
            wc.plan(mixed, snapshot(), request(), policy())


class CompactionTests(unittest.TestCase):
    def test_summary_proactive_preserves_two_recent_current_and_personal(self):
        history, ids, req, pol = compact_case()
        before = copy.deepcopy(history)
        planned = wc.plan(history, snapshot(), req, pol)
        self.assertTrue(planned["compact_required"])
        self.assertIsNone(planned["messages"])
        self.assertEqual(planned["compact_mode"], "summary")
        self.assertEqual(planned["compact_covered_run_ids"], ids[:-2])
        self.assertEqual(planned["recent_run_ids"], ids[-2:])
        self.assertLessEqual(
            wc.input_bytes(planned["compact_messages"]), pol["max_input_bytes"]
        )
        compact_input = context.compact(planned["compact_messages"])
        self.assertIn("older_prefix", compact_input)
        self.assertNotIn("CURRENT_PERSONAL", compact_input)
        self.assertNotIn("CURRENT_USER", compact_input)
        result = wc.finalize(
            planned, {"text": json.dumps(summary()), "calls": [], "finish": "stop"}
        )
        self.assertFalse(result["compact_required"])
        self.assertEqual(result["included_run_ids"], ids)
        self.assertEqual(result["messages"][-1]["content"], "CURRENT_USER")
        self.assertIn("CURRENT_PERSONAL", result["messages"][1]["content"])
        self.assertTrue(result["messages"][2]["content"].startswith(wc.SUMMARY_PREFIX))
        self.assertEqual(result["messages"][3:], planned["recent_messages"][2:])
        self.assertEqual(history, before)
        self.assertIsNone(planned["messages"])

    def test_threshold_starts_compaction_before_hard_budget(self):
        history, _, req, _ = compact_case()
        full = wc.plan(history, snapshot(), req, policy())["messages"]
        size = wc.input_bytes(full)
        planned = wc.plan(history, snapshot(), req, policy(max_input_bytes=size))
        self.assertTrue(planned["compact_required"])
        self.assertLessEqual(size, planned["budget"])
        below = wc.plan(
            history, snapshot(), req, policy(max_input_bytes=(size * 100 + 74) // 75)
        )
        self.assertFalse(below["compact_required"])
        self.assertEqual(below["messages"], full)

    def test_incompressible_recent_can_fit_without_auxiliary_charge(self):
        _, history = round_events("big" * 300)
        full = wc.plan(history, snapshot(), request(), policy())["messages"]
        planned = wc.plan(
            history, snapshot(), request(), policy(max_input_bytes=wc.input_bytes(full))
        )
        self.assertFalse(planned["compact_required"])
        self.assertEqual(planned["messages"], full)

    def test_partial_rounds_newer_than_last_two_complete_are_also_retained(self):
        history, ids, req, pol = compact_case()
        rid, events = round_events("cancelled-user", "safe-partial", status="cancelled")
        history += events
        planned = wc.plan(history, snapshot(), req, pol)
        self.assertEqual(planned["recent_run_ids"], ids[-2:] + [rid])
        self.assertIn("safe-partial", str(planned["recent_messages"]))

    def test_summary_schema_size_duplicate_keys_and_invalid_outputs_fail_without_retry(
        self,
    ):
        history, _, req, pol = compact_case()
        planned = wc.plan(history, snapshot(), req, pol)
        invalid = [
            "not JSON",
            "```json\n{}\n```",
            {},
            summary(extra=[]),
            summary(goal="not-array"),
            summary(goal=["x" * 16384]),
            summary(goal=["\ud800"]),
            summary(**{key: [] for key in wc.SUMMARY_KEYS}),
            '{"goal":[],"goal":["duplicate"]}',
            {"text": json.dumps(summary()), "calls": [{}]},
        ]
        for value in invalid:
            with (
                self.subTest(value=str(value)[:60]),
                self.assertRaises(ChatError) as caught,
            ):
                wc.finalize(planned, value)
            self.assertEqual(caught.exception.code, "CHAT_CONTEXT_COMPACTION_INVALID")
        normalized = wc.finalize(planned, summary())
        self.assertLessEqual(wc.input_bytes(normalized["checkpoint"]["summary"]), 16384)

    def test_summary_exact_16k_and_json_escape_bytes(self):
        value = summary(goal=["x"])
        base = wc.input_bytes(value)
        value["goal"] = ["x" * (16384 - base + 1)]
        self.assertEqual(wc.input_bytes(wc._summary(value)), 16384)
        value["goal"][0] += "x"
        with self.assertRaises(ChatError):
            wc._summary(value)
        value = summary(goal=['"' * 8192])
        with self.assertRaises(ChatError):
            wc._summary(value)

    def test_required_and_old_history_auxiliary_inputs_fail_explicitly(self):
        required = wc.plan([], snapshot(), request(), policy())["messages"]
        with self.assertRaises(ChatError) as caught:
            wc.plan(
                [],
                snapshot(),
                request(),
                policy(max_input_bytes=wc.input_bytes(required) - 1),
            )
        self.assertEqual(caught.exception.code, "CHAT_CONTEXT_TOO_LARGE")
        history, _, req, pol = compact_case(count=12)
        with self.assertRaises(ChatError) as caught:
            wc.plan(history, snapshot(), req, pol)
        self.assertEqual(caught.exception.code, "CHAT_CONTEXT_COMPACTION_LIMIT")
        history, _, req, pol = compact_case()
        with self.assertRaises(ChatError):
            wc.plan(history, snapshot(), req, pol | {"compaction": "none"})

    def test_summary_output_must_fit_retained_input_budget(self):
        history, _, req, pol = compact_case()
        planned = wc.plan(history, snapshot(), req, pol)
        with self.assertRaises(ChatError) as caught:
            wc.finalize(planned, summary(goal=["x" * 6000]))
        self.assertEqual(caught.exception.code, "CHAT_CONTEXT_COMPACTION_LIMIT")

    def test_checkpoint_coverage_binding_and_integrity_invalidates_without_losing_sources(
        self,
    ):
        history, ids, req, pol = compact_case()
        finished = wc.finalize(wc.plan(history, snapshot(), req, pol), summary())
        checkpoint = finished["checkpoint"]
        self.assertEqual(checkpoint["covered_run_ids"], ids[:-2])
        for name, mutate, expected in (
            ("token", lambda c: c["binding"].update(token_id=8), "incompatible"),
            ("model", lambda c: c["binding"].update(model="other"), "incompatible"),
            (
                "protocol",
                lambda c: c["binding"].update(protocol="responses"),
                "incompatible",
            ),
            (
                "revision",
                lambda c: c["binding"].update(capability_revision="other"),
                "incompatible",
            ),
            ("coverage", lambda c: c["covered_run_ids"].reverse(), "stale"),
            ("source", lambda c: c.update(source_sha256="0" * 64), "stale"),
            ("content", lambda c: c["summary"]["goal"].append("tampered"), "invalid"),
        ):
            bad = copy.deepcopy(checkpoint)
            mutate(bad)
            with self.subTest(name=name):
                result = wc.plan(history, snapshot(), req, pol, bad)
                self.assertEqual(result["checkpoint_status"], expected)
                self.assertTrue(result["compact_required"])
                self.assertEqual(result["included_run_ids"], ids)

    def test_unchanged_checkpoint_is_not_recompacted_above_threshold(self):
        history, _, req, pol = compact_case()
        finished = wc.finalize(wc.plan(history, snapshot(), req, pol), summary())
        again = wc.plan(history, snapshot(), req, pol, finished["checkpoint"])
        self.assertEqual(again["checkpoint_status"], "accepted")
        self.assertFalse(again["compact_required"])
        self.assertEqual(again["messages"], finished["messages"])

    def test_rolling_summary_contains_prior_summary_plus_new_older_prefix(self):
        history, _, req, pol = compact_case()
        finished = wc.finalize(wc.plan(history, snapshot(), req, pol), summary())
        _, additional = round_events("new-user:" + "u" * 950, "new-answer:" + "a" * 950)
        history += additional
        planned = wc.plan(history, snapshot(), req, pol, finished["checkpoint"])
        self.assertTrue(planned["compact_required"])
        payload = json.loads(planned["compact_messages"][-1]["content"])
        self.assertEqual(payload["prior_summary"], summary())
        self.assertIn("user-2:", str(payload["older_prefix"]))
        self.assertNotIn("user-0:", str(payload["older_prefix"]))
        self.assertEqual(len(planned["compact_covered_run_ids"]), 3)

    def test_checkpoint_source_hash_covers_original_text(self):
        history, _, req, pol = compact_case()
        finished = wc.finalize(wc.plan(history, snapshot(), req, pol), summary())
        history[1]["data"]["text"] += " changed original"
        replanned = wc.plan(history, snapshot(), req, pol, finished["checkpoint"])
        self.assertEqual(replanned["checkpoint_status"], "stale")
        self.assertNotEqual(replanned["source_sha256"], finished["source_sha256"])

    def test_checkpoint_source_hash_covers_attachment_metadata(self):
        history, _, req, pol = compact_case()
        fid = str(uuid4())
        history[0]["data"]["attachment_ids"] = [fid]
        file_event = event(
            "file.added",
            None,
            file={
                "id": fid,
                "name": "original.txt",
                "size_bytes": 10,
            },
        )
        history.insert(0, file_event)
        finished = wc.finalize(wc.plan(history, snapshot(), req, pol), summary())
        file_event["data"]["file"]["name"] = "changed.txt"
        replanned = wc.plan(history, snapshot(), req, pol, finished["checkpoint"])
        self.assertEqual(replanned["checkpoint_status"], "stale")
        self.assertNotEqual(replanned["source_sha256"], finished["source_sha256"])

    def test_checkpoint_source_hash_covers_reselected_candidate(self):
        history, _, req, pol = compact_case()
        first_rid = history[0]["run_id"]
        first_end = next(
            i for i, e in enumerate(history) if e["type"] == "run.terminal"
        )
        history[0]["data"]["options"] = {"candidate_count": 2}
        history[1]["data"]["candidate_index"] = 0
        other = copy.deepcopy(history[1])
        other["data"].update(message_id=str(uuid4()), candidate_index=1)
        other["data"]["text"] = "alternative:" + "b" * 950
        choice = event("candidate.selected", first_rid, candidate_index=0)
        history.insert(first_end, other)
        history.insert(first_end + 2, choice)
        finished = wc.finalize(wc.plan(history, snapshot(), req, pol), summary())
        choice["data"]["candidate_index"] = 1
        replanned = wc.plan(history, snapshot(), req, pol, finished["checkpoint"])
        self.assertEqual(replanned["checkpoint_status"], "stale")
        self.assertNotEqual(replanned["source_sha256"], finished["source_sha256"])

    def test_native_explicit_responses_only_whole_returned_window_replayed(self):
        history, ids, req, pol = compact_case("responses", "native")
        planned = wc.plan(history, snapshot(), req, pol)
        self.assertTrue(planned["compact_required"])
        self.assertIsNone(planned["compact_messages"])
        self.assertLessEqual(
            wc.input_bytes(planned["compact_input"]), pol["max_input_bytes"]
        )
        self.assertEqual(len(planned["compact_input"]), 4)
        window = [
            {"role": "user", "content": "original retained user"},
            {"type": "compaction", "id": "cp1", "encrypted_content": "OPAQUE"},
            {"role": "assistant", "content": "original retained answer"},
        ]
        finished = wc.finalize(planned, {"output": window})
        self.assertEqual(finished["window"], window)
        self.assertEqual(finished["checkpoint"]["window"], window)
        self.assertEqual(finished["checkpoint"]["covered_run_ids"], ids[:-2])
        body = protocols.wire_body(
            "responses",
            {
                "model": req["model"],
                "messages": finished["messages"],
                "_window": finished["window"],
            },
        )
        self.assertEqual(body["input"][1:4], window)
        again = wc.plan(history, snapshot(), req, pol, finished["checkpoint"])
        self.assertEqual(again["window"], window)
        self.assertFalse(again["compact_required"])
        with self.assertRaises(ChatError):
            wc.plan(
                history,
                snapshot(),
                request(),
                policy(max_input_bytes=6500, compaction="native"),
            )

    def test_native_invalid_partial_or_orphan_tools_and_oversize_window_fail(self):
        history, _, req, pol = compact_case("responses", "native")
        planned = wc.plan(history, snapshot(), req, pol)
        for value in (
            [],
            [{"type": "unknown"}],
            [{"role": "assistant", "content": "partial", "status": "incomplete"}],
            [{"type": "function_call_output", "call_id": "orphan", "output": "bad"}],
            [
                {
                    "type": "function_call",
                    "call_id": "unfinished",
                    "name": "read_file",
                    "arguments": "{}",
                }
            ],
        ):
            with self.subTest(value=value), self.assertRaises(ChatError) as caught:
                wc.finalize(planned, value)
            self.assertEqual(caught.exception.code, "CHAT_CONTEXT_COMPACTION_INVALID")
        with self.assertRaises(ChatError) as caught:
            wc.finalize(planned, [{"role": "user", "content": "x" * 6500}])
        self.assertEqual(caught.exception.code, "CHAT_CONTEXT_COMPACTION_LIMIT")


class ActiveAndSearchTests(unittest.TestCase):
    def active_messages(self):
        messages = wc.plan([], snapshot(), request(), policy())["messages"]
        for index in range(3):
            messages += [
                {
                    "role": "assistant",
                    "content": "checking:" + str(index),
                    "tool_calls": [
                        {
                            "id": "active_" + str(index),
                            "type": "function",
                            "function": {"name": "read_file", "arguments": "{}"},
                        },
                    ],
                },
                {
                    "role": "tool",
                    "tool_call_id": "active_" + str(index),
                    "content": "result:" + str(index) + "t" * 1600,
                },
            ]
        return messages

    def test_active_compacts_whole_older_pairs_keeps_current_intent_latest_pair(self):
        messages = self.active_messages()
        pol = policy(max_input_bytes=8000)
        planned = wc.plan_active(messages, pol, binding())
        self.assertTrue(planned["compact_required"])
        self.assertFalse(planned["persist_checkpoint"])
        self.assertEqual(planned["recent_messages"], messages[:3] + messages[-2:])
        payload = json.loads(planned["compact_messages"][-1]["content"])
        older = payload["older_prefix"]
        self.assertEqual(
            [m["role"] for m in older], ["assistant", "tool", "assistant", "tool"]
        )
        finished = wc.finalize(planned, summary())
        self.assertIsNone(finished["checkpoint"])
        self.assertEqual(finished["messages"][-3:], messages[2:3] + messages[-2:])
        self.assertLessEqual(wc.input_bytes(finished["messages"]), 8000)

    def test_active_rejects_incomplete_pairs_even_below_threshold(self):
        messages = self.active_messages()
        messages.pop()
        with self.assertRaises(ChatError):
            wc.plan_active(messages, policy(), binding())

    def test_active_single_required_tool_pair_over_budget_fails(self):
        messages = self.active_messages()
        messages = messages[:3] + messages[-2:]
        with self.assertRaises(ChatError) as caught:
            wc.plan_active(messages, policy(max_input_bytes=2000), binding())
        self.assertEqual(caught.exception.code, "CHAT_CONTEXT_TOO_LARGE")

    def test_active_native_window_and_new_input_preserve_whole_output(self):
        messages = self.active_messages()
        pol = policy(protocol="responses", compaction="native", max_input_bytes=8000)
        previous = [
            {"role": "user", "content": "prior"},
            {"type": "compaction", "encrypted_content": "opaque"},
        ]
        planned = wc.plan_active(messages, pol, binding("responses"), window=previous)
        self.assertTrue(planned["compact_required"])
        self.assertEqual(planned["compact_input"][:2], previous)
        finished = wc.finalize(planned, previous)
        self.assertEqual(finished["window"], previous)
        self.assertIsNone(finished["checkpoint"])

    def test_search_only_supplied_chat_public_sources_no_opaque_or_old_personal(self):
        rid, events = round_events("needle USER", "NEEDLE answer", candidates=2)
        events.insert(
            1,
            event(
                "assistant.protocol",
                rid,
                message_id=str(uuid4()),
                protocol="chat_completions",
                state={"reasoning_content": "needle OPAQUE_NOT_SEARCHABLE"},
            ),
        )
        events = ordered(events)
        result = wc.search_history(events, "needle", limit=20)
        self.assertEqual(len(result["matches"]), 3)
        self.assertEqual(result["matches"][0]["event_id"], 1)
        self.assertEqual(result["matches"][1]["candidate_index"], 0)
        self.assertNotIn("OPAQUE_NOT_SEARCHABLE", str(result))
        self.assertNotIn("OLD_PERSONAL_NOT_REPLAYED", str(result))
        self.assertEqual(
            wc.search_history(events, "NEEDLE", case_sensitive=True)["matches"][0][
                "type"
            ],
            "assistant.message",
        )
        self.assertEqual(wc.search_history([], "needle")["matches"], [])
        with self.assertRaises(TypeError):
            wc.search_history(events, "needle", conversation_id="other")

    def test_search_cursor_and_json_size_bounds_literal_query_no_regex(self):
        history = ordered(
            sum((round_events("needle:" + str(i))[1] for i in range(8)), [])
        )
        first = wc.search_history(history, "needle", limit=2)
        second = wc.search_history(
            history, "needle", offset=first["next_offset"], limit=2
        )
        self.assertEqual(len(first["matches"]), 2)
        self.assertEqual(len(second["matches"]), 2)
        self.assertLess(
            first["matches"][-1]["event_id"], second["matches"][0]["event_id"]
        )
        self.assertLessEqual(
            wc.input_bytes(wc.search_history(history, "needle", max_bytes=700)), 700
        )
        self.assertEqual(wc.search_history(history, ".*")["matches"], [])
        with patch.object(config, "TOOL_SEARCH_SCAN_BYTES", 20):
            result = wc.search_history(history, "absent")
            self.assertTrue(result["truncated"])
            self.assertIsNotNone(result["next_offset"])

    def test_search_schema_has_no_crosschat_selector_and_is_fresh(self):
        schema = wc.history_schema()
        props = schema["function"]["parameters"]
        self.assertFalse(props["additionalProperties"])
        self.assertEqual(
            set(props["properties"]), {"query", "offset", "limit", "case_sensitive"}
        )
        props["properties"].clear()
        self.assertTrue(wc.history_schema()["function"]["parameters"]["properties"])


if __name__ == "__main__":
    unittest.main()
