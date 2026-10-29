#!/usr/bin/env python3
# Usage: PGHOST=... PGPASSWORD=... python -m new_api_cockpit.app
# Production: docker compose up -d --build (Gunicorn serves /cockpit/).
"""Cockpit pages/APIs with isolated image and private-chat request roles."""

import os
import re
import time
from datetime import date, datetime
from decimal import Decimal
from urllib.parse import quote, urlsplit

from flask import (
    Flask,
    g,
    jsonify,
    render_template,
    request,
    send_file,
    redirect,
    make_response,
    Response,
    stream_with_context,
)
from flask.json.provider import DefaultJSONProvider
import psycopg
from werkzeug.exceptions import HTTPException
from new_api_cockpit import balance
from new_api_cockpit import pat_permissions
from new_api_cockpit import scopes as scope_backend
from new_api_cockpit import notifications
from new_api_cockpit import quota as quota_backend
from new_api_cockpit import (
    quota_schedule,
    report_snapshots,
    timers,
    user_management,
    operation_records,
    openapi,
    quality,
    channel_flow,
    intelligence,
    manual,
    client_downloads,
    currency_display,
    image_studio,
    image_receipts,
)

from new_api_cockpit.features.image_studio import http as image_http
from new_api_cockpit.platform.identity import Principal, RequestAccess
from new_api_cockpit.platform.host import HostServices
from new_api_cockpit.composition import register_image, register_chat
from new_api_cockpit.features.chat import http as chat_http
from new_api_cockpit.features.account_mcp import http as account_mcp_http

from new_api_cockpit.report import (
    TZ,
    export_excel,
    load_group_options,
    load_report,
    load_token_options,
    totals,
    parse_boundary,
    rankings,
    load_site_name,
)
from new_api_cockpit.auth import (
    COOKIE_NAME,
    COOKIE_PATH,
    LoginRequired,
    LoginForbidden,
    AuthUnavailable,
    verify_session,
    verify_pat,
)

app = Flask(__name__, static_url_path="/cockpit/static")
PAT_ONLY_ENDPOINTS = {"balance_api", "balance_alert_api"}
# Every other route keeps its administrator requirement, including new routes.
USER_ENDPOINTS = {
    "cockpit_home",
    "browser_session",
    "manual_page",
    "tools_page",
    "vscode_download",
    "manual_tokens",
    "manual_models",
    "manual_key",
    "intelligence_page",
    "intelligence_models",
    "intelligence_test",
    "intelligence_preview",
}


USER_ENDPOINTS |= image_http.USER_ENDPOINTS | chat_http.USER_ENDPOINTS
# This endpoint has its own PAT-only boundary; browser sessions do not grant it.
USER_ENDPOINTS.add(account_mcp_http.ENDPOINT)


class JSONProvider(DefaultJSONProvider):
    def default(self, obj):
        if isinstance(obj, Decimal):
            return str(obj)
        if isinstance(obj, (date, datetime)):
            return obj.isoformat()
        return super().default(obj)


app.json = JSONProvider(app)


def safe_next(value):
    """Only return to known local pages/downloads, never a supplied origin."""
    default = "/cockpit/statistics/"
    if not isinstance(value, str) or len(value) > 4096:
        return default
    if (
        "\\" in value
        or any(ord(char) < 32 or ord(char) == 127 for char in value)
        or not value.startswith("/cockpit")
    ):
        return default
    parsed = urlsplit(value)
    paths = {
        "/cockpit",
        "/cockpit/",
        "/cockpit/statistics",
        "/cockpit/statistics/",
        "/cockpit/users",
        "/cockpit/users/",
        "/cockpit/keys",
        "/cockpit/keys/",
        "/cockpit/operations",
        "/cockpit/operations/",
        "/cockpit/docs",
        "/cockpit/docs/",
        "/cockpit/quality",
        "/cockpit/quality/",
        "/cockpit/intelligence",
        "/cockpit/intelligence/",
        "/cockpit/manual",
        "/cockpit/manual/",
        "/cockpit/tools",
        "/cockpit/tools/",
        "/cockpit/image-studio",
        "/cockpit/image-studio/",
        "/cockpit/chat",
        "/cockpit/chat/",
        "/cockpit/chat/settings",
        "/cockpit/chat/settings/",
        "/cockpit/api/statistics/export",
    }
    return value if not parsed.netloc and parsed.path in paths else default


def login_url():
    return "/cockpit/login?next=" + quote(
        safe_next(request.full_path.rstrip("?")), safe=""
    )


def request_identity():
    """Explicit Bearer wins over cookies; invalid credentials never fall back.

    Business APIs accept a New API dashboard JWT or an administrator PAT.
    Pages/session transport accept JWTs only; balance/alert keep PAT-only auth.
    """
    pat_only = request.endpoint in PAT_ONLY_ENDPOINTS
    allow_pat = (
        request.path.startswith("/cockpit/api/")
        and request.endpoint != "browser_session"
        and request.endpoint not in chat_http.USER_ENDPOINTS
    )
    authorization = request.headers.get("Authorization")
    token = request.cookies.get(COOKIE_NAME)
    if authorization is not None:
        scheme, separator, value = authorization.partition(" ")
        if scheme.lower() == "bearer":
            if not separator or not value or any(char.isspace() for char in value):
                raise LoginRequired("Bearer 凭据格式无效。")
            # Both credential kinds are validated upstream. Scoped PATs also
            # require an explicit grant for this Cockpit operation.
            if allow_pat and (pat_only or "." not in value):
                g.auth_kind = "pat"
                user = verify_pat(value)
                pat_permissions.require_access(user, request.endpoint, request.method)
                return user
            g.auth_kind = "session"
            return verify_session(value, allow_user=True)
        if pat_only or not token:
            raise LoginRequired("请使用 Bearer 凭据。")
    if pat_only:
        raise LoginRequired("请提供 New API 管理员 PAT。")
    # Other authorization schemes grant no access; a browser cookie still works.
    if not token:
        return None
    g.auth_kind = "session"
    return verify_session(token, allow_user=True)


