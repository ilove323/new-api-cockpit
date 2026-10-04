"""Administrator quota changes via New API's atomic add/subtract endpoint.

The reporting application's PostgreSQL connection remains read-only.  Quota
changes are never implemented by overwriting users.quota in this process.
"""

import json
import logging
import os
import urllib.error
import urllib.request
from decimal import Decimal, DecimalException, localcontext
from concurrent.futures import ThreadPoolExecutor

import psycopg
from psycopg.rows import dict_row

from new_api_cockpit import operation_records


QUOTA_PER_YUAN = Decimal("500000")
CONCURRENT_REQUESTS = 5
MAX_AMOUNT = Decimal("1000000000")


class QuotaError(ValueError):
    pass


class QuotaRequestUncertain(QuotaError):
    """A sent mutation may have committed; never automatically retry it."""


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
                      role,status,quota,used_quota,COALESCE(remark,'') AS remark,
                      (SELECT count(*) FROM tokens WHERE user_id=users.id AND deleted_at IS NULL) AS token_count
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
            "remark": row.get("remark", ""),
            "token_count": row.get("token_count", 0),
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
    if not isinstance(ids, list) or not ids:
        raise QuotaError("请至少选择一个用户。")
    if any(type(value) is not int or value <= 0 for value in ids) or len(
        set(ids)
    ) != len(ids):
        raise QuotaError("用户 ID 必须是互不重复的正整数。")
    mode = body.get("mode")
    if mode not in ("add", "subtract"):
        raise QuotaError("只允许增加或减少额度，不允许覆盖。")
    try:
        raw = str(body.get("amount_yuan", ""))
        if len(raw) > 128:
            raise ValueError()
        amount = Decimal(raw)
        # Check finiteness/range BEFORE arithmetic: sNaN and enormous
        # exponents otherwise raise outside the application's JSON handler.
        if not amount.is_finite() or not 0 < amount <= MAX_AMOUNT:
            raise ValueError()
        if amount.as_tuple().exponent < -128:
            raise ValueError()
        with localcontext() as context:
            context.prec = max(32, len(amount.as_tuple().digits) + 6)
            units = amount * QUOTA_PER_YUAN
            if units != units.to_integral_value():
                raise ValueError()
    except (DecimalException, ValueError):
        raise QuotaError("请输入有效金额。") from None
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
        raise QuotaRequestUncertain(
            f"New API 返回 HTTP {exc.code}；该用户的操作结果需人工核对。"
        ) from None
    except (urllib.error.URLError, TimeoutError, ValueError):
        raise QuotaRequestUncertain(
            "New API 请求结果不明确；不要重试该用户，先核对实际额度和审计日志。"
        ) from None
    if not isinstance(result, dict) or type(result.get("success")) is not bool:
        raise QuotaRequestUncertain(
            "New API 响应无效，操作结果不明确，请先核对额度和审计日志。"
        )
    if not result["success"]:
        message = result.get("message", "未知错误")
        raise QuotaError(f"New API 拒绝了调整：{str(message)[:180]}")


def apply(username, body):
    ids, mode, units, amount = validate_request(body)
    with connect() as conn:
        operator = _operator(conn, username)
        targets = _targets(conn, ids, operator)
    operation_id = operation_records.begin_quota(
        operator, username, targets, ids, mode, units
    )

    def attempt(user_id):
        try:
            _call_manage(operator, user_id, mode, units)
        except QuotaError as exc:
            return {
                "id": user_id,
                "username": targets[user_id]["username"],
                "ok": False,
                "state": "unknown"
                if isinstance(exc, QuotaRequestUncertain)
                else "failed",
                "message": str(exc),
            }
        except Exception as exc:
            # Do not leak request credentials or imply that a failed response
            # means the upstream mutation did not happen.
            logging.getLogger(__name__).error(
                "Quota request error for user %s (%s)", user_id, type(exc).__name__
            )
            return {
                "id": user_id,
                "username": targets[user_id]["username"],
                "ok": False,
                "state": "unknown",
                "message": "请求结果不明确，请先核对实际额度和审计日志，不要直接重试。",
            }
        return {
            "id": user_id,
            "username": targets[user_id]["username"],
            "ok": True,
            "state": "success",
        }

    results = []
    with ThreadPoolExecutor(max_workers=CONCURRENT_REQUESTS) as pool:
        for offset in range(0, len(ids), CONCURRENT_REQUESTS):
            wave_ids = ids[offset : offset + CONCURRENT_REQUESTS]
            operation_records.start_quota_wave(operation_id, wave_ids)
            wave = list(pool.map(attempt, wave_ids))
            operation_records.finish_quota(operation_id, wave, final=False)
            results.extend(wave)
            if any(not row["ok"] for row in wave):
                break  # Account for all in-flight requests, but never start another wave.
    operation_records.finish_quota(operation_id, [], final=True)
    return {
        "operation_id": str(operation_id),
        "mode": mode,
        "amount_yuan": str(amount),
        "results": results,
        "completed": len(results) == len(ids) and all(row["ok"] for row in results),
        "remaining_user_ids": ids[len(results) :],
    }
