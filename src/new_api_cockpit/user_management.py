"""Administrator user/token management through owner PATs and official APIs.

Source SQL is read-only except the narrowly granted ensure-PAT function. The
upstream token PUT is an absolute update, not an atomic quota-delta endpoint.
"""

import base64
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from decimal import Decimal, DecimalException
import json
import os
import secrets
import urllib.error
import urllib.request
import uuid

import psycopg
from psycopg.types.json import Jsonb

from new_api_cockpit import balance, quota


class ManagementError(ValueError):
    pass


class Forbidden(ManagementError):
    pass


class Conflict(ManagementError):
    pass


class Uncertain(ManagementError):
    pass


TOKEN_FIELDS = (
    "id",
    "name",
    "status",
    "expired_time",
    "remain_quota",
    "unlimited_quota",
    "model_limits_enabled",
    "model_limits",
    "allow_ips",
    "group",
    "cross_group_retry",
    "auto_groups",
)
USER_FIELDS = ("username", "display_name", "group", "remark")
# Upstream rc.40 defaults, used only when the option has never been stored.
DEFAULT_GROUP_RATIO = {"default": 1, "vip": 1, "svip": 1}
DEFAULT_USABLE_GROUPS = {"default": "默认分组", "vip": "vip分组"}


def config_map(values, key, default=None):
    """An explicitly empty/NULL map is empty, not a grant of default groups."""
    if key not in values:
        return dict(default or {})
    try:
        value = json.loads(values[key]) if values[key] and values[key].strip() else {}
        if value is None:
            return {}
        if not isinstance(value, dict):
            raise ValueError()
        return value
    except (ValueError, TypeError, AttributeError):
        raise ManagementError("New API 分组配置无效。") from None


def actor(username):
    with quota.connect() as conn:
        row = conn.execute(
            "SELECT id,username,role,status FROM users WHERE username=%s AND deleted_at IS NULL",
            (username,),
        ).fetchone()
    if not row or row["role"] < 10 or row["status"] != 1:
        raise Forbidden("管理员已失效，请重新登录。")
    return row


def target(operator, user_id, *, enabled=False):
    user_id = positive_id(user_id)
    with quota.connect() as conn:
        row = conn.execute(
            'SELECT id,username,role,status,"group" FROM users WHERE id=%s AND deleted_at IS NULL',
            (user_id,),
        ).fetchone()
    if not row:
        raise ManagementError("用户不存在或已删除。")
    if (
        operator["role"] != 100
        and operator["role"] <= row["role"]
        and operator["id"] != user_id
    ):
        raise Forbidden("无权管理同级或更高权限用户。")
    if enabled and row["status"] != 1:
        raise ManagementError("所属用户已禁用，不能使用其 PAT；请先明确启用该用户。")
    return row


def positive_id(value):
    if type(value) is not int and (
        not isinstance(value, str) or not value.isascii() or not value.isdigit()
    ):
        raise ManagementError("ID 无效。")
    try:
        result = int(value)
    except (ValueError, TypeError):
        raise ManagementError("ID 无效。") from None
    if result <= 0:
        raise ManagementError("ID 无效。")
    return result


def _ids(values):
    if not isinstance(values, list) or any(
        type(x) is not int or x <= 0 for x in values
    ):
        raise ManagementError("请选择有效的 ID。")
    if len(set(values)) != len(values):
        raise ManagementError("ID 不可重复。")
    return values


