"""Drawing-stream regressions: local fixtures only, never call a real model."""

from contextlib import contextmanager, nullcontext
import json
import threading
import time
import unittest
from unittest.mock import patch

from new_api_cockpit import intelligence as intel
from new_api_cockpit.app import app
from new_api_cockpit.intelligence_stream import DrawingStream
from test_intelligence import ACTOR, CHOICE, KEY, MEMBER
from test_intelligence_transport import BODY, RESULT, chunked, respond, upstream

SVG = '<svg xmlns="http://www.w3.org/2000/svg"><text>鹈鹕</text></svg>'


def sse(value, newline="\n"):
    data = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    return ("data: " + data + newline * 2).encode()


def openai_wire(reason="stop"):
    return b"".join(
        [
            b"\xef\xbb\xbf: keepalive\r\n\r\n",
            sse(
                {
                    "choices": [
                        {
                            "index": 0,
                            "delta": {"reasoning_content": "fixture-private-reasoning"},
                        }
                    ]
                },
                "\r\n",
            ),
            sse({"choices": [{"index": 0, "delta": {"content": SVG}}]}, "\r\n"),
            sse(
                {"choices": [{"index": 0, "delta": {}, "finish_reason": reason}]},
                "\r\n",
            ),
            sse("[DONE]", "\r\n"),
        ]
    )


def anthropic_wire(reason="end_turn"):
    return b"".join(
        sse(event, "\r")
        for event in [
            {"type": "message_start", "message": {"role": "assistant"}},
            {"type": "ping"},
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "thinking"},
            },
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {
                    "type": "thinking_delta",
                    "thinking": "fixture-private-reasoning",
                },
            },
            {"type": "content_block_stop", "index": 0},
            {
                "type": "content_block_start",
                "index": 1,
                "content_block": {"type": "text", "text": SVG[:12]},
            },
            {
                "type": "content_block_delta",
                "index": 1,
                "delta": {"type": "text_delta", "text": SVG[12:]},
            },
            {"type": "content_block_stop", "index": 1},
            {"type": "message_delta", "delta": {"stop_reason": reason}},
            {"type": "message_stop"},
        ]
    )


def drawing_request(endpoint="openai"):
    path, body, headers = intel.generation_request({**CHOICE, "endpoint": endpoint})
    return intel.stream_upstream(
        path, KEY, 1, body=body, endpoint=endpoint, headers=headers
    )


@contextmanager
def prepared(events):
    with (
        patch.object(intel, "exclusive_test", return_value=nullcontext()),
        patch.object(intel, "model_options", return_value={"rows": [CHOICE]}),
        patch.object(intel.management, "actor", return_value=ACTOR),
        patch.object(intel, "test_key", return_value=KEY),
        patch.object(intel, "upstream", return_value={"data": [{"id": "model-a"}]}),
        patch.object(intel, "stream_upstream", return_value=events) as generation,
    ):
        yield generation


class DrawingDecoderTest(unittest.TestCase):
    def test_both_protocols_across_all_chunk_boundaries_and_line_endings(self):
        for endpoint, wire, reason in [
            ("openai", openai_wire(), "stop"),
            ("anthropic", anthropic_wire(), "end_turn"),
        ]:
            for size in (1, 2, 7, len(wire)):
                with self.subTest(endpoint=endpoint, size=size):
                    decoder, events = DrawingStream(endpoint), []
                    for start in range(0, len(wire), size):
                        events.extend(decoder.feed(wire[start : start + size]))
                    decoder.end()
                    self.assertTrue(decoder.done)
                    self.assertEqual(decoder.reason, reason)
                    self.assertEqual(
                        "".join(e["text"] for e in events if e["type"] == "text"), SVG
                    )
                    self.assertIn({"type": "status", "stage": "thinking"}, events)
                    self.assertNotIn("fixture-private-reasoning", json.dumps(events))

    def test_multiline_data_and_unknown_sse_fields_do_not_break_valid_json(self):
        decoder = DrawingStream("openai")
        wire = (
            b'id: ignored\r\nevent: ignored\r\ndata: {"choices":\r\ndata: [{"index":0,"delta":{"content":"<svg/>"},"finish_reason":"stop"}]}\r\n\r\n'
            + sse("[DONE]")
        )
        events = list(decoder.feed(wire))
        decoder.end()
        self.assertEqual(events, [{"type": "text", "text": "<svg/>"}])

    def test_missing_terminal_or_finish_malformed_or_error_frame_never_succeeds(self):
        cases = [
            ("openai", openai_wire().rsplit(b"data: [DONE]", 1)[0]),
            ("openai", sse("[DONE]")),
            ("openai", b"data: {bad-json}\n\n"),
            ("openai", sse({"error": {"message": "fixture-private-error"}})),
            ("openai", sse({"choices": [{"index": 0, "delta": {"content": 2}}]})),
            (
                "anthropic",
                anthropic_wire().rsplit(b'data: {"type": "message_stop"}', 1)[0],
            ),
            ("anthropic", sse({"type": "message_stop"})),
            (
                "anthropic",
                sse({"type": "error", "error": {"message": "fixture-private-error"}}),
            ),
        ]
        for endpoint, wire in cases:
            with (
                self.subTest(endpoint=endpoint, size=len(wire)),
                self.assertRaises(ValueError),
            ):
                decoder = DrawingStream(endpoint)
                list(decoder.feed(wire))
                decoder.end()


