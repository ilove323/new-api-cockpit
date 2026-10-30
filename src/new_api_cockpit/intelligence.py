"""Request-scoped, stateless SVG challenge using the current user's test KEY.

Only KEY creation/configuration uses the existing management audit. Results and
HTML are never written to a database, file, cache, queue, or background executor.
"""

from contextlib import closing, contextmanager
from functools import cache
from html.parser import HTMLParser
from http.client import HTTPConnection, HTTPSConnection, HTTPException
from importlib.resources import files
import json
import logging
import os
import re
import time
import traceback
from urllib.parse import urlsplit
from xml.etree import ElementTree

from . import balance, quota, user_management as management
from .locks import INTELLIGENCE_NAMESPACE
from .intelligence_stream import DrawingStream

PROMPT = "Generate an SVG of a pelican riding a bicycle"
KEY_NAME = "cockpit-智力测试专用"
MAX_HTML_BYTES = 512 * 1024
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
GENERATION_TIMEOUT = 300
TEXT_ENDPOINTS = {"openai", "openai-response", "anthropic", "gemini"}
ANTHROPIC_CHANNEL_TYPE = 14
PREVIEW_CSP = (
    "sandbox allow-scripts; default-src 'none'; script-src 'unsafe-inline'; "
    "style-src 'unsafe-inline'; img-src data: blob:; connect-src 'none'; "
    "object-src 'none'; base-uri 'none'; form-action 'none'; "
    "frame-ancestors 'self'"
)


class IntelligenceError(ValueError):
    status = 400
    uncertain = False


class Unavailable(IntelligenceError):
    status = 503


class GenerationError(IntelligenceError):
    status = 502
    uncertain = True


class GenerationTimeout(GenerationError):
    status = 504


def _invalid_constant(_value):
    raise ValueError("Non-finite JSON")


@contextmanager
def _response(path, credential, user_id, *, body=None, generation=False, headers=None):
    """Fixed internal origin, bounded reads/deadline, no redirects or retries."""
    base = urlsplit(os.environ.get("NEW_API_INTERNAL_URL", "http://new-api:3000"))
    if (
        base.scheme not in {"http", "https"}
        or not base.hostname
        or base.username is not None
        or base.query
        or base.fragment
        or not path.startswith("/")
        or path.startswith("//")
    ):
        raise Unavailable("New API 内部地址配置无效。")
    if (
        not isinstance(credential, str)
        or not credential
        or any(
            char.isspace() or ord(char) < 32 or ord(char) == 127 for char in credential
        )
    ):
        raise Unavailable("New API 调用凭据不可用。")
    timeout = GENERATION_TIMEOUT if generation else 10
    deadline = time.monotonic() + timeout
    error = GenerationError if generation else Unavailable
    connection = None
    try:
        connection_type = HTTPSConnection if base.scheme == "https" else HTTPConnection
        connection = connection_type(base.hostname, base.port, timeout=timeout)
        connection.request(
            "POST" if body is not None else "GET",
            base.path.rstrip("/") + path,
            body=json.dumps(body, ensure_ascii=False).encode()
            if body is not None
            else None,
            headers={
                "Authorization": "Bearer " + credential,
                "New-Api-User": str(user_id),
                "Content-Type": "application/json",
                "Accept": "application/json",
                **(headers or {}),
            },
        )
        sock = connection.sock
        sock.settimeout(max(0.01, deadline - time.monotonic()))
        with connection.getresponse() as response:
            if response.status != 200:
                raise error(f"New API 请求失败（HTTP {response.status}），未自动重试。")

            def chunks():
                size = 0
                expected_size = response.length
                # Do not touch a socket released by a complete read1().
                while not response.isclosed():
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError()
                    sock.settimeout(remaining)
                    chunk = response.read1(min(65536, MAX_RESPONSE_BYTES + 1 - size))
                    if time.monotonic() >= deadline:
                        raise TimeoutError()
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > MAX_RESPONSE_BYTES:
                        raise error("New API 响应过大，未自动重试。")
                    yield chunk
                if expected_size is not None and size != expected_size:
                    raise error("New API 响应不完整，未自动重试。")

            yield response, chunks()
    except TimeoutError:
        raise (
            GenerationTimeout("模型生成超时，可能已产生费用；未自动重试。")
            if generation
            else Unavailable("New API 查询超时，请稍后重新选择模型。")
        ) from None
    except (OSError, HTTPException, ValueError) as exc:
        if isinstance(exc, IntelligenceError):
            raise
        if generation:
            # A protocol decoder error is not necessarily a network failure.
            # Log only code location/type; exception text can contain upstream
            # response data, credentials or generated content.
            origin = traceback.extract_tb(exc.__traceback__)[-1]
            logging.getLogger(__name__).warning(
                "Intelligence upstream failure: type=%s errno=%s location=%s:%s:%s",
                type(exc).__name__,
                getattr(exc, "errno", None),
                os.path.basename(origin.filename),
                origin.name,
                origin.lineno,
            )
        raise error("New API 连接或响应异常，未自动重试。") from None
    finally:
        if connection is not None:
            connection.close()


