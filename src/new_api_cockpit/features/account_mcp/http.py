"""A small stateless MCP 2025 Streamable HTTP endpoint (JSON responses).

No browser-cookie authentication, sessions, credential storage, OAuth
impersonation, sampling, model calls, or arbitrary upstream forwarding.
"""

import json
import os
import re
from urllib.parse import urlsplit

from flask import Blueprint, jsonify, make_response, request
from werkzeug.exceptions import BadRequest, RequestEntityTooLarge

from . import keys, service, upstream

ENDPOINT = "account_mcp.endpoint"
VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26")
INFO = {"name": "new-api-cockpit-account", "version": "1.1.0"}
ANNOTATIONS = {
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": False,
}
TOOLS = [
    {
        "name": "get_balance",
        "description": "查询当前用户自己的账户余额、累计消费和累计请求数。仅使用用户 API 访问令牌 PAT，不是模型 sk- KEY。",
        "inputSchema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
        "annotations": ANNOTATIONS,
    },
    {
        "name": "get_usage",
        "description": "查询本人消费请求数、Token 用量、消费金额和按模型汇总。默认北京时间今天到现在；自定义区间最长 31 天，start 包含、end 不含。金额是 Cockpit 账面单位，非自动换汇结果。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "start": {
                    "type": "string",
                    "maxLength": 40,
                    "description": "ISO 日期（北京时间零点）或带时区的 ISO 时间；须同时传 end",
                },
                "end": {
                    "type": "string",
                    "maxLength": 40,
                    "description": "不包含的结束时刻，格式同 start，不能晚于现在",
                },
            },
            "additionalProperties": False,
        },
        "annotations": ANNOTATIONS,
    },
] + keys.TOOLS


def rpc_error(code, message, ident=None, status=200):
    return jsonify(
        jsonrpc="2.0", id=ident, error={"code": code, "message": message}
    ), status


def account_error(exc, ident=None):
    response = make_response(*rpc_error(-32001, str(exc), ident, exc.status))
    if exc.status == 401:
        response.headers["WWW-Authenticate"] = 'Bearer realm="cockpit-account-mcp"'
    if exc.status == 429 and exc.retry_after is not None:
        response.headers["Retry-After"] = exc.retry_after
    return response


def credential():
    authorization = request.headers.get("Authorization", "")
    scheme, _, value = authorization.partition(" ")
    if (
        scheme.lower() != "bearer"
        or not 16 <= len(value) <= 256
        or not re.fullmatch(r"[A-Za-z0-9_+/=-]+", value)
        or value.startswith("sk-")
    ):
        raise upstream.AccountError(
            "请使用用户 API 访问令牌 PAT；不接受模型 KEY、登录 JWT 或浏览器 Cookie。",
            401,
        )
    return value


def valid_origin():
    if request.headers.get("Sec-Fetch-Site") == "cross-site":
        return False
    origin = request.headers.get("Origin")
    if origin is None:
        return True
    try:
        parsed = urlsplit(origin)
        secure = os.environ.get("COCKPIT_COOKIE_SECURE", "false").lower() == "true"
        return (
            parsed.scheme == ("https" if secure else request.scheme)
            and parsed.netloc == request.host
            and not parsed.username
            and parsed.path in {"", "/"}
            and not parsed.query
            and not parsed.fragment
        )
    except ValueError:
        return False


