"""TEST ONLY: v2 stream adapter over the existing real Flask/ChatService fixture.

Only the injected synthetic model stream changes. Identity (owner 12), real
routes, templates, storage, Memory CAS/audits, locks and loopback-only networking
are inherited unchanged from chat_fixture. No real credentials/model calls.
"""

import json
import time

import chat_fixture as base


FACT = "我长期偏好中文回复。"
OPAQUE = "OPAQUE_REPLAY_V2_CANDIDATE_"
OFF_ORIGIN = "https://untrusted-v2.invalid/not-an-image.png"


def candidate_text(index):
    return (
        f"v2-candidate-{index + 1} 独立正文。\n"
        f'<img src="{OFF_ORIGIN}" onerror="window.__v2Injected=true">\n'
        f"![不加载远程图片]({OFF_ORIGIN})"
    )


class V2BrowserStream:
    """Exactly one invocation per real runner _generate, even for native n=3."""

    def __init__(self):
        self.calls = 0

    def __call__(self, _key, body, deadline, abort):
        self.calls += 1
        abort.check()
        if time.monotonic() >= deadline:
            raise TimeoutError("Synthetic v2 stream deadline")
        messages = body["messages"]
        first_system = next(
            (m["content"] for m in messages if m.get("role") == "system"), ""
        )
        memory = first_system.startswith("You propose durable personal MEMORY facts")
        n = body.get("n", 1)
        user_messages = [m["content"] for m in messages if m.get("role") == "user"]
        assistants = [m for m in messages if m.get("role") == "assistant"]
        native_markers = [
            m.get("_native", {}).get("value", {}).get("reasoning_content")
            for m in assistants
            if isinstance(m.get("_native", {}).get("value"), dict)
        ]
        personal = "\n".join(
            m.get("content", "")
            for m in messages
            if m.get("role") == "user"
            and m.get("content", "").startswith("以下内容是本人设置")
        )
        phase = (
            "memory"
            if memory
            else "candidates"
            if n > 1
            else "continuation"
            if assistants
            else "main"
        )
        metadata = {
            "kind": "model_call",
            "number": self.calls,
            "phase": phase,
            "n": n,
            "candidate_count": n,
            "message_roles": [m["role"] for m in messages],
            "assistant_contents": [m["content"] for m in assistants],
            "native_markers": native_markers,
            "latest_user_text": None if memory else user_messages[-1],
            "manual_memory_present": base.MEMORY in personal,
            "managed_fact_present": FACT in personal,
        }
        if memory:
            payload = json.loads(user_messages[-1])
            source = payload["user_sources"][0]
            metadata.update(
                source_message_id=source["message_id"],
                source_fact=source["text"],
                manual_memory_present=base.MEMORY in payload["memory_content"],
            )
            result = {
                "v": 1,
                "operations": [
                    {
                        "op": "add",
                        "text": source["text"],
                        "sources": [
                            {
                                "message_id": source["message_id"],
                                "excerpt": source["text"],
                            }
                        ],
                    }
                ]
                if source["text"] == FACT
                else [],
            }
            base.emit(**metadata)
            yield {"type": "text", "text": json.dumps(result, ensure_ascii=False)}
            yield {"type": "finish", "reason": "stop"}
        else:
            base.emit(**metadata)
            # Interleave candidates: indexing, rather than event order, separates
            # their visible body, raw reasoning, summary and opaque replay state.
            for index in range(n):
                yield {
                    "type": "text",
                    "candidate_index": index,
                    "text": candidate_text(index)
                    if n > 1
                    else "v2-single-answer 合成普通回答。",
                }
                yield {
                    "type": "reasoning",
                    "candidate_index": index,
                    "kind": "raw",
                    "field": "reasoning_content",
                    "text": f"v2-raw-{index + 1} <script>window.__v2Injected=true</script> ![raw]({OFF_ORIGIN})",
                }
            time.sleep(0.02)
            abort.check()
            for index in reversed(range(n)):
                yield {
                    "type": "reasoning",
                    "candidate_index": index,
                    "kind": "summary",
                    "field": "reasoning",
                    "text": f'v2-summary-{index + 1} <svg onload="window.__v2Injected=true">仅合成公开摘要</svg>',
                }
                yield {
                    "type": "replay",
                    "candidate_index": index,
                    "protocol": "chat_completions",
                    "value": {"reasoning_content": OPAQUE + str(index + 1)},
                }
                yield {"type": "finish", "candidate_index": index, "reason": "stop"}
        yield {
            "type": "usage",
            "prompt_tokens": 30 if memory else 10,
            "completion_tokens": 7 if memory else 5 * n,
            "total_tokens": 37 if memory else 10 + 5 * n,
        }


if __name__ == "__main__":
    base.BrowserStream = V2BrowserStream
    base.main()