class DrawingTransportTest(unittest.TestCase):
    def test_streaming_http_framings_for_both_protocols(self):
        for endpoint, body in [
            ("openai", openai_wire()),
            ("anthropic", anthropic_wire()),
        ]:
            for framing in ("length", "chunked", "eof"):
                wire = chunked(body) if framing == "chunked" else body
                with (
                    self.subTest(endpoint=endpoint, framing=framing),
                    upstream(
                        respond(wire, framing, content_type="text/event-stream")
                    ) as attempts,
                ):
                    events = list(drawing_request(endpoint))
                    self.assertEqual(
                        "".join(e["text"] for e in events if e["type"] == "text"), SVG
                    )
                    self.assertEqual(events[-1]["type"], "finish")
                    self.assertEqual(len(attempts), 1)

    def test_first_delta_is_delivered_before_upstream_finishes_and_socket_closes(self):
        finish = threading.Event()
        first = sse({"choices": [{"index": 0, "delta": {"content": "<svg>"}}]})
        last = sse(
            {
                "choices": [
                    {
                        "index": 0,
                        "delta": {"content": "</svg>"},
                        "finish_reason": "stop",
                    }
                ]
            }
        ) + sse("[DONE]")

        def reply(handler):
            self.assertEqual(handler.headers["Accept"], "text/event-stream")
            handler.send_response(200)
            handler.send_header("Content-Type", "text/event-stream")
            handler.send_header("Connection", "close")
            handler.end_headers()
            handler.wfile.write(first)
            handler.wfile.flush()
            finish.wait(2)
            handler.wfile.write(last)
            handler.wfile.flush()

        with upstream(reply) as attempts:
            events = drawing_request()
            started = time.monotonic()
            try:
                self.assertEqual(next(events), {"type": "text", "text": "<svg>"})
                self.assertLess(time.monotonic() - started, 1)
                finish.set()
                self.assertEqual(list(events)[-1], {"type": "finish", "reason": "stop"})
            finally:
                finish.set()
                events.close()
            self.assertEqual(len(attempts), 1)

    def test_json_only_upstream_returns_once_without_a_second_paid_call(self):
        with upstream(respond(BODY)) as attempts:
            events = list(drawing_request())
            self.assertEqual(
                events,
                [
                    {
                        "type": "text",
                        "text": RESULT["choices"][0]["message"]["content"],
                    },
                    {"type": "finish", "reason": None},
                ],
            )
            self.assertEqual(len(attempts), 1)

    def test_timeout_incomplete_stream_and_error_responses_never_retry(self):
        def drip(handler):
            handler.send_response(200)
            handler.send_header("Content-Type", "text/event-stream")
            handler.send_header("Connection", "close")
            handler.end_headers()
            for _ in range(80):
                handler.wfile.write(b": ping\n\n")
                handler.wfile.flush()
                time.sleep(0.01)

        for reply, error in [
            (drip, intel.GenerationTimeout),
            (
                respond(b"data: {}\n\n", content_type="text/event-stream"),
                intel.GenerationError,
            ),
            (respond(b"fixture-private-error", status=429), intel.GenerationError),
        ]:
            with (
                upstream(reply) as attempts,
                patch.object(intel, "GENERATION_TIMEOUT", 0.12),
                self.assertRaises(error) as caught,
            ):
                list(drawing_request())
            self.assertNotIn("fixture-private-error", str(caught.exception))
            self.assertEqual(len(attempts), 1)


