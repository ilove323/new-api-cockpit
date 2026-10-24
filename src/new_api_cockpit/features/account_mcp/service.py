"""Own-account totals; never enumerate users or provision credentials."""

from datetime import date, datetime, time, timedelta
from decimal import Decimal
from urllib.parse import urlencode

import psycopg

from ... import quota, report
from . import upstream

COUNT_FIELDS = (
    "request_count",
    "input_tokens",
    "output_tokens",
    "cache_read_tokens",
    "cache_write_tokens",
    "total_tokens",
)
MAX_DAYS = 31
MAX_MODELS = 20
# Reuse the reporting normalization (not a second implementation of cache
# arithmetic). The ownership predicate is INSIDE the source CTE, before any
# metadata, price snapshots, grouping or sorting can touch another user.
if report.SQL.count("/* scope_channels */") != 1:
    raise RuntimeError(
        "The account MCP requires exactly one source ownership predicate"
    )
OWN_SQL = report.SQL.replace("/* scope_channels */", "AND user_id = %(user_id)s")
SUMS = ", ".join(
    f"COALESCE(SUM({field}), 0) AS {field}" for field in (*COUNT_FIELDS, "amount")
)
USAGE_SQL = (
    "WITH own_report AS (" + OWN_SQL + ") SELECT GROUPING(model_name) AS is_total, "
    "model_name, " + SUMS + " FROM own_report GROUP BY GROUPING SETS ((),(model_name)) "
    "ORDER BY is_total DESC, amount DESC, model_name LIMIT 21"
)


def balance(user):
    if any(
        type(user.get(field)) is not int
        for field in ("quota", "used_quota", "request_count")
    ):
        raise upstream.AccountError("账户余额响应不完整。")
    return {
        "user": {"id": user["id"], "username": user["username"]},
        "remaining_quota": user["quota"],
        "used_quota": user["used_quota"],
        "remaining_amount": str(Decimal(user["quota"]) / quota.QUOTA_PER_YUAN),
        "used_amount": str(Decimal(user["used_quota"]) / quota.QUOTA_PER_YUAN),
        "request_count": user["request_count"],
        **amount_unit(),
    }


def amount_unit():
    return {
        "quota_per_unit": int(quota.QUOTA_PER_YUAN),
        "amount_unit": "Cockpit 账面单位（quota / 500000），未做货币或汇率转换",
    }


def boundary(value):
    if not isinstance(value, str) or not 1 <= len(value) <= 40:
        raise ValueError("时间必须为 ISO 日期或带时区的 ISO 时间。")
    try:
        if len(value) == 10:
            return datetime.combine(date.fromisoformat(value), time(), report.TZ)
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None or parsed.microsecond:
            raise ValueError()
        return parsed.astimezone(report.TZ)
    except (ValueError, OverflowError):
        raise ValueError("时间必须为 ISO 日期或带时区、精确到秒的 ISO 时间。") from None


def period(arguments, *, now=None):
    if not isinstance(arguments, dict) or set(arguments) - {"start", "end"}:
        raise ValueError("只接受 start 和 end；不能指定用户、KEY 或上游地址。")
    if ("start" in arguments) != ("end" in arguments):
        raise ValueError("start 和 end 必须同时提供。")
    now = (now or datetime.now(report.TZ)).replace(microsecond=0)
    start, end = (
        (boundary(arguments["start"]), boundary(arguments["end"]))
        if arguments
        else (now.replace(hour=0, minute=0, second=0), now)
    )
    if end <= start or end - start > timedelta(days=MAX_DAYS) or end > now:
        raise ValueError("查询范围须大于 0、最长 31 天，结束时间不能晚于当前时间。")
    return start, end


def usage(user, credential, arguments):
    start, end = period(arguments)
    params = {
        "start_timestamp": int(start.timestamp()),
        "end_timestamp": int(end.timestamp()) - 1,
    }
    # New API, not a local role or hash lookup, enforces usage:read, expiration,
    # revocation, and its configured log-range policy on every tool invocation.
    upstream.read("/api/log/self/stat?" + urlencode(params), credential)
    try:
        with quota.connect() as conn:
            rows = conn.execute(
                USAGE_SQL,
                {
                    "user_id": user["id"],
                    "start": int(start.timestamp()),
                    "end": int(end.timestamp()),
                    "by_token": False,
                    "token_ids": None,
                    "groups": None,
                },
            ).fetchall()
    except psycopg.Error:
        raise upstream.AccountError("用量查询暂不可用，未自动重试。") from None
    totals, models = None, []
    for row in rows:
        item = {field: int(row[field]) for field in COUNT_FIELDS}
        item["amount"] = str(row["amount"])
        if row["is_total"]:
            totals = item
        else:
            models.append({"model": row["model_name"], **item})
    if totals is None:
        raise upstream.AccountError("用量汇总响应无效。")
    return {
        "user": {"id": user["id"], "username": user["username"]},
        "start": start.isoformat(),
        "end_exclusive": end.isoformat(),
        "timezone": "Asia/Shanghai",
        "totals": totals,
        "models": models,
        "model_limit": MAX_MODELS,
        "models_may_be_truncated": len(models) == MAX_MODELS,
        "notes": "仅消费日志 type=2；起点包含、终点不含。总计涵盖全部模型，模型列表按金额最多列 20 个；缓存去重与 Cockpit 统计页一致，不返回提示词或明细日志。",
        **amount_unit(),
    }
