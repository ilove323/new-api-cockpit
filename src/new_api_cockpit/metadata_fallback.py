"""Exceptional-path aggregation for corrupt metadata, compatible with PG 15+.

The ordinary query remains PostgreSQL-aggregated. On metadata cast failures a
server cursor streams the same filtered interval, retaining every billed quota.
No source DDL/functions, guessed cache counts or current-price substitutions.
"""

import json
from decimal import Decimal, DecimalException

TOKENS = (
    "raw_input_tokens",
    "input_tokens",
    "output_tokens",
    "cache_read_tokens",
    "cache_write_tokens",
    "pricing_input_tokens",
    "total_tokens",
)
SNAPSHOT_KEYS = (
    "expr_b64",
    "matched_tier",
    "model_price",
    "model_ratio",
    "completion_ratio",
    "cache_ratio",
    "cache_creation_ratio_5m",
    "cache_creation_ratio",
)
RAW_SQL = """SELECT id,created_at,user_id,username,token_id,token_name,model_name,
    COALESCE("group",'') AS group_name,quota,prompt_tokens,completion_tokens,other
    FROM logs WHERE type=%(kind)s AND created_at >= %(start)s AND created_at < %(end)s
    /* scope_channels */
    AND (%(token_ids)s::bigint[] IS NULL OR token_id=ANY(%(token_ids)s::bigint[]))
    AND (%(groups)s::text[] IS NULL OR COALESCE("group",'')=ANY(%(groups)s::text[]))"""


def metadata(raw):
    def invalid_constant(_):
        raise ValueError()

    value = (
        json.loads(
            raw.strip() or "{}", parse_constant=invalid_constant, parse_float=Decimal
        )
        if raw
        else {}
    )
    if not isinstance(value, dict):
        raise ValueError()
    return value


def text(value):
    # Match jsonb ->> representation for scalar snapshot fields.
    if value is None:
        return None
    if isinstance(value, (str, Decimal)):
        return str(value)
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def numeric(value, default, integer=False):
    raw = text(value)
    if raw is None or raw == "":
        return default
    if len(raw) > 128:
        raise ValueError()
    number = Decimal(raw)
    if not number.is_finite() or number < 0 or abs(number.as_tuple().exponent) > 128:
        raise ValueError()
    if integer:
        if number != number.to_integral_value() or number > 9223372036854775807:
            raise ValueError()
        return int(number)
    return number