class DrawingEventsTest(unittest.TestCase):
    def test_prose_and_fenced_svg_preview_without_changing_source_or_repeating_call(
        self,
    ):
        source = (
            "I’ll create the drawing. Save as `pelican.svg`:\n\n```svg\n"
            + SVG
            + "\n```"
        )

        def upstream_events():
            for part in (source[:30], source[30:]):
                yield {"type": "text", "text": part}
            yield {"type": "finish", "reason": "stop"}

        with prepared(upstream_events()) as generation:
            events = list(intel.run_events(ACTOR, "fixture-pat", {"model": "model-a"}))
        result = events[-1]["result"]
        self.assertEqual(result["source"], source)
        self.assertEqual(result["warning"], "")
        self.assertIn(SVG, result["html"])
        self.assertNotIn("I’ll create", result["html"])
        self.assertEqual(
            "".join(e["text"] for e in events if e["type"] == "text"), source
        )
        generation.assert_called_once()

    def test_http_disconnect_closes_generation_and_releases_the_user_lock(self):
        closed = []

        @contextmanager
        def lock(_user):
            try:
                yield
            finally:
                closed.append("lock")

        def upstream_events():
            try:
                yield {"type": "text", "text": "<svg>"}
                self.fail("Disconnected HTTP response must not resume this model call")
            finally:
                closed.append("upstream")

        with (
            prepared(upstream_events()) as generation,
            patch.object(intel, "exclusive_test", side_effect=lock),
            patch("new_api_cockpit.app.request_identity", return_value=ACTOR),
        ):
            response = app.test_client().post(
                "/cockpit/api/intelligence/test",
                json={"model": "model-a"},
                headers={"Accept": "text/event-stream", "X-Intelligence-Request": "1"},
                buffered=False,
            )
            for chunk in response.response:
                if b"event: text" in chunk:
                    break
            response.close()
            self.assertEqual(generation.call_count, 1)
        self.assertEqual(closed, ["upstream", "lock"])

    def test_streamed_oversized_source_is_a_prefix_and_never_previews(self):
        def upstream_events():
            yield {"type": "text", "text": "<svg>" + "鹈鹕" * 10}
            yield {"type": "text", "text": "must-not-fill-truncated-utf8"}
            yield {"type": "finish", "reason": "stop"}

        with prepared(upstream_events()), patch.object(intel, "MAX_HTML_BYTES", 7):
            result = intel.run(ACTOR, "fixture-pat", {"model": "model-a"})
        self.assertEqual(result["source"], "<svg>")
        self.assertIsNone(result["html"])
        self.assertIn("生成内容过大", result["warning"])

    def test_run_streams_original_svg_and_unlocks_before_final_result(self):
        state = []

        @contextmanager
        def lock(_user):
            state.append("locked")
            try:
                yield
            finally:
                state.append("unlocked")

        def upstream_events():
            yield {"type": "text", "text": SVG}
            yield {"type": "finish", "reason": "stop"}

        with (
            prepared(upstream_events()) as generation,
            patch.object(intel, "exclusive_test", side_effect=lock),
        ):
            events = intel.run_events(ACTOR, "fixture-pat", {"model": "model-a"})
            seen = list(events)
        self.assertEqual(state, ["locked", "unlocked"])
        self.assertEqual(
            [e["stage"] for e in seen if e["type"] == "status"],
            ["checking", "key", "waiting"],
        )
        result = seen[-1]["result"]
        self.assertEqual(result["source"], SVG)
        self.assertIn(SVG, result["html"])
        self.assertTrue(generation.call_args.kwargs["body"]["stream"])
        self.assertNotIn("fixture-pat", json.dumps(seen))
        self.assertNotIn(KEY, json.dumps(seen))

    def test_close_midstream_closes_upstream_and_releases_lock_without_retry(self):
        closed = []

        @contextmanager
        def lock(_user):
            try:
                yield
            finally:
                closed.append("lock")

        def upstream_events():
            try:
                yield {"type": "text", "text": "<svg>"}
                self.fail("Closing the response must not continue generation")
            finally:
                closed.append("upstream")

        with (
            prepared(upstream_events()) as generation,
            patch.object(intel, "exclusive_test", side_effect=lock),
        ):
            events = intel.run_events(ACTOR, "fixture-pat", {"model": "model-a"})
            while next(events)["type"] != "text":
                pass
            events.close()
        self.assertEqual(closed, ["upstream", "lock"])
        self.assertEqual(generation.call_count, 1)

    def test_stream_response_status_errors_no_cache_and_close(self):
        headers = {"Accept": "text/event-stream", "X-Intelligence-Request": "1"}
        client = app.test_client()
        closed = []

        def events(*_args):
            try:
                yield {"type": "status", "stage": "checking"}
                yield {"type": "text", "text": "<svg>"}
                raise intel.GenerationTimeout("模型生成超时")
            finally:
                closed.append(True)

        with (
            patch("new_api_cockpit.app.request_identity", return_value=MEMBER),
            patch.object(intel, "run_events", side_effect=events) as call,
        ):
            response = client.post(
                "/cockpit/api/intelligence/test",
                json={"model": "model-a"},
                headers=headers,
            )
            self.assertEqual(response.mimetype, "text/event-stream")
            self.assertEqual(response.headers["Cache-Control"], "no-store")
            self.assertEqual(response.headers["X-Accel-Buffering"], "no")
            body = response.get_data(as_text=True)
            values = [
                json.loads(line[6:])
                for line in body.splitlines()
                if line.startswith("data: ")
            ]
            self.assertEqual([v["type"] for v in values], ["status", "text", "error"])
            self.assertEqual(values[-1]["status"], 504)
            self.assertTrue(values[-1]["uncertain"])
            self.assertEqual(closed, [True])
            self.assertEqual(call.call_count, 1)

        with (
            patch("new_api_cockpit.app.request_identity", return_value=MEMBER),
            patch.object(
                intel, "exclusive_test", side_effect=intel.management.Conflict("busy")
            ),
        ):
            self.assertEqual(
                client.post(
                    "/cockpit/api/intelligence/test",
                    json={"model": "model-a"},
                    headers=headers,
                ).status_code,
                409,
            )
            self.assertEqual(
                client.post(
                    "/cockpit/api/intelligence/test",
                    json={"model": "model-a", "group": "x"},
                    headers=headers,
                ).status_code,
                400,
            )