def _json_result(chunks, error):
    result = json.loads(b"".join(chunks), parse_constant=_invalid_constant)
    if (
        not isinstance(result, dict)
        or result.get("error")
        or result.get("success") is False
    ):
        raise error("New API 拒绝请求，请检查模型、分组和账户余额；未自动重试。")
    return result


def upstream(path, credential, user_id, *, body=None, generation=False, headers=None):
    with _response(
        path, credential, user_id, body=body, generation=generation, headers=headers
    ) as (_, chunks):
        return _json_result(chunks, GenerationError if generation else Unavailable)


def stream_upstream(path, credential, user_id, *, body, endpoint, headers):
    """One paid request. A JSON-only upstream is consumed once, never replayed."""
    with _response(
        path,
        credential,
        user_id,
        body=body,
        generation=True,
        headers={
            **headers,
            "Accept": "text/event-stream",
            "Accept-Encoding": "identity",
        },
    ) as (response, chunks):
        content_type = (
            response.getheader("Content-Type", "").split(";", 1)[0].strip().lower()
        )
        if content_type == "text/event-stream":
            parser = DrawingStream(endpoint)
            try:
                for chunk in chunks:
                    yield from parser.feed(chunk)
                    if parser.done:
                        break
                parser.end()
            except (OSError, HTTPException, ValueError) as exc:
                if endpoint != "openai" or parser.reason is None:
                    raise
                # Keep consuming a normal usage/DONE tail for New API billing,
                # but a tail-only disconnect cannot undo a valid finish_reason.
                # Never infer completion from an apparently closed SVG alone.
                logging.getLogger(__name__).warning(
                    "Intelligence completed choice, accounting trailer unavailable: %s",
                    type(exc).__name__,
                )
            yield {"type": "finish", "reason": parser.reason}
        elif content_type == "application/json" or content_type.endswith("+json"):
            text, reason = generated_text(
                _json_result(chunks, GenerationError), endpoint
            )
            if text:
                yield {"type": "text", "text": text}
            yield {"type": "finish", "reason": reason}
        else:
            raise GenerationError("New API 返回的生成响应格式无效。")


def model_catalog(credential, user_id):
    """Use the authenticated catalog, independent of public pricing navigation."""
    entries = []
    for page in range(1, 21):
        result = upstream(
            f"/api/models/?include_channel_models=true&page={page}&page_size=100",
            credential,
            user_id,
        )
        data = result.get("data")
        if (
            result.get("success") is not True
            or not isinstance(data, dict)
            or not isinstance(data.get("items"), list)
            or len(data["items"]) > 100
            or type(data.get("total")) is not int
            or not 0 <= data["total"] <= 2000
            or data.get("page") != page
        ):
            raise Unavailable("New API 管理员模型目录响应无效。")
        entries.extend(data["items"])
        if len(entries) >= data["total"]:
            return entries
        if not data["items"]:
            break
    raise Unavailable("New API 模型目录过大或分页不完整，没有返回部分模型。")


def _operator(identity):
    operator = (
        management.actor(identity["username"])
        if identity["role"] >= 10
        else management.self_actor(identity)
    )
    if operator["id"] != identity["id"]:
        raise management.Forbidden("当前用户身份已变化，请重新登录。")
    return operator