def aggregate(records, by_token, *, failures=False):
    groups, unknown = {}, {}
    for r in records:
        token_id = r["token_id"] if by_token else 0
        token_name = (r["token_name"] or "").strip() or "未知令牌" if by_token else ""
        dimension = (r["user_id"], r["username"], token_id, token_name, r["model_name"])
        latest = (r["created_at"], r["id"])
        if failures:
            try:
                o = metadata(r["other"])
                code = text(o.get("status_code")) or "未知"
            except (ValueError, TypeError, RecursionError):
                code = "未知"
            # Failure SQL groups by token ID, not its changing name.
            key = (r["user_id"], r["username"], token_id, r["model_name"], code)
            row = groups.setdefault(
                key,
                dict(
                    user_id=r["user_id"],
                    username=r["username"],
                    token_id=token_id,
                    token_name=token_name,
                    model_name=r["model_name"],
                    status_code=code,
                    failure_count=0,
                    latest_at=0,
                    latest_id=0,
                ),
            )
            row["failure_count"] += 1
            if latest > (row["latest_at"], row["latest_id"]):
                row.update(
                    latest_at=latest[0], latest_id=latest[1], token_name=token_name
                )
            continue
        p, c = r["prompt_tokens"] or 0, r["completion_tokens"] or 0
        separate = r["model_name"].lower().startswith("claude")
        amount = Decimal(r["quota"]) / 500000
        try:
            o = metadata(r["other"])
            cr = numeric(o.get("cache_tokens"), 0, True)
            cw = numeric(
                o.get("cache_write_tokens")
                if o.get("cache_write_tokens") not in (None, "")
                else o.get("cache_creation_tokens"),
                0,
                True,
            )
            ratio = numeric(o.get("group_ratio"), Decimal(1))
            matched = text(o.get("matched_tier")) or ""
            snapshot = {key: text(o.get(key)) for key in SNAPSHOT_KEYS}
        except (ValueError, TypeError, DecimalException, RecursionError):
            row = unknown.setdefault(
                dimension,
                dict(
                    zip(
                        ("user_id", "username", "token_id", "token_name", "model_name"),
                        dimension,
                    )
                ),
            )
            if "amount" not in row:
                row.update(
                    request_count=0,
                    metadata_error_count=0,
                    amount=Decimal(0),
                    ratio_count=0,
                    group_ratio=None,
                    total_tokens=None if separate else 0,
                    raw_input_tokens=0,
                    input_tokens=0 if separate else None,
                    output_tokens=0,
                    cache_read_tokens=None,
                    cache_write_tokens=None,
                    pricing_input_tokens=0 if separate else None,
                )
            row["amount"] += amount
            row["request_count"] += 1
            row["metadata_error_count"] += 1
            row["raw_input_tokens"] += p
            row["output_tokens"] += c
            if separate:
                row["input_tokens"] += p
                row["pricing_input_tokens"] += p
            else:
                row["total_tokens"] += p + c
            continue
        bucket_key = (matched, r["group_name"], ratio, tuple(snapshot.items()))
        entry = groups.setdefault(
            dimension, {"buckets": {}, "latest": (-1, -1), "ratio": None}
        )
        bucket = entry["buckets"].setdefault(
            bucket_key,
            dict(
                matched_tier=matched,
                group_name=r["group_name"],
                group_ratio=str(ratio),
                price_snapshot=snapshot,
                request_count=0,
                amount=Decimal(0),
                latest_at=0,
                latest_id=0,
                **dict.fromkeys(TOKENS, 0),
            ),
        )
        clean = p if separate else max(p - cr - cw, 0)
        values = (p, clean, c, cr, cw, clean, p + c + cr + cw if separate else p + c)
        for field, value in zip(TOKENS, values):
            bucket[field] += value
        bucket["request_count"] += 1
        bucket["amount"] += amount
        if latest > (bucket["latest_at"], bucket["latest_id"]):
            bucket.update(latest_at=latest[0], latest_id=latest[1])
        if latest > entry["latest"]:
            entry.update(latest=latest, ratio=ratio)
    if failures:
        return list(groups.values())
    rows = []
    for dimension, entry in groups.items():
        buckets = sorted(
            entry["buckets"].values(),
            key=lambda b: (
                b["matched_tier"],
                b["group_name"],
                Decimal(b["group_ratio"]),
            ),
        )
        rows.append(
            dict(
                zip(
                    ("user_id", "username", "token_id", "token_name", "model_name"),
                    dimension,
                )
            )
            | dict(
                tier_usage=buckets,
                amount=sum((b["amount"] for b in buckets), Decimal(0)),
                request_count=sum(b["request_count"] for b in buckets),
                group_ratio=entry["ratio"],
                ratio_count=len({Decimal(b["group_ratio"]) for b in buckets}),
                **{field: sum(b[field] for b in buckets) for field in TOKENS},
            )
        )
    rows.extend(unknown.values())
    return sorted(
        rows,
        key=lambda r: (
            r["username"],
            r["user_id"],
            r["token_name"],
            r["token_id"] or 0,
            r["total_tokens"] is None,
            -(r["total_tokens"] or 0),
            r["model_name"],
        ),
    )


def load(conn, params, scope_sql, *, failures=False):
    query = scope_sql(RAW_SQL, params["channel_ids"], params["excluded_channel_ids"])
    with conn.cursor(name="metadata_fallback") as cursor:
        cursor.itersize = 1000
        cursor.execute(query, dict(params, kind=5 if failures else 2))
        return aggregate(cursor, params["by_token"], failures=failures)
