"""Protocol/compact interoperability on local HTTP sockets, never paid models."""

import copy
import json
import time
import unittest
from unittest.mock import patch

from new_api_cockpit.features.chat import config, protocols, transport
from new_api_cockpit.features.chat.errors import ChatError
from test_chat_transport import BODY, KEY, frame, upstream

TOOL = {
    "type": "function",
    "function": {
        "name": "list_files",
        "description": "列文件",
        "parameters": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    },
}
REASONING = {
    "type": "reasoning",
    "id": "rs_1",
    "summary": [{"type": "summary_text", "text": "公开摘要"}],
    "encrypted_content": "opaque-state-unchanged",
}
CALL = {
    "type": "function_call",
    "id": "fc_1",
    "call_id": "call_1",
    "name": "list_files",
    "arguments": "{}",
    "status": "completed",
}
MESSAGE = {
    "type": "message",
    "id": "msg_1",
    "role": "assistant",
    "status": "completed",
    "phase": "final_answer",
    "content": [
        {"type": "output_text", "text": "你好🌍", "annotations": [], "logprobs": []}
    ],
}
WINDOW = [
    {
        "type": "message",
        "id": "msg_user",
        "role": "user",
        "status": "completed",
        "content": [{"type": "input_text", "text": "历史原文"}],
    },
    {"type": "compaction", "id": "cmp_1", "encrypted_content": "native-compact-state"},
]


def event(kind, **body):
    return (
        "event: "
        + kind
        + "\ndata: "
        + json.dumps({"type": kind, **body}, ensure_ascii=False)
        + "\n\n"
    ).encode()


def responses(items=None, *, status="completed", usage=True):
    value = {
        "id": "resp_1",
        "status": status,
        "output": items if items is not None else [REASONING, MESSAGE],
    }
    if usage:
        value["usage"] = {"input_tokens": 12, "output_tokens": 8, "total_tokens": 20}
    return event("response." + status, response=value)


def anthropic(blocks=None, *, reason="end_turn"):
    blocks = blocks or [
        {
            "type": "thinking",
            "thinking": "思考摘要",
            "signature": "signature-unchanged",
        },
        {"type": "text", "text": "你好🌍"},
    ]
    raw = event(
        "message_start",
        message={
            "id": "msg_1",
            "role": "assistant",
            "content": [],
            "usage": {
                "input_tokens": 12,
                "cache_read_input_tokens": 3,
                "cache_creation_input_tokens": 1,
            },
        },
    )
    for i, block in enumerate(blocks):
        raw += event("content_block_start", index=i, content_block=block)
        raw += event("content_block_stop", index=i)
    return (
        raw
        + event(
            "message_delta", delta={"stop_reason": reason}, usage={"output_tokens": 8}
        )
        + event("message_stop")
    )


def collect(protocol, body=None, **kwargs):
    return list(
        transport.stream_model(
            KEY,
            body or BODY,
            time.monotonic() + 3,
            transport.AbortControl(),
            protocol=protocol,
            **kwargs,
        )
    )