def user_model_catalog(credential, user_id):
    """UserAuth endpoint includes models from the owner's usable groups."""
    result = upstream("/api/user/models", credential, user_id)
    names = result.get("data")
    if (
        result.get("success") is not True
        or not isinstance(names, list)
        or len(names) > 10000
        or any(not isinstance(name, str) for name in names)
    ):
        raise Unavailable("New API 本人模型列表响应无效。")
    # This endpoint supplies names, not endpoint metadata. Do not fabricate
    # declared protocols or borrow an administrator's catalog credential.
    return [{"model_name": name} for name in sorted(set(names))]


def model_options(identity, credential):
    """Read-only discovery: opening this page must never provision a PAT/KEY."""
    operator = _operator(identity)
    is_admin = operator["role"] >= 10
    user = management.target(operator, operator["id"], enabled=True)
    allowed = set(management.selectable_groups(operator, user))
    with quota.connect() as conn:
        live = {}
        for channel in conn.execute(
            'SELECT id,type,models,"group" FROM channels WHERE status=1 '
            "ORDER BY COALESCE(priority,0) DESC,id"
        ):
            groups = {v.strip() for v in (channel["group"] or "").split(",")} & allowed
            for model in (channel["models"] or "").split(","):
                model = model.strip()
                if model and len(model) <= 256 and not any(ord(c) < 32 for c in model):
                    routes = live.setdefault(model, {})
                    for group in groups:
                        # Stable protocol reference; only admins pin it at inference.
                        routes.setdefault(group, channel)
    rows = {}
    catalog = model_catalog if is_admin else user_model_catalog
    for entry in catalog(credential, operator["id"]):
        if not isinstance(entry, dict):
            continue
        names = (
            entry.get("matched_models")
            if entry.get("name_rule") in (1, 2, 3)
            else [entry.get("model_name")]
        )
        types = entry.get("supported_endpoints")
        if not isinstance(names, list):
            continue
        if is_admin and (
            not isinstance(types, list)
            or not any(isinstance(v, str) and v in TEXT_ENDPOINTS for v in types)
        ):
            continue
        for name in names:
            routes = live.get(name, {}) if isinstance(name, str) else {}
            if routes:
                ordered = sorted(
                    routes, key=lambda value: (value != user["group"], value)
                )
                channel = routes[ordered[0]]
                rows[name] = dict(
                    model=name,
                    group=ordered[0],
                    available_groups=ordered,
                    channel_id=channel["id"],
                    channel_type=channel["type"],
                    endpoint="anthropic"
                    if channel["type"] == ANTHROPIC_CHANNEL_TYPE
                    else "openai",
                )
    return {
        "rows": [rows[name] for name in sorted(rows)],
        "key_name": KEY_NAME,
        "prompt": PROMPT,
    }


@contextmanager
def exclusive_test(user_id):
    """Session lock spans inference across Gunicorn processes, without a new table."""
    management._audit_ready()
    with balance.connect() as conn:
        conn.autocommit = True
        lock = (INTELLIGENCE_NAMESPACE, str(user_id))
        if not conn.execute(
            "SELECT pg_try_advisory_lock(%s,hashtext(%s)) AS acquired", lock
        ).fetchone()["acquired"]:
            raise management.Conflict("当前用户已有测试正在执行，请等它结束后再测试。")
        try:
            yield
        finally:
            conn.execute("SELECT pg_advisory_unlock(%s,hashtext(%s))", lock)


def _find_token(user_id):
    with quota.connect() as conn:
        rows = conn.execute(
            "SELECT id FROM tokens WHERE user_id=%s AND name=%s AND deleted_at IS NULL ORDER BY id LIMIT 2",
            (user_id, KEY_NAME),
        ).fetchall()
    if len(rows) > 1:
        raise management.Conflict(
            "存在多个同名测试 KEY，请在 New API 令牌页面保留一个后再测试。"
        )
    return rows[0]["id"] if rows else None