def same_origin_request():
    if request.headers.get("Sec-Fetch-Site") == "cross-site":
        return False
    origin = request.headers.get("Origin")
    if not origin:
        # Browser writes also require a non-simple per-feature header and JSON.
        return True
    try:
        parsed = urlsplit(origin)
        return (
            parsed.scheme in {"http", "https"}
            and parsed.netloc == request.host
            and not parsed.username
            and parsed.path in {"", "/"}
            and not parsed.query
            and not parsed.fragment
        )
    except ValueError:
        return False


def auth_failure(
    message="登录已失效，请重新登录。",
    status=401,
    code="AUTH_REQUIRED",
    *,
    clear_cookie=True,
):
    is_page = request.endpoint in {
        "cockpit_home",
        "index",
        "users_page",
        "keys_page",
        "operations_page",
        "api_docs_page",
        "quality_page",
        "intelligence_page",
        "manual_page",
        "tools_page",
        "image_studio.page",
        "chat.page",
        "chat.settings_page",
    }
    if is_page and status == 401:
        response = redirect(login_url(), 302)
    else:
        response = make_response(
            jsonify(error=message, code=code, login_url=login_url()), status
        )
    explicit_bearer = (
        request.headers.get("Authorization", "").split(" ", 1)[0].lower() == "bearer"
    )
    if status == 401 and not is_page:
        response.headers["WWW-Authenticate"] = "Bearer"
    # An unrelated PAT/JWT failure must not erase the browser's valid session.
    if (
        status in {401, 403}
        and clear_cookie
        and not explicit_bearer
        and request.endpoint not in PAT_ONLY_ENDPOINTS
    ):
        response.delete_cookie(
            COOKIE_NAME, path=COOKIE_PATH, httponly=True, samesite="Strict"
        )
    return response


@app.before_request
def authenticate():
    role = os.environ.get("COCKPIT_PROCESS_ROLE", "web")
    if role not in {"web", "image", "chat"}:
        return jsonify(error="实例角色无效。", code="CHAT_CONFIG_INVALID"), 503
    if role == "image" and request.endpoint not in image_http.ROLE_ENDPOINTS[
        "image"
    ] | {"health"}:
        return jsonify(error="该实例只处理图片请求。"), 404
    if role == "chat" and request.endpoint not in chat_http.API_ENDPOINTS | {"health"}:
        return jsonify(error="该实例只处理聊天请求。"), 404
    if request.endpoint == account_mcp_http.ENDPOINT:
        # Keep normal-user PATs OUT of request_identity()/administrator APIs.
        # The MCP handler revalidates PATs/scopes through official own-account
        # endpoints, without accepting cookies, JWTs, or requested identities.
        return None
    if request.endpoint in chat_http.USER_ENDPOINTS:
        if os.environ.get("CHAT_ENABLED", "false").lower() != "true":
            return jsonify(error="聊天功能未启用。", code="CHAT_NOT_FOUND"), 404
        if request.endpoint not in chat_http.ROLE_ENDPOINTS.get(role, frozenset()):
            return jsonify(
                error="聊天请求池尚未接入。", code="CHAT_STORAGE_UNAVAILABLE"
            ), 503
    if request.endpoint is None or request.endpoint in {
        "static",
        "health",
        "login_page",
    }:
        return None
    if request.method not in {"GET", "HEAD", "OPTIONS"} and not same_origin_request():
        return jsonify(error="请求来源不匹配。", code="AUTH_ORIGIN_FORBIDDEN"), 403
    if request.endpoint == "browser_session" and request.method in {"POST", "DELETE"}:
        return None
    try:
        user = request_identity()
    except LoginRequired as exc:
        return auth_failure(str(exc))
    except LoginForbidden as exc:
        return auth_failure(str(exc), 403, "AUTH_FORBIDDEN")
    except AuthUnavailable as exc:
        response = auth_failure(str(exc), exc.status, "AUTH_UNAVAILABLE")
        if exc.status == 429 and exc.retry_after is not None:
            response.headers["Retry-After"] = exc.retry_after
        return response
    if not user:
        return auth_failure()
    if user["role"] < 10 and request.endpoint not in USER_ENDPOINTS:
        return auth_failure(
            "此功能仅管理员可访问；普通用户可使用模型使用、辅助工具、智力测试和生图工作站。",
            403,
            "AUTH_FORBIDDEN",
            clear_cookie=False,
        )
    if request.endpoint in chat_http.API_ENDPOINTS:
        if (
            not user.get("session_id")
            or getattr(g, "auth_kind", "session") != "session"
        ):
            return jsonify(
                error="聊天仅接受本人登录会话。", code="CHAT_SESSION_REQUIRED"
            ), 403
        if not request.headers.get("X-Cockpit-Session"):
            return jsonify(
                error="缺少当前会话标识。", code="CHAT_SESSION_REQUIRED"
            ), 403
    expected = request.headers.get("X-Cockpit-Session")
    if expected and expected != user.get("session_id"):
        return auth_failure(
            "登录账号已切换，请重新加载页面。", 409, "AUTH_SESSION_CHANGED"
        )
    g.current_user = user


@app.context_processor
def login_context():
    return {
        "current_user": getattr(g, "current_user", None),
        "chat_enabled": os.environ.get("CHAT_ENABLED", "false").lower() == "true",
    }


@app.get("/cockpit/login")
def login_page():
    try:
        name = load_site_name()
    except psycopg.Error:
        name = "New API Cockpit"
    return render_template(
        "login.html", site_name=name, next_path=safe_next(request.args.get("next"))
    )


