#!/usr/bin/env python3
# Usage: PGHOST=... PGPASSWORD=... python -m new_api_cockpit.app
# Production: docker compose up -d --build (Gunicorn serves /cockpit/).
"""Single-container statistics, quota and user management with protected APIs."""

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
)
from flask.json.provider import DefaultJSONProvider
import psycopg
from new_api_cockpit import balance
from new_api_cockpit import scopes as scope_backend
from new_api_cockpit import notifications
from new_api_cockpit import quota as quota_backend
from new_api_cockpit import (
    quota_schedule,
    report_snapshots,
    timers,
    user_management,
    operation_records,
)

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
    verify_api_key,
)

app = Flask(__name__, static_url_path="/cockpit/static")


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
        "/cockpit/statistics/api/export",
    }
    return value if not parsed.netloc and parsed.path in paths else default


def login_url():
    return "/cockpit/login?next=" + quote(
        safe_next(request.full_path.rstrip("?")), safe=""
    )


def browser_identity():
    authorization = request.authorization
    token = request.cookies.get(COOKIE_NAME)
    if authorization is not None and authorization.type == "bearer":
        token = authorization.token
    elif authorization is not None and not token:
        raise LoginRequired("请通过登录页面登录。")
    # Only Bearer overrides the session cookie; other schemes grant no access.
    if not token:
        return None
    return verify_session(token)


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


def auth_failure(message="登录已失效，请重新登录。", status=401, code="AUTH_REQUIRED"):
    is_page = request.endpoint in {
        "cockpit_home",
        "index",
        "users_page",
        "keys_page",
        "operations_page",
        "export",
    }
    if is_page and status == 401:
        response = redirect(login_url(), 302)
    else:
        response = make_response(
            jsonify(error=message, code=code, login_url=login_url()), status
        )
    if status in {401, 403}:
        response.delete_cookie(
            COOKIE_NAME, path=COOKIE_PATH, httponly=True, samesite="Strict"
        )
    return response


@app.before_request
def authenticate():
    if request.endpoint is None or request.endpoint in {
        "static",
        "health",
        "login_page",
    }:
        return None
    if request.path in {
        "/cockpit/statistics/api/balance",
        "/cockpit/statistics/api/alert",
    }:
        authorization = request.headers.get("Authorization", "")
        scheme, separator, token = authorization.partition(" ")
        if separator and scheme.lower() == "bearer" and verify_api_key(token.strip()):
            return None
        return (
            jsonify(code=401, message="无效的 New API 管理员 PAT，或账号已停用。"),
            401,
            {"WWW-Authenticate": "Bearer"},
        )
    if request.method not in {"GET", "HEAD", "OPTIONS"} and not same_origin_request():
        return jsonify(error="请求来源不匹配。", code="AUTH_ORIGIN_FORBIDDEN"), 403
    if request.endpoint == "browser_session" and request.method in {"POST", "DELETE"}:
        return None
    try:
        user = browser_identity()
    except LoginRequired as exc:
        return auth_failure(str(exc))
    except LoginForbidden as exc:
        return auth_failure(str(exc), 403, "AUTH_FORBIDDEN")
    except AuthUnavailable as exc:
        return auth_failure(str(exc), 503, "AUTH_UNAVAILABLE")
    if not user:
        return auth_failure()
    expected = request.headers.get("X-Cockpit-Session")
    if expected and expected != user["session_id"]:
        return auth_failure(
            "登录账号已切换，请重新加载页面。", 409, "AUTH_SESSION_CHANGED"
        )
    g.current_user = user


@app.context_processor
def login_context():
    return {"current_user": getattr(g, "current_user", None)}


@app.get("/cockpit/login")
def login_page():
    try:
        name = load_site_name()
    except psycopg.Error:
        name = "New API Cockpit"
    return render_template(
        "login.html", site_name=name, next_path=safe_next(request.args.get("next"))
    )


@app.route("/cockpit/auth/session", methods=["GET", "POST", "DELETE"])
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
        user = verify_session(body["access_token"])
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
            request.path.startswith("/cockpit/statistics/api/balance/")
            and "/channel" not in request.path
        )
    ):
        body = response.get_json()
        if isinstance(body, dict):
            body["scope"] = scope_context()
            response.set_data(app.json.dumps(body))
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; frame-ancestors 'none'"
    )
    return response