class ProtocolV2Test(unittest.TestCase):
    def test_plain_chat_compatibility_and_raw_reasoning_replay(self):
        raw = (
            frame({"reasoning_content": "公开原始🌍"})
            + frame({"content": "回答"})
            + frame(finish="stop")
            + b"data: [DONE]\n\n"
        )
        with upstream(body=raw) as server:
            events = collect("chat_completions")
        self.assertEqual(
            [e["text"] for e in events if e["type"] == "reasoning"], ["公开原始🌍"]
        )
        state = next(e["value"] for e in events if e["type"] == "replay")
        self.assertEqual(state, {"reasoning_content": "公开原始🌍"})
        body = {
            **BODY,
            "messages": [
                *BODY["messages"],
                {
                    "role": "assistant",
                    "content": "回答",
                    "_native": {"protocol": "chat_completions", "value": state},
                },
                {"role": "user", "content": "继续"},
            ],
        }
        self.assertEqual(
            protocols.wire_body("chat_completions", body)["messages"][1][
                "reasoning_content"
            ],
            "公开原始🌍",
        )
        self.assertEqual(server.requests[0][0], "POST /v1/chat/completions HTTP/1.1")

    def test_native_multiple_candidates_interleaved_independent_finishes(self):
        def choice(i, text=None, reason=None):
            return {
                "index": i,
                "delta": {"content": text} if text else {},
                "finish_reason": reason,
            }

        raw = (
            frame(choices=[choice(1, "乙"), choice(0, "甲")])
            + frame(choices=[choice(0, reason="stop")])
            + frame(choices=[choice(1, reason="length")])
            + frame(
                choices=[],
                usage={"prompt_tokens": 5, "completion_tokens": 6, "total_tokens": 11},
            )
            + b"data: [DONE]\n\n"
        )
        with upstream(body=raw) as server:
            events = collect("chat_completions", {**BODY, "n": 2})
        self.assertEqual(
            [(e["candidate_index"], e["text"]) for e in events if e["type"] == "text"],
            [(1, "乙"), (0, "甲")],
        )
        self.assertEqual(
            [e["candidate_index"] for e in events if e["type"] == "finish"], [0, 1]
        )
        self.assertEqual(len(server.requests), 1)
        self.assertEqual(json.loads(server.requests[0][2])["n"], 2)
        self.assertNotIn("candidate_index", events[-1])

    def test_candidates_reject_missing_finish_bad_index_duplicate_reasoning_fields(
        self,
    ):
        invalid = [
            frame(finish="stop") + b"data: [DONE]\n\n",
            frame(choices=[{"index": 2, "delta": {}, "finish_reason": "stop"}]),
            frame({"reasoning_content": "a"}) + frame({"reasoning": "b"}),
            frame({"reasoning_content": "a", "reasoning": "b"}),
        ]
        for raw in invalid:
            with (
                self.subTest(raw=raw),
                upstream(body=raw) as server,
                self.assertRaises(ChatError),
            ):
                collect("chat_completions", {**BODY, "n": 2})
            self.assertEqual(len(server.requests), 1)

    def test_responses_stateless_output_and_encrypted_state_preserved(self):
        raw = (
            event("response.created", response={"id": "resp_1"})
            + event("response.reasoning_summary_text.delta", delta="公开")
            + event("response.output_text.delta", delta="你好")
            + responses()
        )
        with upstream(body=raw) as server:
            events = collect("responses")
        self.assertEqual(
            "".join(e["text"] for e in events if e["type"] == "text"), "你好🌍"
        )
        self.assertEqual(
            "".join(e["text"] for e in events if e["type"] == "reasoning"), "公开摘要"
        )
        state = next(e["value"] for e in events if e["type"] == "replay")
        self.assertEqual(state[0], REASONING)
        self.assertEqual(state[1]["phase"], "final_answer")
        wire = json.loads(server.requests[0][2])
        self.assertEqual(server.requests[0][0], "POST /v1/responses HTTP/1.1")
        self.assertIs(wire["store"], False)
        self.assertEqual(wire["include"], ["reasoning.encrypted_content"])
        self.assertNotIn("messages", wire)
        self.assertNotIn("previous_response_id", wire)
        self.assertEqual(events[-2]["total_tokens"], 20)

    def test_responses_function_roundtrip_not_chat_tool_messages(self):
        with upstream(body=responses([REASONING, CALL])):
            events = collect("responses", {**BODY, "tools": [TOOL]})
        self.assertEqual(events[-1], {"type": "finish", "reason": "tool_calls"})
        state = next(e["value"] for e in events if e["type"] == "replay")
        body = {
            **BODY,
            "tools": [TOOL],
            "messages": [
                *BODY["messages"],
                {
                    "role": "assistant",
                    "content": "",
                    "_native": {"protocol": "responses", "value": state},
                },
                {"role": "tool", "tool_call_id": "call_1", "content": '{"files":[]}'},
            ],
        }
        wire = protocols.wire_body("responses", body)
        self.assertEqual(wire["input"][1:3], [REASONING, CALL])
        self.assertEqual(
            wire["input"][-1],
            {
                "type": "function_call_output",
                "call_id": "call_1",
                "output": '{"files":[]}',
            },
        )
        self.assertEqual(wire["tools"][0]["type"], "function")
        self.assertEqual(wire["tools"][0]["name"], "list_files")

    def test_responses_requires_terminal_and_consistent_output_no_server_tools(self):
        invalid = [
            b"data: [DONE]\n\n",
            event("response.output_text.delta", delta="a"),
            event("response.output_text.delta", delta="wrong") + responses(),
            event("response.failed", response={"status": "failed"}),
            responses([CALL]),
            responses([{"type": "web_search_call"}]),
            responses([{**CALL, "arguments": "{"}]),
            responses(
                [{**REASONING, "summary": [{"type": "summary_text", "text": 1}]}]
            ),
        ]
        for raw in invalid:
            with (
                self.subTest(raw=raw),
                upstream(body=raw) as server,
                self.assertRaises(ChatError),
            ):
                collect("responses")
            self.assertEqual(len(server.requests), 1)

    def test_responses_incomplete_is_not_successful_stop(self):
        with upstream(body=responses([MESSAGE], status="incomplete")):
            events = collect("responses")
        self.assertEqual(events[-1]["reason"], "length")

    def test_anthropic_headers_usage_blocks_and_tool_result_roundtrip(self):
        blocks = [
            {
                "type": "thinking",
                "thinking": "思考摘要",
                "signature": "signature-unchanged",
            },
            {"type": "redacted_thinking", "data": "opaque-unchanged"},
            {"type": "tool_use", "id": "call_1", "name": "list_files", "input": {}},
        ]
        with upstream(body=anthropic(blocks, reason="tool_use")) as server:
            events = collect("anthropic", {**BODY, "tools": [TOOL]})
        first, headers, _ = server.requests[0]
        self.assertEqual(first, "POST /v1/messages HTTP/1.1")
        self.assertNotIn("authorization", headers)
        self.assertEqual(headers["x-api-key"], KEY)
        self.assertEqual(headers["anthropic-version"], "2023-06-01")
        self.assertEqual(
            next(e for e in events if e["type"] == "usage")["prompt_tokens"], 16
        )
        state = next(e["value"] for e in events if e["type"] == "replay")
        self.assertEqual(state, blocks)
        body = {
            **BODY,
            "tools": [TOOL],
            "messages": [
                {"role": "system", "content": "站点规则"},
                *BODY["messages"],
                {
                    "role": "assistant",
                    "content": "",
                    "_native": {"protocol": "anthropic", "value": state},
                },
                {"role": "tool", "tool_call_id": "call_1", "content": "result"},
            ],
        }
        wire = protocols.wire_body("anthropic", body)
        self.assertEqual(wire["system"], "站点规则")
        self.assertEqual(wire["messages"][1]["content"], blocks)
        self.assertEqual(
            wire["messages"][2],
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "call_1",
                        "content": "result",
                    }
                ],
            },
        )

    def test_anthropic_fragmented_text_signature_and_json(self):
        raw = event(
            "message_start", message={"role": "assistant", "usage": {"input_tokens": 1}}
        )
        raw += event(
            "content_block_start",
            index=0,
            content_block={"type": "thinking", "thinking": ""},
        )
        raw += event(
            "content_block_delta",
            index=0,
            delta={"type": "thinking_delta", "thinking": "公开🌍"},
        )
        raw += event(
            "content_block_delta",
            index=0,
            delta={"type": "signature_delta", "signature": "abc"},
        )
        raw += event("content_block_stop", index=0)
        raw += event(
            "content_block_start",
            index=1,
            content_block={
                "type": "tool_use",
                "id": "call_1",
                "name": "read_file",
                "input": {},
            },
        )
        raw += event(
            "content_block_delta",
            index=1,
            delta={"type": "input_json_delta", "partial_json": '{"line":'},
        )
        raw += event(
            "content_block_delta",
            index=1,
            delta={"type": "input_json_delta", "partial_json": "1}"},
        )
        raw += event("content_block_stop", index=1)
        raw += event(
            "message_delta",
            delta={"stop_reason": "tool_use"},
            usage={"output_tokens": 2},
        ) + event("message_stop")

        def script(handler):
            handler.wfile.write(
                b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nTransfer-Encoding: chunked\r\n\r\n"
            )
            for byte in raw:
                handler.wfile.write(b"1\r\n" + bytes([byte]) + b"\r\n")
            handler.wfile.write(b"0\r\n\r\n")
            handler.wfile.flush()

        with upstream(script=script):
            events = collect("anthropic", {**BODY, "tools": [TOOL]})
        state = next(e["value"] for e in events if e["type"] == "replay")
        self.assertEqual(state[0]["signature"], "abc")
        self.assertEqual(state[1]["input"], {"line": 1})
        self.assertEqual(
            next(e["text"] for e in events if e["type"] == "reasoning"), "公开🌍"
        )

    def test_anthropic_requires_signature_and_tool_lifecycle(self):
        invalid = [
            event("message_stop"),
            anthropic([{"type": "thinking", "thinking": "x", "signature": ""}]),
            anthropic(
                [
                    {
                        "type": "tool_use",
                        "id": "call_1",
                        "name": "list_files",
                        "input": {},
                    }
                ]
            ),
            anthropic().replace(event("content_block_stop", index=0), b""),
            anthropic().replace(event("message_stop"), b""),
        ]
        for raw in invalid:
            with (
                self.subTest(raw=raw),
                upstream(body=raw) as server,
                self.assertRaises(ChatError),
            ):
                collect("anthropic", {**BODY, "tools": [TOOL]})
            self.assertEqual(len(server.requests), 1)

    def test_request_validation_fails_before_network_and_never_forwards_extras(self):
        invalid = [
            ("responses", {"n": 2}),
            ("anthropic", {"n": 2}),
            ("responses", {"reasoning_effort": "high"}),
            ("chat_completions", {"reasoning": {"effort": "high"}}),
            ("anthropic", {"thinking": {"type": "enabled", "budget_tokens": 20}}),
            ("responses", {"reasoning": {"summary": "all-hidden"}}),
            ("responses", {"thinking": {"type": "adaptive"}}),
            ("chat_completions", {"stream_options": {"include_usage": 1}}),
            ("chat_completions", {"tools": [TOOL], "n": 2}),
            ("responses", {"headers": {"secret": KEY}}),
            ("responses", {"model": "bad\nname"}),
            (
                "responses",
                {
                    "messages": [
                        {
                            "role": "user",
                            "content": "x",
                            "_native": {"protocol": "responses", "value": [REASONING]},
                        }
                    ]
                },
            ),
            (
                "responses",
                {
                    "messages": [
                        {
                            "role": "assistant",
                            "content": "x",
                            "_native": {
                                "protocol": "anthropic",
                                "value": [{"type": "text", "text": "x"}],
                            },
                        }
                    ]
                },
            ),
            ("chat_completions", {"_window": WINDOW}),
        ]
        for protocol, updates in invalid:
            with (
                self.subTest(protocol=protocol, updates=updates),
                upstream() as server,
                self.assertRaises(ChatError) as error,
            ):
                collect(protocol, {**BODY, **updates})
            self.assertFalse(error.exception.uncertain)
            self.assertEqual(server.requests, [])

    def test_cancel_before_dispatch_and_bad_status_never_retry(self):
        for protocol in protocols.PROTOCOLS:
            abort = transport.AbortControl()
            abort.abort()
            with upstream() as server, self.assertRaises(ChatError):
                list(
                    transport.stream_model(
                        KEY, BODY, time.monotonic() + 2, abort, protocol=protocol
                    )
                )
            self.assertEqual(server.requests, [])
            for status in (302, 429, 500):
                with (
                    upstream(status=status, body=b"private-upstream-body") as server,
                    self.assertRaises(ChatError) as error,
                ):
                    collect(protocol)
                self.assertTrue(error.exception.uncertain)
                self.assertNotIn("private", str(error.exception))
                self.assertEqual(len(server.requests), 1)

    def test_reasoning_and_replay_size_bounds(self):
        with (
            patch.object(config, "REASONING_BYTES", 5),
            upstream(body=frame({"reasoning_content": "too long"})),
        ):
            with self.assertRaises(ChatError):
                collect("chat_completions")
        for state in (
            {"reasoning_content": "x", "other": "no"},
            {"reasoning_content": 1},
        ):
            with self.assertRaises(ChatError):
                protocols.validate_state("chat_completions", state)
        native = copy.deepcopy(REASONING)
        native["arbitrary_provider_field"] = "no"
        with self.assertRaises(ChatError):
            protocols.validate_state("responses", [native])