@app.route("/cockpit/api/auth/session", methods=["GET", "POST", "DELETE"])
def browser_session():
    if request.method == "GET":
        return jsonify(g.current_user)
    if request.headers.get("X-Cockpit-Auth") != "1" or not request.is_json:
        return jsonify(error="不允许的登录请求。"), 403
    if request.method == "DELETE":
        response = jsonify(ok=True)
        response.delete_cookie(
            COOKIE_NAME, path=COOKIE_PATH, httponly=True, samesite="Strict"
        )
        return response
    if request.content_length is not None and request.content_length > 8192:
        return jsonify(error="登录请求过大。"), 413
    request.max_content_length = 8192
    body = request.get_json(silent=True)
    if not isinstance(body, dict) or set(body) != {"access_token"}:
        return jsonify(error="登录请求无效。"), 400
    try:
        user = verify_session(body["access_token"], allow_user=True)
    except LoginRequired as exc:
        return auth_failure(str(exc))
    except LoginForbidden as exc:
        return auth_failure(str(exc), 403, "AUTH_FORBIDDEN")
    except AuthUnavailable as exc:
        return auth_failure(str(exc), 503, "AUTH_UNAVAILABLE")
    response = jsonify(user)
    response.set_cookie(
        COOKIE_NAME,
        body["access_token"],
        max_age=max(1, user["expires_at"] - int(time.time())),
        path=COOKIE_PATH,
        httponly=True,
        secure=os.environ.get("COCKPIT_COOKIE_SECURE", "true").lower() != "false",
        samesite="Strict",
    )
    return response


@app.after_request
def headers(response):
    if (
        "scope_id" in request.args
        and response.is_json
        and response.status_code < 400
        and (
            request.path.startswith("/cockpit/api/statistics/balance/")
            and "/channel" not in request.path
        )
    ):
        body = response.get_json()
        if isinstance(body, dict):
            body["scope"] = scope_context()
            response.set_data(app.json.dumps(body))
    # Static sources contain no user data. Their names are not fingerprinted,
    # so require revalidation rather than leaving an old client after upgrades.
    response.headers["Cache-Control"] = (
        "public, max-age=0, must-revalidate"
        if request.endpoint == "static" and response.status_code in {200, 304}
        else "no-store"
    )
    response.headers["X-Content-Type-Options"] = "nosniff"
    if request.endpoint == account_mcp_http.ENDPOINT and response.status_code == 401:
        response.headers["WWW-Authenticate"] = 'Bearer realm="cockpit-account-mcp"'
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; frame-ancestors 'none'"
    )
    if request.endpoint == "api_docs_page":
        # Scalar injects its local stylesheet. Scripts/connections remain same-origin.
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
            "connect-src 'self'; img-src 'self' data:; font-src 'self'; "
            "object-src 'none'; base-uri 'self'; frame-ancestors 'none'"
        )
    if request.endpoint == "intelligence_page":
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "frame-src 'self'; connect-src 'self'; img-src 'self' data:; "
            "object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'"
        )
    if request.endpoint in {
        "manual_page",
        "manual_tokens",
        "manual_models",
        "manual_key",
        "tools_page",
        "vscode_download",
    }:
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "connect-src 'self'; img-src 'self' data:; object-src 'none'; "
            "base-uri 'self'; form-action 'self'; frame-ancestors 'none'"
        )
    if request.endpoint == "intelligence_preview":
        # The HTTP sandbox also applies when the generated document is opened
        # directly. Never relax the parent's script policy to render model HTML.
        response.headers["Content-Security-Policy"] = intelligence.PREVIEW_CSP
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = (
            "camera=(), microphone=(), geolocation=(), payment=(), usb=()"
        )
    if request.endpoint == "image_studio.page" or request.path.startswith(
        "/cockpit/api/image-studio/"
    ):
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "connect-src 'self'; img-src 'self' data: blob:; "
            "object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'"
        )
    if request.endpoint in chat_http.USER_ENDPOINTS:
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; "
            "img-src 'self'; object-src 'none'; frame-src 'none'; base-uri 'self'; "
            "form-action 'self'; frame-ancestors 'none'"
        )
    return response


@app.get("/healthz")
def health():
    if os.environ.get("COCKPIT_PROCESS_ROLE") == "chat":
        from new_api_cockpit.composition import get_chat_service

        try:
            state = get_chat_service(verify_session).health()
        except ValueError:
            state = {"ready": False, "role": "chat"}
        return jsonify(
            status="ok" if state["ready"] else "unhealthy", chat=state
        ), 200 if state["ready"] else 503
    state = (
        image_studio.health()
        if image_studio.process_role() == "image"
        else timers.status()
    )
    if image_studio.process_role() == "image":
        state["receipts_ready"] = image_receipts.health()
        state["ready"] = state["ready"] and state["receipts_ready"]
    return jsonify(status="ok" if state["ready"] else "unhealthy", timers=state), (
        200 if state["ready"] else 503
    )


@app.get("/cockpit")
@app.get("/cockpit/")
def cockpit_home():
    return redirect(
        "/cockpit/statistics/" if g.current_user["role"] >= 10 else "/cockpit/manual",
        302,
    )


@app.get("/cockpit/manual")
@app.get("/cockpit/manual/")
def manual_page():
    return render_template("manual.html", site_name=load_site_name())


@app.get("/cockpit/tools")
@app.get("/cockpit/tools/")
def tools_page():
    return render_template(
        "tools.html",
        site_name=load_site_name(),
        vscode_release=client_downloads.vscode_release(),
    )


@app.get("/cockpit/downloads/vscode")
def vscode_download():
    release = client_downloads.vscode_release()
    if not release:
        return jsonify(
            error="当前实例未包含可用的 VS Code 插件安装包，请重新构建镜像。"
        ), 404
    return send_file(
        release.path,
        as_attachment=True,
        download_name=release.filename,
        mimetype="application/octet-stream",
        etag=release.sha256,
    )


@app.get("/cockpit/api/manual/tokens")
def manual_tokens():
    return jsonify(manual.list_tokens(g.current_user, new_api_credential()))


