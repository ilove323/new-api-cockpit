"""Read-only channel health from logged routing attempts and historical charges.

Official performance metrics describe model/billing-group request outcomes, not
channel tags. This module reconstructs logged attempts without changing billing,
routing, quotas, or source data. Missing evidence remains unknown.
"""

import json
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

from . import balance
from .historical_prices import request_prices
from .report import TZ, period

SQL = Path(__file__).with_suffix(".sql").read_text()
MAX_FALLBACK_RECORDS = 100000
MAX_FALLBACK_BYTES = 32 * 1024 * 1024


class QualityUnavailable(ValueError):
    """The requested result cannot be produced without inventing data."""


def parameters(args):
    """Validate before accessing either database; all filters are exact matches."""
    if any(key in args for key in ("scope_id", "group", "tier")):
        raise ValueError("渠道质量不按账本、计费分组或档位筛选，请移除这些参数。")
    now = datetime.now(TZ).replace(microsecond=0)
    start = args.get("start") or (now - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%S")
    end = args.get("end") or now.strftime("%Y-%m-%dT%H:%M:%S")
    first, last = period(start, end)
    if last - first > 31 * 86400:
        raise ValueError("渠道质量单次查询最多 31 天，请缩小时间范围。")
    result = dict(start=start, end=end, start_ts=first, end_ts=last)
    for key, values in [
        ("mode", ("channel", "upstream")),
        ("stream", ("all", "stream", "nonstream")),
        ("latency_scope", ("direct", "all")),
        ("channel_status", ("enabled", "all")),
    ]:
        value = args.get(key, values[0])
        if value not in values:
            raise ValueError("无效的渠道质量查询方式。")
        result[key] = value
    for key, name in [
        ("model", "models"),
        ("user", "users"),
        ("token_id", "tokens"),
        ("channel_id", "channels"),
    ]:
        raw = args.getlist(key)
        if len(raw) > 100 or any(len(v) > 256 for v in raw):
            raise ValueError("筛选选项过多或内容过长。")
        if name in ("tokens", "channels"):
            try:
                raw = [int(v) for v in raw]
            except ValueError:
                raise ValueError("渠道或令牌 ID 必须是非负整数。") from None
            if any(v < 0 or v > 2**63 - 1 for v in raw):
                raise ValueError("无效的渠道或令牌 ID。")
        result[name] = sorted(set(raw)) or None
    tag = args.get("tag")
    if tag is not None and len(tag) > 256:
        raise ValueError("渠道标签过长。")
    result["tag"] = tag
    return result


def source_connection():
    return psycopg.connect(
        connect_timeout=8,
        row_factory=dict_row,
        options="-c default_transaction_read_only=on -c statement_timeout=30000",
    )


def catalog(conn, *, include_history=False):
    """Only historical queries need inventory; live identity always wins."""
    stored = {}
    if include_history and balance.configured():
        balance.require_schema()
        with balance.connect() as monitor:
            stored = {
                r["channel_id"]: dict(r)
                for r in monitor.execute("""
                SELECT i.channel_id,i.channel_name,
                  CASE WHEN s.kind='tag' THEN s.tag_value ELSE '' END AS tag_value,
                  NULL::int AS channel_status,true AS is_deleted
                FROM balance_channel_inventory i JOIN balance_scopes s ON s.id=i.scope_id""")
            }
    live = conn.execute(
        """SELECT id AS channel_id,name AS channel_name,status AS channel_status,
            COALESCE(NULLIF(btrim(tag),''),'') AS tag_value,false AS is_deleted FROM channels"""
        + ("" if include_history else " WHERE status=1")
    )
    for row in live:
        stored[row["channel_id"]] = dict(row)
    return list(stored.values())


def query_sql(conn):
    columns = {
        r["column_name"]
        for r in conn.execute("""SELECT column_name FROM information_schema.columns
                 WHERE table_schema=current_schema() AND table_name='logs' """)
    }
    required = {
        "id",
        "created_at",
        "type",
        "username",
        "token_id",
        "token_name",
        "model_name",
        "channel_id",
        "group",
        "quota",
        "other",
    }
    if not required <= columns:
        raise QualityUnavailable("New API 日志字段不兼容，请核对兼容文档。")
    optional = {
        "request_id": "text",
        "is_stream": "boolean",
        "use_time": "bigint",
        "completion_tokens": "bigint",
    }
    expressions = [
        key if key in columns else f"NULL::{kind} AS {key}"
        for key, kind in optional.items()
    ]
    return SQL.replace("/* optional_columns */", ",".join(expressions) + ","), sorted(
        optional.keys() - columns
    )


def fallback_query(conn, query, params):
    """PG15-safe bounded recovery for broken JSON; never drop bad charges silently."""
    header = query[: query.index("), catalog AS (")]
    raw_query = (
        header.replace(
            "COALESCE(NULLIF(btrim(other),''),'{}')::jsonb AS meta", "other AS meta"
        )
        + ") SELECT * FROM source"
    )
    rows, size = [], 0
    with conn.cursor(name="quality_invalid_metadata") as cursor:
        cursor.execute(raw_query, params)
        for row in cursor:
            size += len(str(row.get("meta") or "").encode("utf-8")) + 512
            if len(rows) >= MAX_FALLBACK_RECORDS or size > MAX_FALLBACK_BYTES:
                raise QualityUnavailable(
                    "区间内有异常日志且数据量过大，请缩小时间范围；没有返回部分统计。"
                )
            try:
                meta = json.loads(row["meta"] or "{}", parse_constant=invalid_constant)
            except (ValueError, TypeError):
                meta = None
            if not isinstance(meta, dict):
                meta = {"cockpit_metadata_invalid": True}
            rows.append({**row, "meta": meta})
    source = """WITH source AS MATERIALIZED (
        SELECT * FROM jsonb_to_recordset(%(fallback)s::jsonb) AS r(
          id bigint,created_at bigint,type int,username text,token_id bigint,token_name text,
          model_name text,channel_id bigint,log_group text,quota bigint,
          request_id text,is_stream boolean,use_time bigint,completion_tokens bigint,meta jsonb)
    """
    recovered = source + query[query.index("), catalog AS (") :]
    return conn.execute(recovered, {**params, "fallback": json.dumps(rows)}).fetchone()[
        "result"
    ]


def invalid_constant(_value):
    raise ValueError("Non-finite JSON constant")


def historical_prices(snapshot):
    """Bad or oversized price metadata must not prevent reporting actual fees."""
    encoded = snapshot.get("expr_b64")
    if encoded and (not isinstance(encoded, str) or len(encoded) > 65536):
        return None
    for key, value in snapshot.items():
        if key not in {"expr_b64", "matched_tier"} and value is not None:
            if isinstance(value, (dict, list, bool)) or len(str(value)) > 128:
                return None
    try:
        prices = request_prices(snapshot)
        if prices and any(
            value is not None and abs(value) > Decimal("1e24")
            for value in prices.values()
        ):
            return None
        return prices
    except (ArithmeticError, TypeError, ValueError):
        return None


def percentage(numerator, denominator):
    return round(numerator * 100 / denominator, 2) if denominator else None


def sorted_failure_codes(codes):
    return dict(
        sorted(
            codes.items(),
            key=lambda item: (
                not item[0].isdigit(),
                int(item[0]) if item[0].isdigit() else item[0],
            ),
        )
    )


def top_models(rows):
    """Combine every channel row before ranking; never average rounded rates."""
    models = {}
    for row in rows:
        if not row["request_count"]:
            continue
        name = row["model_name"]
        model = models.setdefault(
            name,
            dict(model_name=name, request_count=0, success_count=0, failure_count=0),
        )
        for key in ("request_count", "success_count", "failure_count"):
            model[key] += row[key]
    ordered = sorted(
        models.values(),
        key=lambda row: (-row["request_count"], row["model_name"] or ""),
    )[:6]
    for model in ordered:
        model["success_rate"] = percentage(
            model["success_count"], model["success_count"] + model["failure_count"]
        )
    return ordered


def decorate(row, catalog_by_id, mode):
    row = dict(row or {})
    if not row:
        return row
    eligible = row["success_count"] + row["failure_count"]
    row["success_rate"] = percentage(row["success_count"], eligible)
    row["outcome_coverage"] = percentage(
        eligible + row["ignored_count"], row["request_count"]
    )
    row["duration_coverage"] = percentage(row["duration_samples"], row["success_count"])
    row["avg_duration_ms"] = (
        row["duration_total_ms"] / row["duration_samples"]
        if row["duration_samples"]
        else None
    )
    row["frt_coverage"] = percentage(row["frt_samples"], row["success_count"])
    row["tps_coverage"] = percentage(row["tps_samples"], row["success_count"])
    row["avg_tokens_per_second"] = (
        row["tps_output_tokens"] * 1000 / row["tps_duration_ms"]
        if row["tps_duration_ms"] > 0
        else None
    )
    row["sample_insufficient"] = eligible < 20
    if "failure_codes" in row:
        row["failure_codes"] = sorted_failure_codes(row["failure_codes"])
    for kind in ("duration", "frt"):
        p = row.pop(kind + "_percentiles", None) or [None, None]
        row[kind + "_p50_ms"], row[kind + "_p95_ms"] = p
    row.pop("grouping", None)
    if "amount" in row:
        row["amount"] = str(row["amount"])
    if row.get("target") is not None:
        if mode == "channel":
            channel_id = int(row["target"])
            info = catalog_by_id.get(channel_id, {})
            row.update(
                channel_id=channel_id,
                channel_name=info.get("channel_name") or f"渠道 #{channel_id}",
                tag_value=info.get("tag_value", ""),
                channel_status=info.get("channel_status"),
                is_deleted=info.get("is_deleted", channel_id != 0),
            )
        else:
            row["tag_value"] = row["target"]
    for bucket in row.get("pricing_buckets", []):
        snapshot = bucket.pop("snapshot", {})
        prices = historical_prices(snapshot)
        bucket["tier"] = (
            snapshot.get("matched_tier")
            if isinstance(snapshot.get("matched_tier"), str)
            else ""
        )
        bucket["prices"] = (
            {k: str(v) if v is not None else None for k, v in prices.items()}
            if prices
            else None
        )
        bucket["amount"] = str(bucket["amount"])
    return row


def load(args, *, with_trends=False):
    params = parameters(args)
    params["with_trends"] = with_trends
    with source_connection() as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        channels = catalog(conn, include_history=params["channel_status"] == "all")
        query, missing = query_sql(conn)
        params["catalog"] = json.dumps(channels)
        try:
            with conn.transaction():
                result = conn.execute(query, params).fetchone()["result"]
        except psycopg.errors.InvalidTextRepresentation:
            result = fallback_query(conn, query, params)
    if len(result["rows"]) > 1000:
        raise QualityUnavailable(
            "结果超过 1000 个模型/渠道组合，请选择模型或渠道；没有返回截断统计。"
        )
    by_id = {r["channel_id"]: r for r in channels}
    rows = [decorate(r, by_id, params["mode"]) for r in result["rows"]]
    totals = decorate(result["totals"], by_id, params["mode"])
    totals["failure_codes"] = {}
    for row in rows:
        for code, count in row["failure_codes"].items():
            totals["failure_codes"][code] = totals["failure_codes"].get(code, 0) + count
    totals["failure_codes"] = sorted_failure_codes(totals["failure_codes"])
    warnings = [
        "仅统计已经落库的调用结果；日志关闭、清理或未记录的失败无法还原，不代表完整可用率。",
        "明确错误（含 400 等业务拒绝）计入失败，客户端取消不计入成功率分母；费用仍取消费日志，不是上游采购成本。",
    ]
    if missing:
        warnings.append(
            "源库缺少字段：" + "、".join(missing) + "；对应指标显示无数据。"
        )
    if totals.get("unknown_count"):
        warnings.append(
            f"{totals['unknown_count']} 条调用缺少可靠结果，未放入成功率分母。"
        )
    if totals.get("legacy_count"):
        warnings.append(
            "旧日志无法完整关联渠道尝试；总耗时可能只有整数秒，未伪造毫秒精度。"
        )
    if result["unsupported_records"]:
        warnings.append(f"排除 {result['unsupported_records']} 条非文本/实时会话记录。")
    result.update(
        rows=rows,
        totals=totals,
        top_models=top_models(rows),
        trends=[decorate(r, by_id, params["mode"]) for r in result["trends"]],
        mode=params["mode"],
        start=params["start"],
        end=params["end"],
        start_ts=params["start_ts"],
        end_ts=params["end_ts"],
        warnings=warnings,
        latency_scope=params["latency_scope"],
        channel_status=params["channel_status"],
        updated_at=datetime.now(TZ).isoformat(timespec="seconds"),
    )
    result["options"]["channels"] = [
        row
        for row in channels
        if params["channel_status"] == "all"
        or (row["channel_status"] == 1 and not row["is_deleted"])
    ]
    return result