class NativeCompactTest(unittest.TestCase):
    def test_entire_compact_window_preserved_exactly_and_replayed(self):
        result = {
            "object": "response.compaction",
            "id": "resp_cmp",
            "output": WINDOW,
            "usage": {"input_tokens": 10, "output_tokens": 2, "total_tokens": 12},
        }
        with upstream(
            body=json.dumps(result, ensure_ascii=False).encode(),
            content_type="application/json",
        ) as server:
            compacted = transport.compact_model(
                KEY,
                {
                    "model": BODY["model"],
                    "input": [{"role": "user", "content": "历史原文"}],
                },
                time.monotonic() + 2,
                transport.AbortControl(),
            )
        self.assertEqual(server.requests[0][0], "POST /v1/responses/compact HTTP/1.1")
        self.assertEqual(server.requests[0][1]["accept"], "application/json")
        self.assertNotIn("stream", json.loads(server.requests[0][2]))
        self.assertEqual(compacted["output"], WINDOW)
        wire = protocols.wire_body(
            "responses", {**BODY, "_window": compacted["output"]}
        )
        self.assertEqual(wire["input"][:2], WINDOW)
        self.assertEqual(wire["input"][2], BODY["messages"][0])
        self.assertEqual(compacted["usage"]["total_tokens"], 12)

    def test_compact_invalid_output_truncated_duplicate_unknown_no_fallback(self):
        invalid = [
            b'{"object":"response.compaction","output":[]}',
            b'{"object":"response.compaction","output":[{"type":"compaction","encrypted_content":""}]}',
            b'{"object":"response.compaction","output":[{"type":"computer_call"}]}',
            b'{"object":"response.compaction","output":',
            b'{"object":"response.compaction","object":"response.compaction","output":[]}',
        ]
        for raw in invalid:
            with (
                upstream(body=raw, content_type="application/json") as server,
                self.assertRaises(ChatError) as error,
            ):
                transport.compact_model(
                    KEY,
                    {"model": BODY["model"], "input": BODY["messages"]},
                    time.monotonic() + 2,
                    transport.AbortControl(),
                )
            self.assertTrue(error.exception.uncertain)
            self.assertEqual(len(server.requests), 1)
            self.assertEqual(
                server.requests[0][0], "POST /v1/responses/compact HTTP/1.1"
            )

    def test_window_tool_pairs_and_message_annotations_kept_not_hidden_only(self):
        window = [
            REASONING,
            CALL,
            {"type": "function_call_output", "call_id": "call_1", "output": "[]"},
            MESSAGE,
            *WINDOW,
        ]
        self.assertEqual(protocols.validate_window(window), window)
        wire = protocols.wire_body(
            "responses",
            {
                **BODY,
                "messages": [
                    {"role": "system", "content": "最新规则"},
                    *BODY["messages"],
                ],
                "_window": window,
            },
        )
        self.assertEqual(wire["input"][0], {"role": "system", "content": "最新规则"})
        self.assertEqual(wire["input"][1:-1], window)


if __name__ == "__main__":
    unittest.main()