def manual_body():
    if request.headers.get("X-Cockpit-Manual") != "1" or not request.is_json:
        raise manual.ManualError("请求需要 JSON 和 X-Cockpit-Manual: 1。", 403)
    request.max_content_length = 1024
    return request.get_json(silent=True)


@app.post("/cockpit/api/manual/models")
def manual_models():
    return jsonify(manual.models(g.current_user, new_api_credential(), manual_body()))


@app.post("/cockpit/api/manual/key")
def manual_key():
    return jsonify(
        key=manual.reveal_key(g.current_user, new_api_credential(), manual_body())
    )


@app.errorhandler(manual.ManualError)
def manual_error(exc):
    from .platform.http_headers import apply_retry_after

    response = jsonify(error=str(exc), code=exc.code)
    response.status_code = exc.status
    return apply_retry_after(response, exc)


@app.get("/cockpit/docs")
@app.get("/cockpit/docs/")
def api_docs_page():
    return render_template("api-docs.html", site_name=load_site_name())


@app.get("/cockpit/api/openapi.json")
def api_schema():
    response = make_response(openapi.serialized_document())
    response.mimetype = "application/json"
    return response


@app.get("/cockpit/operations")
@app.get("/cockpit/operations/")
def operations_page():
    return render_template("operations.html", site_name=load_site_name())


@app.get("/cockpit/api/operations")
def operations_list():
    return jsonify(
        operation_records.list_records(g.current_user["username"], request.args)
    )


@app.get("/cockpit/api/operations/<source>/<record_id>")
def operations_detail(source, record_id):
    return jsonify(
        operation_records.detail(
            g.current_user["username"],
            source,
            record_id,
            request.args.get("after", -1),
        )
    )


@app.get("/cockpit/api/users/<int:target_id>")
def user_profile_detail(target_id):
    return jsonify(
        user_management.detail(g.current_user["username"], "user", target_id)
    )


@app.get("/cockpit/api/users/<int:target_id>/groups")
def user_profile_groups(target_id):
    return jsonify(
        user_management.user_group_options(g.current_user["username"], target_id)
    )


@app.post("/cockpit/api/users/<int:target_id>/action")
def user_profile_action(target_id):
    return jsonify(
        user_management.single_action(
            g.current_user["username"], "user", target_id, management_body()
        )
    )


@app.get("/cockpit/statistics")
@app.get("/cockpit/statistics/")
def index():
    now = datetime.now(TZ).replace(microsecond=0)
    return render_template(
        "index.html",
        site_name=load_site_name(),
        start=now.replace(day=1, hour=0, minute=0, second=0).strftime(
            "%Y-%m-%dT%H:%M:%S"
        ),
        end=now.strftime("%Y-%m-%dT%H:%M:%S"),
    )