def _usable_token(token):
    if not isinstance(token, dict) or token.get("status") != 1:
        raise management.Conflict(
            "测试 KEY 已禁用，请在 New API 令牌页面启用后再测试。"
        )
    expiry = token.get("expired_time")
    if type(expiry) is not int or (expiry != -1 and expiry <= time.time()):
        raise management.Conflict(
            "测试 KEY 已过期，请在 New API 令牌页面调整后再测试。"
        )
    if not token.get("unlimited_quota") and not (token.get("remain_quota", 0) > 0):
        raise management.Conflict(
            "测试 KEY 额度不足，请在 New API 令牌页面调整后再测试。"
        )


def test_key(operator, choice, *, credential=None):
    """Keep the dedicated KEY; preserve existing quota/expiry/status/IP settings."""
    token_id = _find_token(operator["id"])
    changes = dict(
        group=choice["group"],
        model_limits_enabled=True,
        model_limits=choice["model"],
        cross_group_retry=False,
        auto_groups=[],
    )

    def write(object_id, body):
        if operator["role"] >= 10:
            return management.single_action(
                operator["username"], "token", object_id, body
            )
        return management.self_token_action(operator, object_id, body, credential)

    def read(path, **kwargs):
        if operator["role"] < 10:
            if not credential:
                raise management.Forbidden("缺少当前用户登录会话。")
            kwargs["credential"] = credential
        return management.call_api(operator, operator["id"], path, **kwargs)

    def owned(token):
        if (
            not isinstance(token, dict)
            or token.get("id") != token_id
            or token.get("user_id") != operator["id"]
        ):
            raise management.Forbidden("测试 KEY 不属于当前用户。")
        _usable_token(token)
        return token

    if token_id is None:
        write(
            operator["id"],
            {
                "action": "create",
                "changes": {
                    **changes,
                    "name": KEY_NAME,
                    "expired_time": -1,
                    "unlimited_quota": True,
                },
            },
        )
        token_id = _find_token(operator["id"])
        if token_id is None:
            raise management.Uncertain(
                "测试 KEY 创建结果无法确认，请在 New API 令牌页面核对；未自动重试。"
            )
    token = owned(read(f"/api/token/{token_id}"))
    if token.get("name") != KEY_NAME:
        raise management.Conflict("测试 KEY 已被重命名，请重新核对；尚未发起模型请求。")
    if any(
        token.get(key) != value
        for key, value in changes.items()
        if key != "auto_groups"
    ) or token.get("auto_groups"):
        write(token_id, {"action": "edit", "changes": changes})
    token = owned(read(f"/api/token/{token_id}"))
    if (
        token.get("name") != KEY_NAME
        or any(
            token.get(key) != value
            for key, value in changes.items()
            if key != "auto_groups"
        )
        or token.get("auto_groups")
    ):
        raise management.Conflict("测试 KEY 配置已变化，请重新核对；尚未发起模型请求。")
    data = read(f"/api/token/{token_id}/key", method="POST", body={})
    key = data.get("key") if isinstance(data, dict) else None
    # Official /key returns the stored key without sk-. Accept either form,
    # but never a masked key or a caller-supplied channel suffix.
    raw = key.removeprefix("sk-") if isinstance(key, str) else ""
    if not re.fullmatch(r"[A-Za-z0-9]{1,128}", raw):
        raise Unavailable("测试 KEY 响应无效。")
    return "sk-" + raw


def generation_request(choice):
    model, endpoint = choice["model"], choice["endpoint"]
    if endpoint == "openai":
        return (
            "/v1/chat/completions",
            dict(
                model=model,
                messages=[{"role": "user", "content": PROMPT}],
                stream=True,
            ),
            {},
        )
    if endpoint == "anthropic":
        return (
            "/v1/messages",
            dict(
                model=model,
                messages=[{"role": "user", "content": PROMPT}],
                stream=True,
            ),
            {"anthropic-version": "2023-06-01"},
        )
    raise Unavailable("测试协议配置无效；尚未发起生成。")