def create_blueprint():
    bp = Blueprint("account_mcp", __name__)

    @bp.route(
        "/cockpit/mcp",
        methods=["POST", "GET", "DELETE"],
        provide_automatic_options=False,
    )
    def endpoint():
        if os.environ.get("COCKPIT_MCP_ENABLED", "true").lower() != "true":
            return rpc_error(-32600, "账户 MCP 未启用。", status=404)
        if not valid_origin():
            return rpc_error(-32600, "请求来源不允许。", status=403)
        if request.args:
            return rpc_error(
                -32600,
                "MCP 地址不接受查询参数；凭据只能放在 Authorization 请求头。",
                status=400,
            )
        request.max_content_length = 16384
        if request.content_length is not None and request.content_length > 16384:
            return rpc_error(-32600, "请求体超过 16 KiB。", status=413)
        try:
            access = credential()
            user = upstream.authenticate(access)
        except upstream.AccountError as exc:
            return account_error(exc)
        if request.method != "POST":
            response = make_response(
                *rpc_error(
                    -32600,
                    "此 MCP 使用无状态 HTTP POST，不提供 SSE 长连接或会话删除。",
                    status=405,
                )
            )
            response.headers["Allow"] = "POST"
            return response
        if not request.is_json:
            return rpc_error(-32600, "需要 application/json。", status=415)
        if not all(
            request.accept_mimetypes[mime] > 0
            for mime in ("application/json", "text/event-stream")
        ):
            return rpc_error(
                -32600,
                "Accept 需包含 application/json 和 text/event-stream。",
                status=406,
            )
        version = request.headers.get("MCP-Protocol-Version", "2025-03-26")
        try:
            body = request.get_json(silent=False)
        except (BadRequest, RequestEntityTooLarge) as exc:
            # Never include Flask's parse detail, request bytes or credentials.
            return rpc_error(
                -32700,
                "JSON 无效或请求体超过 16 KiB。",
                status=413 if isinstance(exc, RequestEntityTooLarge) else 400,
            )
        if (
            not isinstance(body, dict)
            or body.get("jsonrpc") != "2.0"
            or not isinstance(body.get("method"), str)
            or not isinstance(body.get("params", {}), dict)
            or set(body) - {"jsonrpc", "id", "method", "params"}
        ):
            return rpc_error(
                -32600, "需要单个 JSON-RPC 2.0 请求，不接受批处理。", status=400
            )
        ident = body.get("id")
        if "id" in body and not (
            type(ident) is int
            and abs(ident) <= 2**53 - 1
            or isinstance(ident, str)
            and 1 <= len(ident) <= 128
        ):
            return rpc_error(-32600, "请求 ID 无效。", status=400)
        method, params = body["method"], body.get("params", {})
        if method == "server/discover":
            # Modern SDKs probe this method before falling back to the 2025
            # initialize lifecycle. Report the method, not HTTP/auth, failure.
            return rpc_error(-32601, "请使用 initialize 协商 2025 MCP 协议。", ident)
        if version not in VERSIONS:
            return rpc_error(-32600, "不支持的 MCP 协议版本。", ident, 400)
        if "id" not in body:
            if method in {"notifications/initialized", "notifications/cancelled"}:
                return "", 202
            return rpc_error(-32600, "此方法需要请求 ID。", status=400)
        if method == "initialize":
            result = {
                "protocolVersion": params.get("protocolVersion")
                if params.get("protocolVersion") in VERSIONS
                else VERSIONS[0],
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": INFO,
                "instructions": "仅操作当前 PAT 所属用户；支持余额、用量及本人 KEY 列表、分组、申请、删除、改组和显式读取。不能充值、修改账户额度、查看他人或写本机配置。删除和读取完整 KEY 前须取得用户明确指示；结果未知时先查列表，不能自动重发创建。金额为账面单位。",
            }
        elif method == "ping":
            result = {}
        elif method == "tools/list":
            result = {"tools": TOOLS}
        elif method == "tools/call":
            if set(params) - {"name", "arguments", "_meta"}:
                return rpc_error(-32602, "工具参数无效。", ident)
            name, arguments = params.get("name"), params.get("arguments", {})
            if not isinstance(name, str) or name not in {
                tool["name"] for tool in TOOLS
            }:
                return rpc_error(-32602, "未知工具。", ident)
            if not isinstance(arguments, dict):
                return rpc_error(-32602, "arguments 必须为对象。", ident)
            try:
                if name == "get_balance":
                    if arguments:
                        raise ValueError("余额查询不接受参数，只能查询本人。")
                    data = service.balance(user)
                elif name == "get_usage":
                    data = service.usage(user, access, arguments)
                else:
                    data = keys.call(name, user, access, arguments)
                result = {
                    "content": [
                        {"type": "text", "text": json.dumps(data, ensure_ascii=False)}
                    ],
                    "structuredContent": data,
                    "isError": False,
                }
            except ValueError as exc:
                if isinstance(exc, upstream.AccountError):
                    # Transport auth failure still means auth failure, not a
                    # cached identity or a tool-success response with old data.
                    if exc.status in {401, 403, 429}:
                        return account_error(exc, ident)
                    message = str(exc)
                else:
                    return rpc_error(-32602, str(exc), ident)
                result = {
                    "content": [{"type": "text", "text": message}],
                    "isError": True,
                }
        else:
            return rpc_error(-32601, "不支持的方法。", ident)
        response = jsonify(jsonrpc="2.0", id=ident, result=result)
        response.headers["MCP-Protocol-Version"] = (
            result["protocolVersion"] if method == "initialize" else version
        )
        return response

    return bp