@app.get("/cockpit/quality")
@app.get("/cockpit/quality/")
def quality_page():
    from datetime import timedelta

    now = datetime.now(TZ).replace(microsecond=0)
    return render_template(
        "quality.html",
        site_name=load_site_name(),
        start=(now - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%S"),
        end=now.strftime("%Y-%m-%dT%H:%M:%S"),
    )


@app.get("/cockpit/api/quality/summary")
def quality_summary():
    return jsonify(quality.load(request.args))


@app.get("/cockpit/api/quality/trends")
def quality_trends():
    return jsonify(quality.load(request.args, with_trends=True))


@app.errorhandler(quality.QualityUnavailable)
def quality_unavailable(exc):
    return jsonify(error=str(exc)), 503


def new_api_credential():
    authorization = request.headers.get("Authorization", "")
    scheme, _, value = authorization.partition(" ")
    return value if scheme.lower() == "bearer" else request.cookies.get(COOKIE_NAME)


@app.get("/cockpit/intelligence")
@app.get("/cockpit/intelligence/")
def intelligence_page():
    return render_template(
        "intelligence.html", site_name=load_site_name(), prompt=intelligence.PROMPT
    )


@app.get("/cockpit/api/intelligence/models")
def intelligence_models():
    return jsonify(intelligence.model_options(g.current_user, new_api_credential()))


@app.post("/cockpit/api/intelligence/test")
def intelligence_test():
    request.max_content_length = 4096
    if request.headers.get("X-Intelligence-Request") != "1" or not request.is_json:
        return jsonify(error="请使用 JSON 和 X-Intelligence-Request: 1 发起测试。"), 403
    identity, credential, body = (
        g.current_user,
        new_api_credential(),
        request.get_json(silent=True),
    )
    if (
        request.accept_mimetypes.best_match(["application/json", "text/event-stream"])
        != "text/event-stream"
    ):
        return jsonify(intelligence.run(identity, credential, body))
    events = intelligence.run_events(identity, credential, body)
    # Validate the input and acquire the per-user lock before sending HTTP 200.
    first = next(events)

    def encode(event):
        return f"event: {event['type']}\ndata: {app.json.dumps(event)}\n\n"

    def generate():
        try:
            yield encode(first)
            for event in events:
                yield encode(event)
        except (
            intelligence.IntelligenceError,
            user_management.ManagementError,
            psycopg.Error,
        ) as exc:
            error = app.make_response(app.handle_user_exception(exc))
            yield encode(
                {"type": "error", "status": error.status_code, **error.get_json()}
            )
        except Exception as exc:
            # Never send exception bodies, credentials, or generated content.
            app.logger.error("Intelligence stream failed: %s", type(exc).__name__)
            yield encode(
                {
                    "type": "error",
                    "status": 502,
                    "error": "生成连接异常，请稍后再试。",
                    "uncertain": True,
                }
            )
        finally:
            events.close()

    response = Response(
        stream_with_context(generate()),
        content_type="text/event-stream; charset=utf-8",
        headers={"X-Accel-Buffering": "no"},
    )
    response.call_on_close(events.close)
    return response


@app.post("/cockpit/api/intelligence/preview")
def intelligence_preview():
    # Stateless POST: HTML stays in this page, never in a URL, file or table.
    request.max_content_length = intelligence.MAX_HTML_BYTES * 3 + 8192
    request.max_form_memory_size = request.max_content_length
    if request.is_json:
        if request.headers.get("X-Intelligence-Request") != "1":
            return jsonify(error="缺少预览请求头。"), 403
        body = request.get_json(silent=True)
        if not isinstance(body, dict) or set(body) != {"html"}:
            return jsonify(error="只允许提供 HTML。"), 400
        html = body["html"]
    else:
        # A form targets the sandbox frame without enabling inline scripts in
        # the parent. Require browser same-origin POST AND the page's session ID.
        if (
            request.mimetype != "application/x-www-form-urlencoded"
            or not request.headers.get("Origin")
            or not g.current_user.get("session_id")
            or request.form.get("session_id") != g.current_user["session_id"]
        ):
            return jsonify(error="预览会话或来源不匹配，请重新加载页面。"), 403
        html = request.form.get("html")
    response = make_response(intelligence.preview_document(html))
    response.mimetype = "text/html"
    return response


@app.errorhandler(intelligence.IntelligenceError)
def intelligence_error(exc):
    return jsonify(error=str(exc), uncertain=exc.uncertain), exc.status


@app.get("/cockpit/users")
@app.get("/cockpit/users/")
def users_page():
    return render_template("users.html", site_name=load_site_name())


@app.get("/cockpit/api/users")
def quota_users():
    return jsonify(rows=quota_backend.list_users())


@app.post("/cockpit/api/users/quota/preview")
def quota_preview():
    if request.headers.get("X-Quota-Action") != "preview":
        return jsonify(error="请求来源无效。"), 403
    return jsonify(
        quota_backend.preview(g.current_user["username"], request.get_json(silent=True))
    )


@app.post("/cockpit/api/users/quota/apply")
def quota_apply():
    if request.headers.get("X-Quota-Action") != "confirm":
        return jsonify(error="请先确认额度调整。"), 403
    return jsonify(
        quota_backend.apply(g.current_user["username"], request.get_json(silent=True))
    )


def quota_schedule_write_allowed():
    return (
        request.headers.get("X-Quota-Action") == "schedule"
        and request.is_json
        and request.headers.get("Sec-Fetch-Site") != "cross-site"
    )


@app.get("/cockpit/api/users/schedules")
def quota_schedules_list():
    return jsonify(quota_schedule.list_rules(g.current_user["username"]))


@app.post("/cockpit/api/users/schedules")
def quota_schedule_create():
    if not quota_schedule_write_allowed():
        return jsonify(error="不允许的定时规则请求。"), 403
    return jsonify(
        quota_schedule.save_rule(
            g.current_user["username"], request.get_json(silent=True)
        )
    ), 201


@app.route(
    "/cockpit/api/users/schedules/<int:rule_id>", methods=["PUT", "PATCH", "DELETE"]
)
def quota_schedule_change(rule_id):
    if not quota_schedule_write_allowed():
        return jsonify(error="不允许的定时规则请求。"), 403
    body = request.get_json(silent=True)
    if request.method == "DELETE":
        quota_schedule.delete_rule(g.current_user["username"], rule_id, body)
        return jsonify(deleted=True)
    if request.method == "PATCH":
        return jsonify(
            quota_schedule.set_enabled(g.current_user["username"], rule_id, body)
        )
    return jsonify(quota_schedule.save_rule(g.current_user["username"], body, rule_id))


@app.errorhandler(quota_schedule.ScheduleConflict)
def quota_schedule_conflict(exc):
    return jsonify(error=str(exc)), 409


@app.errorhandler(quota_schedule.ScheduleForbidden)
def quota_schedule_forbidden(exc):
    return jsonify(error=str(exc)), 403


@app.errorhandler(quota_schedule.ScheduleUnavailable)
def quota_schedule_unavailable(exc):
    return jsonify(error=str(exc)), 503


@app.get("/cockpit/keys")
@app.get("/cockpit/keys/")
def keys_page():
    return render_template("keys.html", site_name=load_site_name())


def management_body():
    if (
        request.headers.get("X-Management-Action") != "confirm"
        or not request.is_json
        or request.headers.get("Sec-Fetch-Site") == "cross-site"
    ):
        raise user_management.Forbidden("不允许的用户管理请求。")
    origin = request.headers.get("Origin")
    if origin:
        from urllib.parse import urlsplit

        if urlsplit(origin).netloc != request.host:
            raise user_management.Forbidden("请求来源不匹配。")
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        raise user_management.ManagementError("请求体必须是对象。")
    return body


@app.get("/cockpit/api/keys/options")
def management_options():
    return jsonify(user_management.options(g.current_user["username"]))


@app.post("/cockpit/api/keys/query/grouped")
def management_query():
    return jsonify(
        user_management.list_grouped(g.current_user["username"], management_body())
    )


@app.get("/cockpit/api/keys/<int:target_id>")
def management_detail(target_id):
    return jsonify(
        user_management.detail(g.current_user["username"], "token", target_id)
    )


@app.get("/cockpit/api/keys/<int:token_id>/groups")
def management_token_groups(token_id):
    return jsonify(
        user_management.token_group_options(g.current_user["username"], token_id)
    )


@app.post("/cockpit/api/keys/<int:target_id>/action")
def management_action(target_id):
    body = management_body()
    if body.get("action") == "reveal" and getattr(g, "auth_kind", None) == "pat":
        try:
            pat_permissions.require_secret_access(g.current_user)
        except LoginForbidden as exc:
            return auth_failure(str(exc), 403, "AUTH_FORBIDDEN", clear_cookie=False)
    return jsonify(
        user_management.single_action(
            g.current_user["username"], "token", target_id, body
        )
    )


@app.post("/cockpit/api/keys/groups/preview")
def management_group_preview():
    return jsonify(
        user_management.preview_group(g.current_user["username"], management_body())
    )


@app.post("/cockpit/api/keys/operations/<uuid:operation_id>/apply")
def management_group_apply(operation_id):
    management_body()
    return jsonify(user_management.apply_wave(g.current_user["username"], operation_id))


@app.errorhandler(user_management.Forbidden)
def management_forbidden(exc):
    return jsonify(error=str(exc)), 403


@app.errorhandler(user_management.Conflict)
def management_conflict(exc):
    return jsonify(error=str(exc)), 409


@app.errorhandler(user_management.Uncertain)
def management_uncertain(exc):
    return jsonify(error=str(exc), uncertain=True), 502


@app.errorhandler(user_management.ManagementError)
def management_error(exc):
    return jsonify(error=str(exc)), 400


def scope_context():
    """Resolve a ledger once per request; absent scope preserves all-site behavior.

    Backend contract: scopes.get_scope(id), list_scopes(), channel_filter(id).
    A scoped request must fail closed if its scope does not exist.
    """
    if "usage_scope" not in g:
        raw = request.args.get("scope_id")
        if raw is None or raw == "1":
            g.usage_scope = {"id": 1, "kind": "all", "tag_value": ""}
        else:
            try:
                scope_id = int(raw)
            except (TypeError, ValueError):
                raise ValueError("请选择有效的统计分组。") from None
            if scope_id <= 0:
                raise ValueError("请选择有效的统计分组。")
            scope = scope_backend.get_scope(scope_id)
            if not scope:
                raise ValueError("统计分组不存在。")
            g.usage_scope = {key: scope[key] for key in ("id", "kind", "tag_value")}
    return g.usage_scope


def scope_kwargs():
    """Pass an explicit ledger ID only when the caller selected one."""
    scope = scope_context()
    return {"scope_id": scope["id"]} if "scope_id" in request.args else {}


def report_scope_kwargs():
    scope = scope_context()
    if scope["kind"] == "all":
        return {}
    if "scope_channels" not in g:
        g.scope_channels = scope_backend.channel_filter(scope["id"])
        if g.scope_channels is None:
            raise ValueError("统计分组的渠道范围不可用。")
    if scope["kind"] == "ungrouped":
        # Unknown historical/deleted IDs must not disappear on a fresh install.
        # channel_filter above refreshes live tags while retaining deleted inventory.
        if "excluded_scope_channels" not in g:
            with balance.connect() as conn:
                g.excluded_scope_channels = [
                    row["channel_id"]
                    for row in conn.execute(
                        "SELECT channel_id FROM balance_channel_inventory WHERE scope_id <> %s",
                        (scope["id"],),
                    ).fetchall()
                ]
        return {"excluded_channel_ids": g.excluded_scope_channels}
    return {"channel_ids": g.scope_channels}


@app.get("/cockpit/api/statistics/scopes")
def scopes():
    if not balance.configured():
        return jsonify(rows=[{"id": 1, "kind": "all", "tag_value": ""}])
    return jsonify(
        rows=[
            {key: row[key] for key in ("id", "kind", "tag_value")}
            for row in scope_backend.list_scopes()
        ]
    )


def failure_diagnostics():
    return request.args.get("include_failures") == "1"


def selected(*, by_token=False, include_failures=False, display_currency=None):
    start, end = request.args.get("start", ""), request.args.get("end", "")
    kwargs = {"by_token": by_token, **report_scope_kwargs()}
    if include_failures:
        kwargs["include_failures"] = True
    if display_currency is not None:
        kwargs["currency_display"] = display_currency
    rows = load_report(start, end, **kwargs)
    user = request.args.get("user", "").strip()
    if user:
        rows = [r for r in rows if r["username"] == user]
    model = request.args.get("model", "").strip()
    if model:
        rows = [r for r in rows if r["model_name"] == model]
    return start, end, rows


def report_rows(rows):
    if request.args.get("details") == "lazy" and balance.configured():
        return report_snapshots.create(
            rows, g.current_user["username"], scope_context()["id"]
        )
    return rows


@app.post("/cockpit/api/statistics/usage/details")
def usage_details():
    if not monitor_write_allowed():
        return jsonify(error="不允许的详情请求。"), 403
    return jsonify(
        rows=report_snapshots.fetch(
            request.get_json(), g.current_user["username"], scope_context()["id"]
        )
    )


@app.errorhandler(report_snapshots.SnapshotExpired)
def snapshot_expired(exc):
    return jsonify(error=str(exc)), 410


@app.get("/cockpit/api/statistics/usage")
def usage():
    display = currency_display.settings({})
    start, end, rows = selected(
        include_failures=failure_diagnostics(), display_currency=display
    )
    return jsonify(
        scope=scope_context(),
        start=start,
        end=end,
        rows=report_rows(rows),
        totals=totals(rows, start, end),
        rankings=rankings(rows),
        updated_at=datetime.now(TZ).isoformat(timespec="seconds"),
        currency_display=display,
    )


@app.get("/cockpit/api/statistics/channel-flow")
def channel_flow_usage():
    return jsonify(channel_flow.load(*channel_flow.parameters(request.args)))


@app.errorhandler(channel_flow.FlowUnavailable)
def channel_flow_unavailable(exc):
    return jsonify(error=str(exc)), 503


@app.get("/cockpit/api/statistics/usage/by-token")
def usage_by_token():
    start, end, rows = selected(by_token=True, include_failures=failure_diagnostics())
    return jsonify(scope=scope_context(), start=start, end=end, rows=report_rows(rows))


@app.get("/cockpit/api/statistics/usage/tokens")
def usage_tokens():
    start, end = request.args.get("start", ""), request.args.get("end", "")
    kwargs = report_scope_kwargs()
    if failure_diagnostics():
        kwargs["include_failures"] = True
    return jsonify(
        scope=scope_context(),
        start=start,
        end=end,
        rows=load_token_options(start, end, **kwargs),
    )


@app.get("/cockpit/api/statistics/usage/groups")
def usage_groups():
    start, end = request.args.get("start", ""), request.args.get("end", "")
    kwargs = report_scope_kwargs()
    if failure_diagnostics():
        kwargs["include_failures"] = True
    return jsonify(
        scope=scope_context(),
        start=start,
        end=end,
        rows=load_group_options(start, end, **kwargs),
    )


def requested_token_ids():
    values = request.args.getlist("token_id")
    if len(values) > 500:
        raise ValueError("请选择有效的令牌。")
    if not values:
        return None
    try:
        token_ids = sorted({int(value) for value in values})
    except ValueError:
        raise ValueError("请选择有效的令牌。") from None
    if any(token_id < 0 for token_id in token_ids):
        raise ValueError("请选择有效的令牌。")
    return token_ids


def requested_groups():
    values = request.args.getlist("group")
    if len(values) > 500 or any(len(value) > 128 for value in values):
        raise ValueError("请选择有效的分组。")
    return sorted(set(values)) or None


@app.get("/cockpit/api/statistics/usage/by-selection")
def usage_by_selection():
    start, end = request.args.get("start", ""), request.args.get("end", "")
    token_ids, groups = requested_token_ids(), requested_groups()
    if token_ids is None and groups is None:
        raise ValueError("请选择令牌或分组。")
    by_token = request.args.get("by_token", "0") == "1"
    kwargs = dict(
        by_token=by_token, token_ids=token_ids, groups=groups, **report_scope_kwargs()
    )
    if failure_diagnostics():
        kwargs["include_failures"] = True
    rows = load_report(start, end, **kwargs)
    return jsonify(scope=scope_context(), start=start, end=end, rows=report_rows(rows))


@app.get("/cockpit/api/statistics/export")
def export():
    display = currency_display.settings({})
    start, end, rows = selected(display_currency=display)
    first = parse_boundary(start).strftime("%Y-%m-%d_%H-%M-%S")
    last = parse_boundary(end, end=True).strftime("%Y-%m-%d_%H-%M-%S")
    scope = scope_context()
    label = {"all": "全部", "ungrouped": "未分组"}.get(
        scope["kind"], scope["tag_value"]
    )
    label = re.sub(r"[^\w\-\u4e00-\u9fff]", "_", label or "scope")[:80]
    return send_file(
        export_excel(rows, start, end, usd_cny_rate=display["usd_cny_rate"]),
        as_attachment=True,
        download_name=f"usage_{scope['id']}_{label}_{first}_{last}.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


def monitor_write_allowed():
    # Session cookies are ambient. A custom header plus JSON blocks cross-site forms.
    return (
        request.headers.get("X-Statistics-Request") == "1"
        and request.is_json
        and request.headers.get("Sec-Fetch-Site") != "cross-site"
    )


@app.get("/cockpit/api/statistics/balance/status")
def balance_status():
    return jsonify(
        balance.snapshot(live=request.args.get("live") == "1", **scope_kwargs())
    )


@app.get("/cockpit/api/statistics/balance")
def balance_api():
    result = balance.snapshot(live=True, **scope_kwargs())
    if not result.get("configured") or not result.get("valid"):
        return jsonify(code=503, message="余额数据暂不可用。"), 503
    settings, state = result["settings"], result["state"]
    used = state["archived_amount"] + state["current_amount"]
    budget = settings["budget"]
    percent = float((used * 100 / budget).quantize(Decimal("0.01"))) if budget else None
    return jsonify(
        code=0,
        data={
            "site": load_site_name(),
            **({"scope": scope_context()} if "scope_id" in request.args else {}),
            "currency": "CNY",
            "total_quota": float(budget),
            "used_quota": float(used),
            "remaining_quota": float(state["remaining"]),
            "alert_threshold": float(settings["threshold"]),
            "usage_percent": percent,
            "checked_at": state["checked_at"]
            .astimezone(TZ)
            .isoformat(timespec="seconds"),
        },
    )


@app.get("/cockpit/api/statistics/alert")
def balance_alert_api():
    try:
        balance.check_once(daily=False, **scope_kwargs())
    except balance.CheckBusy:
        return jsonify(error="其他检查或设置保存正在进行，请稍后重试。"), 409
    alert = notifications.current_alert_record(**scope_kwargs())
    return jsonify(
        **({"scope": scope_context()} if "scope_id" in request.args else {}),
        has_alert=alert is not None,
        alert=alert,
    )


@app.put("/cockpit/api/statistics/balance/settings")
def balance_settings():
    if not monitor_write_allowed():
        return jsonify(error="不允许的设置请求。"), 403
    try:
        balance.save_settings(
            request.get_json(), g.current_user["username"], **scope_kwargs()
        )
    except balance.SettingsConflict:
        return jsonify(error="设置已被其他管理员修改，请重新打开设置。"), 409
    return jsonify(saved=True)


@app.post("/cockpit/api/statistics/balance/recalculate-history/preview")
def balance_recalculate_history_preview():
    if not monitor_write_allowed():
        return jsonify(error="不允许的追溯请求。"), 403
    try:
        result = balance.history_preview(request.get_json(), **scope_kwargs())
    except balance.SettingsConflict:
        return jsonify(error="设置已被其他管理员修改，请重新打开设置。"), 409
    except balance.ArchiveDataMissing:
        return (
            jsonify(error="New API 未返回历史消费数据，已取消追溯，原归档保持不变。"),
            409,
        )
    return jsonify(result)


@app.post("/cockpit/api/statistics/balance/recalculate-history")
def balance_recalculate_history():
    if not monitor_write_allowed():
        return jsonify(error="不允许的追溯请求。"), 403
    payload = request.get_json()
    if not isinstance(payload, dict):
        return jsonify(error="历史计费预览无效，请重新预览。"), 400
    try:
        result = balance.recalculate_history(
            payload.get("settings"),
            payload.get("preview"),
            g.current_user["username"],
            **scope_kwargs(),
        )
    except balance.SettingsConflict:
        return jsonify(error="设置已被其他管理员修改，请重新打开设置。"), 409
    except balance.ArchiveDataMissing:
        return (
            jsonify(error="New API 未返回历史消费数据，已取消追溯，原归档保持不变。"),
            409,
        )
    except balance.HistoryPreviewChanged:
        return (
            jsonify(error="历史计费数据在预览后发生变化，未写入新费用，请重新预览。"),
            409,
        )
    return jsonify(recalculated=True, **result)


@app.get("/cockpit/api/statistics/balance/usage-channels")
def balance_usage_channels():
    return jsonify(
        **({"scope": scope_context()} if "scope_id" in request.args else {}),
        rows=balance.usage_channels_snapshot(**scope_kwargs()),
    )


@app.post("/cockpit/api/statistics/balance/check")
def balance_check():
    if not monitor_write_allowed():
        return jsonify(error="不允许的检查请求。"), 403
    try:
        checked = balance.check_once(daily=False, **scope_kwargs())
    except balance.CheckBusy:
        return jsonify(error="其他检查或设置保存正在进行，请稍后再次点击铃铛。"), 409
    return jsonify(balance.snapshot(live=not checked, **scope_kwargs()))


@app.errorhandler(ValueError)
def invalid(exc):
    return jsonify(error=str(exc)), 400


@app.errorhandler(HTTPException)
def http_error(exc):
    if not request.path.startswith("/cockpit/api/"):
        return exc
    response = exc.get_response()
    body = {"error": exc.description}
    if request.path.startswith("/cockpit/api/image-studio/"):
        body.update(
            code="IMAGE_TOO_LARGE" if exc.code == 413 else "IMAGE_INVALID_REQUEST",
            uncertain=exc.code >= 500
            and request.endpoint in {"image_studio.generations", "image_studio.edits"},
        )
    response.set_data(app.json.dumps(body))
    response.content_type = "application/json"
    return response


@app.route("/cockpit/api/statistics/balance/channel", methods=["GET", "PUT"])
def notification_settings():
    if request.method == "GET":
        return jsonify(notifications.snapshot(request.args.get("channel") or None))
    if not monitor_write_allowed():
        return jsonify(error="不允许的设置请求。"), 403
    try:
        body = request.get_json()
        notifications.save(body, g.current_user["username"])
    except balance.SettingsConflict:
        return jsonify(error="渠道配置已改变，请重新打开设置。"), 409
    return jsonify(notifications.snapshot(body["channel"]))


@app.post("/cockpit/api/statistics/balance/channel/test")
def notification_test():
    if not monitor_write_allowed():
        return jsonify(error="不允许的测试请求。"), 403
    body = request.get_json()
    if (
        not isinstance(body, dict)
        or type(body.get("version")) is not int
        or not isinstance(body.get("channel"), str)
        or body["channel"] not in notifications.CHANNELS
    ):
        raise ValueError("请先选择并保存报警渠道。")
    try:
        notifications.deliver(
            test=True, expected_version=body["version"], channel=body.get("channel")
        )
    except balance.SettingsConflict:
        return jsonify(error="渠道配置已改变，请重新打开设置。"), 409
    return jsonify(sent=True)


@app.errorhandler(balance.SchemaUnavailable)
def schema_unavailable(exc):
    return jsonify(error=str(exc)), 503


@app.errorhandler(psycopg.Error)
def database_error(exc):
    app.logger.error("Database query failed: %s", type(exc).__name__)
    if isinstance(exc, psycopg.errors.QueryCanceled):
        if request.path.startswith("/cockpit/api/quality/"):
            return jsonify(error="查询超过 30 秒，请缩小时间范围后重试。"), 504
        return jsonify(error="查询超过 60 秒，请缩小时间范围后重试。"), 504
    return jsonify(error="数据库查询失败，请检查连接配置与数据库日志。"), 503


@app.errorhandler(quota_backend.QuotaError)
def quota_error(exc):
    return jsonify(error=str(exc)), 400


def feature_access():
    user = g.current_user
    return RequestAccess(
        Principal(
            user_id=user["id"],
            username=user.get("username", ""),
            role=user["role"],
            session_id=user.get("session_id"),
            expires_at=user.get("expires_at"),
            auth_kind=getattr(
                g, "auth_kind", "session" if user.get("session_id") else "pat"
            ),
        ),
        new_api_credential(),
    )


def feature_write(header, content_type):
    from werkzeug.exceptions import Forbidden, BadRequest

    if not same_origin_request() or request.headers.get(header) != "1":
        raise Forbidden("请求来源或特征头无效。")
    if request.mimetype != content_type:
        raise BadRequest("请求内容类型无效。")


feature_host = HostServices(feature_access, feature_write, lambda: load_site_name())
register_image(app, feature_host)
register_chat(app, feature_host, verify_session)
app.register_blueprint(account_mcp_http.create_blueprint())


def run_development():
    role = os.environ.get("COCKPIT_PROCESS_ROLE", "web")
    if role not in {"web", "image", "chat"}:
        raise SystemExit("Unknown process role")
    chat_service = None
    if role == "web":
        if balance.configured():
            balance.initialize()
        timers.start()
    elif role == "image":
        image_studio.initialize_capacity()
    else:
        from new_api_cockpit.composition import get_chat_service

        chat_service = get_chat_service(verify_session)
    try:
        app.run(
            host="127.0.0.1",
            port=int(os.environ.get("PORT", "8091")),
            use_reloader=False,
        )
    finally:
        if role == "web":
            timers.stop()
        elif chat_service is not None:
            chat_service.shutdown()


if __name__ == "__main__":
    run_development()