@app.get("/healthz")
def health():
    state = timers.status()
    return jsonify(status="ok" if state["ready"] else "unhealthy", timers=state), (
        200 if state["ready"] else 503
    )


@app.get("/cockpit")
@app.get("/cockpit/")
def cockpit_home():
    return redirect("/cockpit/statistics/", 302)


@app.get("/cockpit/operations")
@app.get("/cockpit/operations/")
def operations_page():
    return render_template("operations.html", site_name=load_site_name())


@app.get("/cockpit/operations/api/records")
def operations_list():
    return jsonify(
        operation_records.list_records(g.current_user["username"], request.args)
    )


@app.get("/cockpit/operations/api/records/<source>/<record_id>")
def operations_detail(source, record_id):
    return jsonify(
        operation_records.detail(
            g.current_user["username"],
            source,
            record_id,
            request.args.get("after", -1),
        )
    )


@app.get("/cockpit/users/api/user/<int:target_id>")
def user_profile_detail(target_id):
    return jsonify(
        user_management.detail(g.current_user["username"], "user", target_id)
    )


@app.get("/cockpit/users/api/user/<int:target_id>/groups")
def user_profile_groups(target_id):
    return jsonify(
        user_management.user_group_options(g.current_user["username"], target_id)
    )


@app.post("/cockpit/users/api/user/<int:target_id>/action")
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


@app.get("/cockpit/users")
@app.get("/cockpit/users/")
def users_page():
    return render_template("users.html", site_name=load_site_name())


@app.get("/cockpit/users/api/users")
def quota_users():
    return jsonify(rows=quota_backend.list_users())


@app.post("/cockpit/users/api/preview")
def quota_preview():
    if request.headers.get("X-Quota-Action") != "preview":
        return jsonify(error="请求来源无效。"), 403
    return jsonify(
        quota_backend.preview(g.current_user["username"], request.get_json(silent=True))
    )


@app.post("/cockpit/users/api/apply")
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


@app.get("/cockpit/users/api/schedules")
def quota_schedules_list():
    return jsonify(quota_schedule.list_rules(g.current_user["username"]))


@app.post("/cockpit/users/api/schedules")
def quota_schedule_create():
    if not quota_schedule_write_allowed():
        return jsonify(error="不允许的定时规则请求。"), 403
    return jsonify(
        quota_schedule.save_rule(
            g.current_user["username"], request.get_json(silent=True)
        )
    ), 201