def generated_text(result, endpoint):
    """Do not render reasoning, tool calls, refusals, or partial output as HTML."""

    def blocks(items):
        return (
            "".join(
                item["text"]
                for item in items
                if isinstance(item, dict)
                and isinstance(item.get("text"), str)
                and item.get("type") in {"text", "output_text"}
            )
            if isinstance(items, list)
            else ""
        )

    if endpoint == "openai":
        choices = result.get("choices")
        item = (
            choices[0]
            if isinstance(choices, list) and choices and isinstance(choices[0], dict)
            else {}
        )
        message = item.get("message") or {}
        content = message.get("content") if isinstance(message, dict) else None
        reason = item.get("finish_reason")
        return content if isinstance(content, str) else blocks(content), (
            reason
            if isinstance(reason, str)
            and reason in {"length", "content_filter", "tool_calls", "function_call"}
            else None
        )
    if endpoint == "anthropic":
        reason = result.get("stop_reason")
        return blocks(result.get("content")), (
            reason
            if isinstance(reason, str)
            and reason
            in {
                "max_tokens",
                "model_context_window_exceeded",
                "refusal",
                "pause_turn",
                "tool_use",
            }
            else None
        )
    raise GenerationError("模型响应的测试协议无效，未自动重试。")


class DocumentCheck(HTMLParser):
    def __init__(self):
        super().__init__()
        self.html = self.closed = self.svg = False

    def handle_starttag(self, tag, attrs):
        if tag == "html":
            self.html = True
        if tag == "svg":
            self.svg = True

    def handle_endtag(self, tag):
        if tag == "html":
            self.closed = True


def extract_drawing(text):
    """Return the sandbox document and an optional standalone SVG image.

    An SVG image needs its XML namespace. Namespace-less inline SVG and full
    HTML keep using the HTTP sandbox; never rewrite the model's drawing.
    """
    if not isinstance(text, str) or not text.strip():
        raise IntelligenceError("模型没有返回可用的 SVG。")
    if len(text.encode()) > MAX_HTML_BYTES:
        raise IntelligenceError("生成内容超过预览大小限制。")
    text = text.strip()
    fence = re.fullmatch(r"```(?:html|svg|xml)?\s*\n(.*?)\n```", text, re.S | re.I)
    if fence is None:
        # Models often introduce the drawing before its Markdown code block.
        # Extract one closed document block, not arbitrary <svg> substrings or
        # an ambiguous choice between multiple drawings. Keep source unchanged.
        blocks = list(
            re.finditer(
                r"^[ \t]*```(?:html|svg|xml)?[ \t]*\r?\n(.*?)\r?\n[ \t]*```[ \t]*\r?$",
                text,
                re.S | re.I | re.M,
            )
        )
        if len(blocks) == 1:
            fence = blocks[0]
    html = fence[1].strip() if fence else text
    check = DocumentCheck()
    check.feed(html)
    if check.html and check.closed and check.svg:
        return html, None
    # The original benchmark asks for SVG, not HTML. Wrap only a complete SVG;
    # do not repair a drawing, execute it, or expand XML entities on the server.
    if not re.search(r"<!\s*(?:DOCTYPE|ENTITY)", html, re.I):
        try:
            root = ElementTree.fromstring(html)
            if root.tag in {"svg", "{http://www.w3.org/2000/svg}svg"}:
                document = (
                    '<!doctype html><html><head><meta charset="utf-8">'
                    "<style>body{margin:0;min-height:100vh;display:grid;place-items:center}"
                    "svg{max-width:100%;max-height:100vh}</style></head><body>"
                    + html
                    + "</body></html>"
                )
                if len(document.encode()) > MAX_HTML_BYTES:
                    raise IntelligenceError("生成内容超过预览大小限制。")
                return document, (
                    html if root.tag == "{http://www.w3.org/2000/svg}svg" else None
                )
        except ElementTree.ParseError:
            pass
    raise IntelligenceError("未能从模型输出中提取完整 SVG；可切换源码查看原始输出。")


def extract_html(text):
    return extract_drawing(text)[0]


@cache
def _preview_script():
    """Process-local packaged asset, never generated HTML or other user data."""
    return files(__package__).joinpath("static/intelligence-preview.js").read_text()


def preview_document(text):
    """Measure only inside the opaque sandbox; never expose its DOM to the parent."""
    return extract_html(text) + "\n<script>" + _preview_script() + "</script>"