def generate_pat():
    # Exactly the Go generator: random length 29..32, floor(length*3/4)
    # crypto-random bytes, standard Base64 including its padding.
    length = 29 + secrets.randbelow(4)
    return base64.b64encode(secrets.token_bytes(length * 3 // 4)).decode("ascii")


def owner_pat(operator, user_id):
    user = target(operator, user_id, enabled=True)
    with quota.connect() as conn:
        row = conn.execute(
            "SELECT access_token FROM users WHERE id=%s AND deleted_at IS NULL",
            (user_id,),
        ).fetchone()
    if not row or not row["access_token"]:
        _audit_ready()
        for attempt in range(5):
            try:
                # Dedicated write-capable transaction; ordinary source queries
                # keep default_transaction_read_only=on. The reader has no
                # UPDATE grants, only EXECUTE on this SECURITY DEFINER function.
                # Override a reader role's read-only default for this connection.
                with psycopg.connect(
                    connect_timeout=8,
                    options="-c default_transaction_read_only=off -c statement_timeout=10000 -c lock_timeout=5000",
                ) as conn:
                    candidate = generate_pat()
                    committed = conn.execute(
                        "SELECT public.statistics_ensure_user_pat(%s,%s,%s)",
                        (operator["id"], user_id, candidate),
                    ).fetchone()[0]
                if committed == candidate:
                    _record_pat_creation(operator, user)
                break
            except psycopg.errors.UniqueViolation:
                if attempt == 4:
                    raise ManagementError("生成 PAT 冲突，请稍后重试。") from None
            except (
                psycopg.errors.UndefinedFunction,
                psycopg.errors.InsufficientPrivilege,
            ):
                raise ManagementError(
                    "PAT 补建函数尚未安装或授权，请按用户管理部署文档配置。"
                ) from None
        # Always read the committed latest value, never return the candidate.
        with quota.connect() as conn:
            row = conn.execute(
                "SELECT access_token FROM users WHERE id=%s AND status=1 AND deleted_at IS NULL",
                (user_id,),
            ).fetchone()
    if not row or not row["access_token"]:
        raise ManagementError("PAT 不可用，请刷新用户状态。")
    return row["access_token"]


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        return None


def call_api(operator, user_id, path, *, method="GET", body=None):
    pat = owner_pat(operator, user_id)
    base = os.environ.get("NEW_API_INTERNAL_URL", "http://new-api:3000").rstrip("/")
    if not base.startswith(("http://", "https://")):
        raise ManagementError("New API 内部地址配置无效。")
    req = urllib.request.Request(
        base + path,
        method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={
            "Authorization": "Bearer " + pat,
            "New-Api-User": str(user_id),
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    mutation = method != "GET"
    try:
        with urllib.request.build_opener(NoRedirect()).open(
            req, timeout=15
        ) as response:
            raw = response.read(2_000_001)
            if len(raw) > 2_000_000:
                raise ValueError()
            result = json.loads(raw)
    except (urllib.error.URLError, TimeoutError, ValueError, OSError):
        if mutation:
            raise Uncertain(
                "New API 响应不明确；请核对实际结果，不要自动重试。"
            ) from None
        raise ManagementError("New API 查询失败，请检查内部连接和 PAT。") from None
    if not isinstance(result, dict) or type(result.get("success")) is not bool:
        raise (
            Uncertain("New API 响应格式异常，请人工核对。")
            if mutation
            else ManagementError("New API 查询响应异常。")
        )
    if not result["success"]:
        # Avoid reflecting raw responses which may contain credentials.
        raise ManagementError(
            "New API 拒绝操作，请检查用户权限、分组、字段及额度限制。"
        )
    return result.get("data")


def filters(operator, args, *, tokens=False):
    if not isinstance(args, dict):
        raise ManagementError("筛选条件必须是对象。")
    clauses = ["u.deleted_at IS NULL"]
    params = []
    if operator["role"] != 100:
        clauses.append("(u.role<%s OR u.id=%s)")
        params.extend((operator["role"], operator["id"]))
    statuses = args.get("user_statuses", [1])
    if not isinstance(statuses, list) or any(
        type(s) is not int or s not in (1, 2) for s in statuses
    ):
        raise ManagementError("用户状态筛选无效。")
    clauses.append("u.status=ANY(%s)")
    params.append(statuses)
    for field, column in (("user_groups", 'u."group"'), ("token_groups", 't."group"')):
        if field in args and args[field] is not None:
            if field == "token_groups" and not tokens:
                continue
            values = args[field]
            if not isinstance(values, list) or any(
                not isinstance(s, str) or len(s) > 64 for s in values
            ):
                raise ManagementError("分组筛选无效。")
            clauses.append(column + "=ANY(%s)")
            params.append(values)
    text = args.get("search", "")
    if not isinstance(text, str) or len(text) > 200:
        raise ManagementError("搜索关键词过长。")
    if text:
        clause, values = keyword_clause(text, tokens=tokens)
        clauses.append(clause)
        params.extend(values)
    if args.get("user_id") is not None:
        clauses.append("u.id=%s")
        params.append(positive_id(args["user_id"]))
    if tokens:
        clauses.append("t.deleted_at IS NULL")
        if args.get("token_statuses") is not None:
            statuses = args["token_statuses"]
            if not isinstance(statuses, list) or any(
                type(s) is not int or s not in (1, 2, 3, 4) for s in statuses
            ):
                raise ManagementError("KEY 状态筛选无效。")
            # Effective expired/exhausted states, not just persisted status.
            clauses.append("(" + effective_status() + ")=ANY(%s)")
            params.append(statuses)
    return " AND ".join(clauses), params


def keyword_clause(text, *, tokens=False):
    fields = ["u.username", "COALESCE(u.display_name,'')", "u.id::text"]
    if tokens:
        fields.extend(["t.name", "t.id::text"])
    return (
        "(" + " OR ".join("strpos(lower(" + f + "),lower(%s))>0" for f in fields) + ")",
        [text] * len(fields),
    )


def effective_status():
    return "CASE WHEN t.status=2 THEN 2 WHEN t.expired_time<>-1 AND t.expired_time<=extract(epoch FROM now()) THEN 3 WHEN NOT t.unlimited_quota AND t.remain_quota<=0 THEN 4 ELSE t.status END"


def options(username):
    operator = actor(username)
    with quota.connect() as conn:
        config = {
            r["key"]: r["value"]
            for r in conn.execute(
                "SELECT key,value FROM options WHERE key=ANY(%s)",
                (["GroupRatio"],),
            )
        }
        groups = [
            r["name"]
            for r in conn.execute(
                'SELECT DISTINCT "group" AS name FROM users WHERE deleted_at IS NULL UNION SELECT DISTINCT "group" FROM tokens WHERE deleted_at IS NULL ORDER BY name'
            )
        ]
    return {
        "groups": sorted(
            set(config_map(config, "GroupRatio", DEFAULT_GROUP_RATIO))
            | {g for g in groups if isinstance(g, str)}
        ),
        "operator": {k: operator[k] for k in ("id", "username", "role")},
        "persistence": balance.configured(),
    }


def selectable_groups(operator, user):
    with quota.connect() as conn:
        values = {
            r["key"]: r["value"]
            for r in conn.execute(
                "SELECT key,value FROM options WHERE key=ANY(%s)",
                (
                    [
                        "GroupRatio",
                        "UserUsableGroups",
                        "group_ratio_setting.group_special_usable_group",
                    ],
                ),
            )
        }
    try:
        ratios = config_map(values, "GroupRatio", DEFAULT_GROUP_RATIO)
        allowed = config_map(values, "UserUsableGroups", DEFAULT_USABLE_GROUPS)
        special = (
            config_map(values, "group_ratio_setting.group_special_usable_group").get(
                user["group"]
            )
            or {}
        )
        for name, desc in special.items():
            if name.startswith("-:"):
                allowed.pop(name[2:], None)
            else:
                allowed[name[2:] if name.startswith("+:") else name] = desc
        allowed[user["group"]] = "用户分组"
        return sorted(set(ratios) & set(allowed))
    except (ValueError, TypeError, AttributeError):
        raise ManagementError("New API 分组配置无效。") from None


def token_owner(operator, token_id):
    token_id = positive_id(token_id)
    with quota.connect() as conn:
        row = conn.execute(
            "SELECT user_id FROM tokens WHERE id=%s AND deleted_at IS NULL", (token_id,)
        ).fetchone()
    if not row:
        raise ManagementError("KEY 不存在或已删除。")
    return target(operator, row["user_id"]), token_id


def token_columns():
    return (
        """t.id,t.user_id,t.name,t.status,t.created_time,t.accessed_time,t.expired_time,
          t.remain_quota,t.used_quota,t.unlimited_quota,t.model_limits_enabled,t.model_limits,t.allow_ips,
          t."group" AS token_group,t.cross_group_retry,u.username,u.display_name,u."group" AS user_group,u.status AS user_status,
          CASE WHEN length(t.key)>8 THEN substring(t.key,1,4)||'**********'||right(t.key,4) ELSE '********' END AS masked_key,"""
        + effective_status()
        + " AS effective_status"
    )


def money_fields(row):
    for field in ("quota", "used_quota", "remain_quota"):
        if field in row:
            row[field + "_yuan"] = str(Decimal(row[field]) / quota.QUOTA_PER_YUAN)
    return row


def list_grouped(username, body):
    """Page by user, then fetch every matching KEY for those users in one query.

    Only users owning matching KEYs are included. Counts and child rows use one
    snapshot; neither a join LIMIT nor a per-user query can split a user.
    """
    if not isinstance(body, dict):
        raise ManagementError("筛选条件必须是对象。")
    operator = actor(username)
    values = {"token_statuses": [1, 2, 3, 4], **body}
    token_where, token_params = filters(operator, values, tokens=True)
    user_where, user_params = filters(operator, {**values, "search": ""})
    exists = (
        "EXISTS (SELECT 1 FROM tokens t WHERE t.user_id=u.id AND " + token_where + ")"
    )
    user_where += " AND " + exists
    user_params.extend(token_params)
    page = positive_id(values.get("page", 1))
    size = min(positive_id(values.get("page_size", 50)), 100)
    with quota.connect() as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        total = conn.execute(
            "SELECT count(*) AS n FROM users u WHERE " + user_where, user_params
        ).fetchone()["n"]
        users = conn.execute(
            """SELECT u.id,u.username,u.display_name,u."group" AS user_group,u.role,u.status,
            u.quota,u.used_quota,u.remark,
            (SELECT count(*) FROM tokens k WHERE k.user_id=u.id AND k.deleted_at IS NULL) AS token_count
            FROM users u WHERE """
            + user_where
            + " ORDER BY u.id DESC LIMIT %s OFFSET %s",
            (*user_params, size, (page - 1) * size),
        ).fetchall()
        total_keys = conn.execute(
            "SELECT count(*) AS n FROM tokens t JOIN users u ON u.id=t.user_id WHERE "
            + token_where,
            token_params,
        ).fetchone()["n"]
        children = (
            conn.execute(
                "SELECT "
                + token_columns()
                + " FROM tokens t JOIN users u ON u.id=t.user_id WHERE "
                + token_where
                + " AND t.user_id=ANY(%s) ORDER BY t.user_id DESC,t.id DESC",
                (*token_params, [u["id"] for u in users]),
            ).fetchall()
            if users
            else []
        )
    by_user = {}
    for user in users:
        money_fields(user)
        user["tokens"] = []
        by_user[user["id"]] = user
    for token in children:
        by_user[token["user_id"]]["tokens"].append(money_fields(token))
    return {
        "rows": users,
        "total": total,
        "total_keys": total_keys,
        "page": page,
        "page_size": size,
    }


def detail(username, kind, target_id):
    operator = actor(username)
    if kind == "user":
        user = target(operator, target_id)
        data = call_api(operator, operator["id"], f"/api/user/{user['id']}")
        safe = {k: data.get(k, "") for k in USER_FIELDS}
        safe.update(id=user["id"], status=user["status"], role=user["role"])
    else:
        user, token_id = token_owner(operator, target_id)
        data = call_api(operator, user["id"], f"/api/token/{token_id}")
        safe = {k: data.get(k) for k in TOKEN_FIELDS}
        safe["user_id"] = user["id"]
        safe["remain_quota_yuan"] = str(
            Decimal(data["remain_quota"]) / quota.QUOTA_PER_YUAN
        )
    safe["available_groups"] = selectable_groups(operator, user)
    return safe


def token_group_options(username, token_id):
    """Read permitted groups without an upstream request or creating an owner PAT."""
    operator = actor(username)
    user, token_id = token_owner(operator, token_id)
    with quota.connect() as conn:
        row = conn.execute(
            'SELECT "group" FROM tokens WHERE id=%s AND user_id=%s AND deleted_at IS NULL',
            (token_id, user["id"]),
        ).fetchone()
    if not row:
        raise ManagementError("KEY 不存在或已删除。")
    return {
        "id": token_id,
        "group": row["group"],
        "user_status": user["status"],
        "available_groups": selectable_groups(operator, user),
    }


def available_user_groups():
    """Account groups use global ratios, not a KEY owner's usable-group policy."""
    with quota.connect() as conn:
        values = {
            row["key"]: row["value"]
            for row in conn.execute(
                "SELECT key,value FROM options WHERE key='GroupRatio'"
            )
        }
    return sorted(
        group
        for group in config_map(values, "GroupRatio", DEFAULT_GROUP_RATIO)
        if isinstance(group, str)
        and group.strip()
        and group != "auto"
        and len(group) <= 64
    )


def user_group_options(username, user_id):
    """Opening the user-group menu never calls New API or provisions a PAT."""
    operator = actor(username)
    user = target(operator, user_id)
    return {
        "id": user["id"],
        "username": user["username"],
        "group": user["group"] or "",
        "available_groups": available_user_groups(),
        "persistence": balance.configured(),
    }


def validate_user_group(changes):
    group = changes.get("group")
    if (
        set(changes) != {"group"}
        or not isinstance(group, str)
        or len(group) > 64
        or group not in available_user_groups()
    ):
        raise ManagementError("请选择 New API 已配置的用户组，只能修改用户组字段。")
    return group


def amount_units(value):
    # Reuse exact, bounded Decimal validation, including pathological inputs.
    raw = str(value)
    try:
        zero = Decimal(raw) if len(raw) <= 128 else None
        if (
            zero is not None
            and zero.is_finite()
            and zero.is_zero()
            and -128 <= zero.as_tuple().exponent <= 128
        ):
            return 0
    except (DecimalException, ValueError):
        pass
    return quota.validate_request(
        {"user_ids": [1], "mode": "add", "amount_yuan": value}
    )[2]


def token_payload(data):
    return {k: data[k] for k in TOKEN_FIELDS if k in data and data[k] is not None}


def validate_group(operator, user, value):
    if not isinstance(value, str) or len(value) > 64:
        raise ManagementError("KEY 分组无效。")
    if value not in ("", "auto") and value not in selectable_groups(operator, user):
        raise ManagementError("目标 KEY 分组对所属用户不可用。")


def _validate_token_fields(changes):
    for field in ("unlimited_quota", "model_limits_enabled", "cross_group_retry"):
        if field in changes and type(changes[field]) is not bool:
            raise ManagementError("KEY 开关字段须为布尔值。")
    for field in ("name", "group", "model_limits", "allow_ips"):
        if field in changes and (
            not isinstance(changes[field], str) or len(changes[field]) > 10000
        ):
            raise ManagementError("KEY 文本字段无效。")
    if "name" in changes and len(changes["name"].encode()) > 50:
        raise ManagementError("KEY 名称不能超过 50 字节。")
    if "expired_time" in changes and (
        type(changes["expired_time"]) is not int or changes["expired_time"] < -1
    ):
        raise ManagementError("KEY 到期时间无效。")
    if "auto_groups" in changes and (
        not isinstance(changes["auto_groups"], list)
        or any(not isinstance(g, str) or len(g) > 64 for g in changes["auto_groups"])
    ):
        raise ManagementError("自动分组范围无效。")


def _perform(operator, kind, object_id, action, changes, before=None):
    if kind == "user":
        if action == "create":
            if operator["role"] != 100:
                raise Forbidden("新增用户仅允许超级管理员操作。")
            body = dict(changes)
            body["role"] = 1
            return call_api(
                operator, operator["id"], "/api/user/", method="POST", body=body
            )
        user = target(operator, object_id)
        if action in ("enable", "disable", "delete"):
            if user["role"] == 100 and action in ("disable", "delete"):
                raise Forbidden("不能禁用或删除超级管理员。")
            return call_api(
                operator,
                operator["id"],
                "/api/user/manage",
                method="POST",
                body={"id": user["id"], "action": action},
            )
        if action == "group":
            group = validate_user_group(changes)
        data = call_api(operator, operator["id"], f"/api/user/{user['id']}")
        payload = {k: data.get(k, "") for k in USER_FIELDS}
        payload.update(id=user["id"], role=user["role"])
        if action == "password":
            password = changes.get("password")
            if (
                not isinstance(password, str)
                or not 8 <= len(password.encode()) <= 72
                or password != changes.get("password_confirm")
            ):
                raise ManagementError("密码须为 8–72 字节，且两次输入一致。")
            payload["password"] = password
        elif action == "edit":
            if set(changes) - set(USER_FIELDS):
                raise ManagementError("不允许修改该用户字段。")
            if any(not isinstance(v, str) for v in changes.values()):
                raise ManagementError("用户资料字段须为文本。")
            payload.update(changes)
            if (
                not payload["username"].strip()
                or len(payload["username"]) > 20
                or len(payload["display_name"]) > 20
                or len(payload["remark"]) > 255
            ):
                raise ManagementError("用户名、显示名或备注长度无效。")
        elif action == "group":
            if data.get("group") == group:
                raise Conflict("用户已在目标组，请刷新用户列表。")
            # Preserve the latest role as well as other profile fields; the
            # group shortcut must never reset a concurrent role modification.
            role = data.get("role", user["role"])
            if type(role) is not int:
                raise ManagementError("New API 用户资料响应无效。")
            if (
                operator["role"] != 100
                and operator["role"] <= role
                and operator["id"] != user["id"]
            ):
                raise Forbidden("无权管理同级或更高权限用户。")
            payload.update(group=group, role=role)
        else:
            raise ManagementError("用户操作无效。")
        return call_api(
            operator, operator["id"], "/api/user/", method="PUT", body=payload
        )
    user = (
        target(operator, object_id, enabled=True)
        if action == "create"
        else token_owner(operator, object_id)[0]
    )
    if action == "create":
        allowed = {
            "name",
            "group",
            "expired_time",
            "amount_yuan",
            "unlimited_quota",
            "model_limits_enabled",
            "model_limits",
            "allow_ips",
            "cross_group_retry",
            "auto_groups",
        }
        if set(changes) - allowed:
            raise ManagementError("不允许指定 KEY 或修改归属字段。")
        _validate_token_fields(changes)
        validate_group(operator, user, changes.get("group", ""))
        payload = dict(changes)
        payload["remain_quota"] = amount_units(payload.pop("amount_yuan", "0"))
        payload.setdefault("expired_time", -1)
        return call_api(
            operator, user["id"], "/api/token/", method="POST", body=payload
        )
    token_id = positive_id(object_id)
    if action == "reveal":
        data = call_api(
            operator, user["id"], f"/api/token/{token_id}/key", method="POST", body={}
        )
        if not isinstance(data, dict) or not isinstance(data.get("key"), str):
            raise ManagementError("KEY 查看响应无效。")
        return {"key": data["key"]}
    if action == "delete":
        return call_api(operator, user["id"], f"/api/token/{token_id}", method="DELETE")
    data = call_api(operator, user["id"], f"/api/token/{token_id}")
    if action in ("enable", "disable"):
        return call_api(
            operator,
            user["id"],
            "/api/token/?status_only=true",
            method="PUT",
            body={
                "id": token_id,
                "status": 1 if action == "enable" else 2,
                "unlimited_quota": True,
            },
        )
    # Official PUT overwrites remain_quota. Re-read immediately before update;
    # UI must explicitly warn it cannot be atomic against concurrent inference.
    payload = token_payload(data)
    if before and any(data.get(k) != v for k, v in before.items()):
        raise Conflict("KEY 分组或配置已变化，请重新预览。")
    if action == "group":
        validate_group(operator, user, changes.get("group"))
        payload["group"] = changes["group"]
        if payload["group"] != "auto":
            payload["cross_group_retry"] = False
            payload["auto_groups"] = []
    elif action == "quota":
        mode = changes.get("mode")
        if mode == "unlimited":
            payload["unlimited_quota"] = True
        else:
            units = amount_units(changes.get("amount_yuan"))
            if mode != "set" and data["unlimited_quota"]:
                raise ManagementError("无限额度 KEY 请先设置有限额度。")
            if mode == "set":
                payload["remain_quota"] = units
                payload["unlimited_quota"] = False
            elif mode in ("add", "subtract"):
                payload["remain_quota"] = data["remain_quota"] + (
                    units if mode == "add" else -units
                )
            else:
                raise ManagementError("KEY 额度操作无效。")
            if payload["remain_quota"] < 0:
                raise ManagementError("KEY 剩余额度不能为负数。")
    elif action == "edit":
        allowed = {
            "name",
            "expired_time",
            "model_limits_enabled",
            "model_limits",
            "allow_ips",
            "cross_group_retry",
            "auto_groups",
            "group",
        }
        if set(changes) - allowed:
            raise ManagementError("不允许修改该 KEY 字段。")
        if "group" in changes:
            validate_group(operator, user, changes["group"])
        _validate_token_fields(changes)
        payload.update(changes)
        if not isinstance(payload["name"], str) or len(payload["name"].encode()) > 50:
            raise ManagementError("KEY 名称不能超过 50 字节。")
        if type(payload["expired_time"]) is not int or payload["expired_time"] < -1:
            raise ManagementError("到期时间无效。")
    else:
        raise ManagementError("KEY 操作无效。")
    result = call_api(operator, user["id"], "/api/token/", method="PUT", body=payload)
    # Never return upstream key/password/credential-bearing responses.
    return {"updated": True} if result is not None else None


def _audit_ready():
    if not balance.configured():
        raise ManagementError("用户管理写操作需要独立监控数据库保存审计。")
    balance.require_schema()


def _record_pat_creation(operator, user):
    operation_id = uuid.uuid4()
    identity = {"id": user["id"], "username": user["username"]}
    with balance.connect() as conn:
        conn.execute(
            "INSERT INTO user_management_operations(id,operator_id,operator_name,action,parameters,state,expires_at,confirmed_at,finished_at) VALUES(%s,%s,%s,'user.pat_create',%s,'completed',now(),now(),now())",
            (
                operation_id,
                operator["id"],
                operator["username"],
                Jsonb({"target_user": identity}),
            ),
        )
        conn.execute(
            "INSERT INTO user_management_operation_items(operation_id,target_type,target_id,user_id,after_data,state,started_at,finished_at) VALUES(%s,'user',%s,%s,%s,'success',now(),now())",
            (
                operation_id,
                user["id"],
                user["id"],
                Jsonb({"pat_created": True, "username": user["username"]}),
            ),
        )


def _safe_changes(changes):
    return {
        k: v
        for k, v in changes.items()
        if k not in ("password", "password_confirm", "key", "access_token")
    }


def single_action(username, kind, object_id, body):
    operator = actor(username)
    action = body.get("action")
    changes = body.get("changes", {})
    if not isinstance(changes, dict):
        raise ManagementError("变更内容无效。")
    if kind not in ("user", "token"):
        raise ManagementError("对象类型无效。")
    if action == "create" and kind == "user":
        if (
            set(changes) - {"username", "display_name", "password"}
            or not isinstance(changes.get("password"), str)
            or not 8 <= len(changes["password"].encode()) <= 72
        ):
            raise ManagementError("新用户资料或密码无效。")
        # The upstream create response need not contain an ID. The administrator
        # is the actor, never a substitute for the newly requested user.
        user_id = 0
        identity = {"id": None, "username": changes.get("username", "")}
        target_id = 0
    elif action == "create":
        user_id = positive_id(object_id)
        user = target(operator, user_id, enabled=True)
        identity = {"id": user_id, "username": user["username"]}
        target_id = 0
    elif kind == "user":
        user = target(operator, object_id)
        user_id = user["id"]
        identity = {"id": user_id, "username": user["username"]}
        target_id = user_id
    else:
        user, target_id = token_owner(operator, object_id)
        user_id = user["id"]
        identity = {"id": user_id, "username": user["username"]}
    if kind == "user" and action == "group":
        if validate_user_group(changes) == user["group"]:
            raise Conflict("用户已在目标组，请刷新用户列表。")
    _audit_ready()
    operation_id = uuid.uuid4()
    safe = _safe_changes(changes)
    snapshot = {}
    if target_id:
        with quota.connect() as source:
            if kind == "user":
                snapshot = (
                    source.execute(
                        'SELECT username,display_name,"group",remark,status FROM users WHERE id=%s',
                        (target_id,),
                    ).fetchone()
                    or {}
                )
            else:
                snapshot = (
                    source.execute(
                        'SELECT name,"group",status,remain_quota,unlimited_quota,expired_time FROM tokens WHERE id=%s',
                        (target_id,),
                    ).fetchone()
                    or {}
                )
    with balance.connect() as conn:
        conn.execute(
            "INSERT INTO user_management_operations(id,operator_id,operator_name,action,parameters,state,expires_at,confirmed_at) VALUES(%s,%s,%s,%s,%s,'running',now()+interval '15 minutes',now())",
            (
                operation_id,
                operator["id"],
                operator["username"],
                kind + "." + str(action),
                Jsonb(safe),
            ),
        )
        conn.execute(
            "INSERT INTO user_management_operation_items(operation_id,target_type,target_id,user_id,before_data,after_data,state,started_at) VALUES(%s,%s,%s,%s,%s,%s,'sending',now())",
            (
                operation_id,
                kind,
                target_id,
                user_id,
                Jsonb(snapshot),
                Jsonb({**safe, "target_user": identity}),
            ),
        )
    try:
        result = _perform(operator, kind, object_id, action, changes)
    except Exception as exc:
        state = (
            "unknown"
            if isinstance(exc, Uncertain) or not isinstance(exc, ManagementError)
            else "conflict"
            if isinstance(exc, Conflict)
            else "failed"
        )
        message = (
            str(exc)
            if isinstance(exc, ManagementError)
            else "执行异常，请核对实际结果。"
        )
        _finish(operation_id, target_id, state, message)
        raise
    _finish(operation_id, target_id, "success", "")
    return {
        "operation_id": str(operation_id),
        "result": result if action == "reveal" else None,
        "ok": True,
    }


def _finish(operation_id, target_id, state, message):
    with balance.connect() as conn:
        conn.execute(
            "SELECT id FROM user_management_operations WHERE id=%s FOR UPDATE",
            (operation_id,),
        )
        conn.execute(
            "UPDATE user_management_operation_items SET state=%s,message=%s,finished_at=now() WHERE operation_id=%s AND target_id=%s",
            (state, message, operation_id, target_id),
        )
        remaining = conn.execute(
            """SELECT (SELECT count(*) FROM user_management_operation_targets WHERE operation_id=%s)
            + (SELECT count(*) FROM user_management_operation_items WHERE operation_id=%s AND state='sending') AS n""",
            (operation_id, operation_id),
        ).fetchone()["n"]
        if not remaining:
            failed = conn.execute(
                "SELECT count(*) AS n FROM user_management_operation_items WHERE operation_id=%s AND state<>'success'",
                (operation_id,),
            ).fetchone()["n"]
            conn.execute(
                "UPDATE user_management_operations SET state=%s,finished_at=now() WHERE id=%s",
                ("partial" if failed else "completed", operation_id),
            )


def preview_group(username, body):
    operator = actor(username)
    _audit_ready()
    group = body.get("target_group")
    if not isinstance(group, str) or len(group) > 64:
        raise ManagementError("请选择目标 KEY 分组。")
    if body.get("all_filtered"):
        where, params = filters(operator, body.get("filters", {}), tokens=True)
        with quota.connect() as conn:
            rows = conn.execute(
                'SELECT t.id,t.user_id,t."group",t.status,u.username,u.role,u.status AS user_status,u."group" AS user_group FROM tokens t JOIN users u ON u.id=t.user_id WHERE '
                + where
                + " ORDER BY t.id",
                params,
            ).fetchall()
    else:
        ids = _ids(body.get("token_ids"))
        if not ids:
            raise ManagementError("请至少选择一个 KEY。")
        with quota.connect() as conn:
            rows = conn.execute(
                'SELECT t.id,t.user_id,t."group",t.status,u.username,u.role,u.status AS user_status,u."group" AS user_group FROM tokens t JOIN users u ON u.id=t.user_id WHERE t.id=ANY(%s) AND t.deleted_at IS NULL AND u.deleted_at IS NULL ORDER BY t.id',
                (ids,),
            ).fetchall()
        if len(rows) != len(ids):
            raise Conflict("所选 KEY 有记录已删除，请刷新。")
    if not rows:
        raise ManagementError("筛选结果没有 KEY。")
    groups_by_user_group = {}
    for row in rows:
        if (
            operator["role"] != 100
            and operator["role"] <= row["role"]
            and operator["id"] != row["user_id"]
        ):
            raise Forbidden("所选 KEY 包含无权管理的用户。")
        if row["user_status"] != 1:
            raise ManagementError("所选 KEY 包含禁用用户，请取消选择或先启用用户。")
        ug = row["user_group"]
        if ug not in groups_by_user_group:
            groups_by_user_group[ug] = selectable_groups(operator, {"group": ug})
        if group not in ("", "auto") and group not in groups_by_user_group[ug]:
            raise ManagementError(f"目标分组对用户 ID {row['user_id']} 不可用。")
    operation_id = uuid.uuid4()
    with balance.connect() as conn:
        # Expired previews have no request journal and cannot be executed.
        conn.execute(
            """DELETE FROM user_management_operations o WHERE id IN
            (SELECT id FROM user_management_operations WHERE state='preview' AND expires_at<now()
             FOR UPDATE SKIP LOCKED)
            AND NOT EXISTS (SELECT 1 FROM user_management_operation_items i WHERE i.operation_id=o.id)"""
        )
        conn.execute(
            "INSERT INTO user_management_operations(id,operator_id,operator_name,action,parameters,expires_at) VALUES(%s,%s,%s,%s,%s,now()+interval '15 minutes')",
            (
                operation_id,
                operator["id"],
                operator["username"],
                "token.group",
                Jsonb({"group": group}),
            ),
        )
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO user_management_operation_targets(operation_id,target_type,target_id,user_id,before_data,after_data) VALUES(%s,'token',%s,%s,%s,%s)",
                [
                    (
                        operation_id,
                        r["id"],
                        r["user_id"],
                        Jsonb({"group": r["group"], "status": r["status"]}),
                        Jsonb(
                            {
                                "group": group,
                                "target_user": {
                                    "id": r["user_id"],
                                    "username": r["username"],
                                },
                            }
                        ),
                    )
                    for r in rows
                ],
            )
    return {
        "operation_id": str(operation_id),
        "count": len(rows),
        "rows": [
            {
                "id": r["id"],
                "user_id": r["user_id"],
                "username": r["username"],
                "before_group": r["group"],
                "after_group": group,
            }
            for r in rows[:100]
        ],
        "preview_truncated": len(rows) > 100,
    }


def apply_wave(username, operation_id):
    operator = actor(username)
    operation_id = uuid.UUID(str(operation_id))
    with balance.connect() as conn:
        op = conn.execute(
            "SELECT * FROM user_management_operations WHERE id=%s FOR UPDATE",
            (operation_id,),
        ).fetchone()
        if not op or op["operator_id"] != operator["id"]:
            raise Forbidden("不能执行其他管理员的操作。")
        if op["state"] in ("completed", "partial", "cancelled"):
            return {"done": True, "state": op["state"], "results": []}
        if op["state"] == "preview" and op["expires_at"] < datetime.now(timezone.utc):
            raise Conflict("预览已过期，请重新预览。")
        stale = conn.execute(
            "SELECT count(*) AS n FROM user_management_operation_items WHERE operation_id=%s AND state='sending'",
            (operation_id,),
        ).fetchone()["n"]
        if stale:
            raise Conflict("上一组请求尚未结束；中断后请核对操作记录，不要重复执行。")
        if conn.execute(
            "SELECT 1 FROM user_management_operation_items WHERE operation_id=%s AND state IN ('failed','unknown','conflict') LIMIT 1",
            (operation_id,),
        ).fetchone():
            conn.execute(
                "DELETE FROM user_management_operation_targets WHERE operation_id=%s",
                (operation_id,),
            )
            conn.execute(
                "UPDATE user_management_operations SET state='partial',finished_at=now() WHERE id=%s",
                (operation_id,),
            )
            return {"done": True, "state": "partial", "results": []}
        items = conn.execute(
            "SELECT * FROM user_management_operation_targets WHERE operation_id=%s ORDER BY target_id LIMIT 5 FOR UPDATE",
            (operation_id,),
        ).fetchall()
        conn.execute(
            "UPDATE user_management_operations SET state='running',confirmed_at=COALESCE(confirmed_at,now()) WHERE id=%s",
            (operation_id,),
        )
        for item in items:
            conn.execute(
                """INSERT INTO user_management_operation_items
                (operation_id,target_type,target_id,user_id,before_data,after_data,state,started_at)
                VALUES(%s,%s,%s,%s,%s,%s,'sending',now())""",
                (
                    operation_id,
                    item["target_type"],
                    item["target_id"],
                    item["user_id"],
                    Jsonb(item["before_data"]),
                    Jsonb(item["after_data"]),
                ),
            )
            conn.execute(
                "DELETE FROM user_management_operation_targets WHERE operation_id=%s AND target_id=%s",
                (operation_id, item["target_id"]),
            )

    def execute(item):
        try:
            _perform(
                actor(username),
                "token",
                item["target_id"],
                "group",
                op["parameters"],
                item["before_data"],
            )
            state, message = "success", ""
        except Exception as exc:
            state = (
                "unknown"
                if isinstance(exc, Uncertain) or not isinstance(exc, ManagementError)
                else "conflict"
                if isinstance(exc, Conflict)
                else "failed"
            )
            message = (
                str(exc)
                if isinstance(exc, ManagementError)
                else "执行异常，请人工核对。"
            )
        _finish(operation_id, item["target_id"], state, message)
        return {"id": item["target_id"], "state": state, "message": message}

    with ThreadPoolExecutor(max_workers=5) as pool:
        results = list(pool.map(execute, items))
    with balance.connect() as conn:
        count = conn.execute(
            "SELECT count(*) AS n FROM user_management_operation_targets WHERE operation_id=%s",
            (operation_id,),
        ).fetchone()["n"]
        if any(r["state"] != "success" for r in results):
            conn.execute(
                "DELETE FROM user_management_operation_targets WHERE operation_id=%s",
                (operation_id,),
            )
            conn.execute(
                "UPDATE user_management_operations SET state='partial',finished_at=now() WHERE id=%s",
                (operation_id,),
            )
            done = True
        else:
            done = not count
            if done:
                conn.execute(
                    "UPDATE user_management_operations SET state='completed',finished_at=now() WHERE id=%s",
                    (operation_id,),
                )
    return {"results": results, "done": done, "remaining": count}