@app.route(
    "/cockpit/users/api/schedules/<int:rule_id>", methods=["PUT", "PATCH", "DELETE"]
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


@app.get("/cockpit/keys/api/options")
def management_options():
    return jsonify(user_management.options(g.current_user["username"]))


@app.post("/cockpit/keys/api/query/grouped")
def management_query():
    return jsonify(
        user_management.list_grouped(g.current_user["username"], management_body())
    )


@app.get("/cockpit/keys/api/<kind>/<int:target_id>")
def management_detail(kind, target_id):
    if kind != "token":
        return jsonify(error="Not Found"), 404
    return jsonify(user_management.detail(g.current_user["username"], kind, target_id))


@app.get("/cockpit/keys/api/token/<int:token_id>/groups")
def management_token_groups(token_id):
    return jsonify(
        user_management.token_group_options(g.current_user["username"], token_id)
    )


@app.post("/cockpit/keys/api/<kind>/<int:target_id>/action")
def management_action(kind, target_id):
    if kind != "token":
        return jsonify(error="Not Found"), 404
    return jsonify(
        user_management.single_action(
            g.current_user["username"], kind, target_id, management_body()
        )
    )


@app.post("/cockpit/keys/api/groups/preview")
def management_group_preview():
    return jsonify(
        user_management.preview_group(g.current_user["username"], management_body())
    )


@app.post("/cockpit/keys/api/operations/<uuid:operation_id>/apply")
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


@app.get("/cockpit/statistics/api/scopes")
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


def selected(*, by_token=False, include_failures=False):
    start, end = request.args.get("start", ""), request.args.get("end", "")
    kwargs = {"by_token": by_token, **report_scope_kwargs()}
    if include_failures:
        kwargs["include_failures"] = True
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


@app.post("/cockpit/statistics/api/usage/details")
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


@app.get("/cockpit/statistics/api/usage")
def usage():
    start, end, rows = selected(include_failures=failure_diagnostics())
    return jsonify(
        scope=scope_context(),
        start=start,
        end=end,
        rows=report_rows(rows),
        totals=totals(rows, start, end),
        rankings=rankings(rows),
        updated_at=datetime.now(TZ).isoformat(timespec="seconds"),
    )


@app.get("/cockpit/statistics/api/usage/by-token")
def usage_by_token():
    start, end, rows = selected(by_token=True, include_failures=failure_diagnostics())
    return jsonify(scope=scope_context(), start=start, end=end, rows=report_rows(rows))


@app.get("/cockpit/statistics/api/usage/tokens")
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


@app.get("/cockpit/statistics/api/usage/groups")
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


@app.get("/cockpit/statistics/api/usage/by-selection")
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


@app.get("/cockpit/statistics/api/export")
def export():
    start, end, rows = selected()
    first = parse_boundary(start).strftime("%Y-%m-%d_%H-%M-%S")
    last = parse_boundary(end, end=True).strftime("%Y-%m-%d_%H-%M-%S")
    scope = scope_context()
    label = {"all": "全部", "ungrouped": "未分组"}.get(
        scope["kind"], scope["tag_value"]
    )
    label = re.sub(r"[^\w\-\u4e00-\u9fff]", "_", label or "scope")[:80]
    return send_file(
        export_excel(rows, start, end),
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


@app.get("/cockpit/statistics/api/balance/status")
def balance_status():
    return jsonify(
        balance.snapshot(live=request.args.get("live") == "1", **scope_kwargs())
    )


@app.get("/cockpit/statistics/api/balance")
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


@app.get("/cockpit/statistics/api/alert")
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


@app.put("/cockpit/statistics/api/balance/settings")
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


@app.post("/cockpit/statistics/api/balance/recalculate-history/preview")
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


@app.post("/cockpit/statistics/api/balance/recalculate-history")
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


@app.get("/cockpit/statistics/api/balance/usage-channels")
def balance_usage_channels():
    return jsonify(
        **({"scope": scope_context()} if "scope_id" in request.args else {}),
        rows=balance.usage_channels_snapshot(**scope_kwargs()),
    )


@app.post("/cockpit/statistics/api/balance/check")
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


@app.route("/cockpit/statistics/api/balance/channel", methods=["GET", "PUT"])
def notification_settings():
    if request.method == "GET":
        return jsonify(notifications.snapshot(request.args.get("channel") or None))
    if not monitor_write_allowed():
        return jsonify(error="不允许的设置请求。"), 403
    try:
        notifications.save(request.get_json(), g.current_user["username"])
    except balance.SettingsConflict:
        return jsonify(error="渠道配置已改变，请重新打开设置。"), 409
    return jsonify(notifications.snapshot())


@app.post("/cockpit/statistics/api/balance/channel/test")
def notification_test():
    if not monitor_write_allowed():
        return jsonify(error="不允许的测试请求。"), 403
    body = request.get_json()
    if not isinstance(body, dict) or type(body.get("version")) is not int:
        raise ValueError("请先保存报警渠道。")
    try:
        notifications.deliver(test=True, expected_version=body["version"])
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
        return jsonify(error="查询超过 60 秒，请缩小时间范围后重试。"), 504
    return jsonify(error="数据库查询失败，请检查连接配置与数据库日志。"), 503


@app.errorhandler(quota_backend.QuotaError)
def quota_error(exc):
    return jsonify(error=str(exc)), 400


if __name__ == "__main__":
    if balance.configured():
        balance.initialize()
    timers.start()
    try:
        app.run(
            host="127.0.0.1",
            port=int(os.environ.get("PORT", "8091")),
            use_reloader=False,
        )
    finally:
        timers.stop()
