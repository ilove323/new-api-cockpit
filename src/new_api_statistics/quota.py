"""Administrator quota changes via New API's atomic add/subtract endpoint.

The reporting application's PostgreSQL connection remains read-only.  Quota
changes are never implemented by overwriting users.quota in this process.
"""

import json
import os
import urllib.error
import urllib.request
from decimal import Decimal, InvalidOperation

import psycopg
from psycopg.rows import dict_row


QUOTA_PER_YUAN = Decimal("500000")
MAX_BATCH = 100
MAX_AMOUNT = Decimal("1000000000")


class QuotaError(ValueError):
    pass


def connect():
    return psycopg.connect(
        connect_timeout=8,
        row_factory=dict_row,
        options="-c default_transaction_read_only=on -c statement_timeout=10000",
    )


def list_users():
    with connect() as conn:
        rows = conn.execute(
            """SELECT id,username,COALESCE(display_name,'') AS display_name,
                      COALESCE("group",'') AS user_group,
                      role,status,quota,used_quota
               FROM users WHERE deleted_at IS NULL ORDER BY id"""
        ).fetchall()
    return [
        {
            "id": row["id"],
            "username": row["username"],
            "display_name": row["display_name"],
            "user_group": row["user_group"],
            "role": row["role"],
            "status": row["status"],
            "quota": row["quota"],
            "quota_yuan": str(Decimal(row["quota"]) / QUOTA_PER_YUAN),
            "used_quota_yuan": str(Decimal(row["used_quota"]) / QUOTA_PER_YUAN),
        }
        for row in rows
    ]


def validate_request(body):
    if not isinstance(body, dict):
        raise QuotaError("请求体必须是对象。")
    ids = body.get("user_ids")
    if not isinstance(ids, list) or not ids or len(ids) > MAX_BATCH:
        raise QuotaError(f"每次请选择 1～{MAX_BATCH} 个用户。")
    if any(type(value) is not int or value <= 0 for value in ids) or len(set(ids)) != len(ids):
        raise QuotaError("用户 ID 必须是互不重复的正整数。")
    mode = body.get("mode")
    if mode not in ("add", "subtract"):
        raise QuotaError("只允许增加或减少额度，不允许覆盖。")
    try:
        amount = Decimal(str(body.get("amount_yuan", "")))
    except (InvalidOperation, ValueError):
        raise QuotaError("请输入有效金额。") from None
    units = amount * QUOTA_PER_YUAN
    if not amount.is_finite() or amount <= 0 or amount > MAX_AMOUNT or units != units.to_integral_value():
        raise QuotaError("金额必须大于 0、最多十亿元，且能精确换算为整数额度单位。")
    return ids, mode, int(units), amount


def _operator(conn, username):
    row = conn.execute(
        """SELECT id,role,status,access_token FROM users
           WHERE username=%s AND deleted_at IS NULL LIMIT 1""",
        (username,),
    ).fetchone()
    if not row or row["role"] < 10 or row["status"] != 1:
        raise QuotaError("管理员账号已失效，请重新登录。")
    if not row["access_token"]:
        raise QuotaError("当前管理员尚未生成 New API PAT，无法通过官方接口调整额度。")
    return row


def _targets(conn, ids, operator):
    rows = conn.execute(
        """SELECT id,username,role,quota FROM users
           WHERE id=ANY(%s) AND deleted_at IS NULL""",
        (ids,),
    ).fetchall()
    by_id = {row["id"]: row for row in rows}
    if len(by_id) != len(ids):
        raise QuotaError("所选用户有账号不存在或已删除，请刷新列表。")
    for user_id in ids:
        target = by_id[user_id]
        if operator["role"] != 100 and operator["role"] <= target["role"]:
            raise QuotaError(f"没有权限调整用户 {target['username']} 的额度。")
    return by_id


def preview(username, body):
    ids, mode, units, amount = validate_request(body)
    with connect() as conn:
        operator = _operator(conn, username)
        targets = _targets(conn, ids, operator)
    delta = units if mode == "add" else -units
    return {
        "mode": mode,
        "amount_yuan": str(amount),
        "users": [
            {
                "id": user_id,
                "username": targets[user_id]["username"],
                "before_yuan": str(Decimal(targets[user_id]["quota"]) / QUOTA_PER_YUAN),
                "estimated_after_yuan": str(
                    Decimal(targets[user_id]["quota"] + delta) / QUOTA_PER_YUAN
                ),
            }
            for user_id in ids
        ],
    }


def _call_manage(operator, user_id, mode, units):
    base = os.environ.get("NEW_API_INTERNAL_URL", "http://new-api:3000").rstrip("/")
    if not base.startswith(("http://", "https://")):
        raise QuotaError("NEW_API_INTERNAL_URL 必须是 HTTP(S) 地址。")
    request = urllib.request.Request(
        base + "/api/user/manage",
        data=json.dumps(
            {"id": user_id, "action": "add_quota", "mode": mode, "value": units}
        ).encode("utf-8"),
        headers={
            "Authorization": "Bearer " + operator["access_token"],
            "New-Api-User": str(operator["id"]),
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            result = json.load(response)
    except urllib.error.HTTPError as exc:
        raise QuotaError(f"New API 返回 HTTP {exc.code}；该用户的操作结果需人工核对。") from None
    except (urllib.error.URLError, TimeoutError, ValueError):
        raise QuotaError("New API 请求结果不明确；不要重试该用户，先核对实际额度和审计日志。") from None
    if not isinstance(result, dict) or not result.get("success"):
        message = result.get("message", "未知错误") if isinstance(result, dict) else "无效响应"
        raise QuotaError(f"New API 拒绝了调整：{str(message)[:180]}")


def apply(username, body):
    ids, mode, units, amount = validate_request(body)
    with connect() as conn:
        operator = _operator(conn, username)
        targets = _targets(conn, ids, operator)
    results = []
    for user_id in ids:
        try:
            _call_manage(operator, user_id, mode, units)
        except QuotaError as exc:
            results.append({"id": user_id, "username": targets[user_id]["username"], "ok": False, "message": str(exc)})
            break  # Never retry or continue after an ambiguous/failed mutation.
        results.append({"id": user_id, "username": targets[user_id]["username"], "ok": True})
    return {
        "mode": mode,
        "amount_yuan": str(amount),
        "results": results,
        "completed": len(results) == len(ids) and all(row["ok"] for row in results),
        "remaining_user_ids": ids[len(results):],
    }