def run_events(identity, credential, body):
    if not isinstance(body, dict) or set(body) != {"model"}:
        raise IntelligenceError(
            "只需提供一个模型，不允许指定用户、分组、KEY 或提示词。"
        )
    model = body.get("model")
    if (
        not isinstance(model, str)
        or not model
        or len(model) > 256
        or "," in model
        or any(ord(c) < 32 for c in model)
    ):
        raise IntelligenceError("请选择有效的模型。")
    with exclusive_test(identity["id"]):
        yield {"type": "status", "stage": "checking"}
        choices = model_options(identity, credential)["rows"]
        choice = next((row for row in choices if row["model"] == model), None)
        if choice is None:
            raise IntelligenceError("该模型没有当前用户可用的启用渠道或文本生成接口。")
        operator = _operator(identity)
        is_admin = operator["role"] >= 10
        yield {"type": "status", "stage": "key"}
        key = test_key(
            operator, choice, **({"credential": credential} if not is_admin else {})
        )
        available = upstream("/v1/models", key, operator["id"])
        if not isinstance(available.get("data"), list) or not any(
            isinstance(row, dict) and row.get("id") == model
            for row in available["data"]
        ):
            raise IntelligenceError(
                "测试 KEY 当前不能调用该模型，请核对计费配置和分组；尚未发起生成。"
            )
        path, payload, headers = generation_request(choice)
        # New API administrators can pin a channel using sk-<key>-<channel_id>.
        # Keep protocol and routing together, without changing the stored KEY.
        # Ordinary owners cannot use the administrator channel suffix. Their
        # requests retain New API's normal routing, still within the chosen group.
        channel_key = f"{key}-{choice['channel_id']}" if is_admin else key
        if choice["endpoint"] == "anthropic":
            headers["x-api-key"] = channel_key
        start = time.monotonic()
        yield {"type": "status", "stage": "waiting"}
        parts, size, oversized, stop_reason = [], 0, False, None
        events = stream_upstream(
            path,
            channel_key,
            operator["id"],
            body=payload,
            headers=headers,
            endpoint=choice["endpoint"],
        )
        # Close resources when this request ends or notices a disconnect; a
        # blocking upstream read is still bounded by the generation deadline.
        with closing(events):
            for event in events:
                if event["type"] == "text":
                    if oversized:
                        continue
                    encoded = event["text"].encode()
                    room = MAX_HTML_BYTES - size
                    oversized |= len(encoded) > room
                    text = encoded[:room].decode("utf-8", errors="ignore")
                    if text:
                        parts.append(text)
                        size += len(text.encode())
                        yield {"type": "text", "text": text}
                elif event["type"] == "finish":
                    stop_reason = event["reason"]
                else:
                    yield event
        elapsed = round((time.monotonic() - start) * 1000)
        text = "".join(parts)
        warning, html, svg = "", None, None
        if oversized:
            warning = "生成内容过大，只显示部分源码，不预览。"
        elif stop_reason not in {None, "stop", "end_turn", "stop_sequence"}:
            reasons = {
                "length": "模型达到 New API 或上游的输出上限",
                "max_tokens": "模型达到 New API 或上游的输出上限",
                "model_context_window_exceeded": "模型达到上下文窗口上限",
                "content_filter": "输出被上游内容规则拦截",
                "refusal": "模型拒绝了本次生成",
                "pause_turn": "上游暂停了本次生成",
                "tool_calls": "模型返回了工具调用，而不是 SVG",
                "function_call": "模型返回了工具调用，而不是 SVG",
                "tool_use": "模型返回了工具调用，而不是 SVG",
            }
            warning = (
                reasons.get(stop_reason, "模型未正常完成生成")
                + f"（{stop_reason}），不预览；未自动重试。"
            )
        else:
            try:
                html, svg = extract_drawing(text)
            except IntelligenceError as exc:
                warning = str(exc)
        result = dict(
            model=model,
            group=choice["group"],
            endpoint=choice["endpoint"],
            channel_id=choice["channel_id"] if is_admin else None,
            channel_type=choice["channel_type"] if is_admin else None,
            key_name=KEY_NAME,
            elapsed_ms=elapsed,
            html=html,
            svg=svg,
            source=text,
            warning=warning,
        )
    yield {"type": "result", "result": result}


def run(identity, credential, body):
    """JSON clients use exactly the same streaming model call, collected once."""
    with closing(run_events(identity, credential, body)) as events:
        for event in events:
            if event["type"] == "result":
                return event["result"]
