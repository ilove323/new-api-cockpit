"""Actual channel charges split by user; one read-only source-database query."""

from decimal import Decimal

import psycopg
from psycopg.rows import dict_row

from .report import period

MAX_ROWS = 10000
QUOTA_PER_UNIT = Decimal(500000)
SQL = """
WITH enabled AS MATERIALIZED (
    SELECT id AS channel_id,name AS channel_name FROM channels WHERE status=1
), usage AS (
    SELECT l.channel_id,l.user_id,
        MAX(NULLIF(btrim(l.username),'')) AS logged_username,
        COUNT(*) AS request_count,SUM(COALESCE(l.quota,0))::numeric AS quota
    FROM logs l JOIN enabled c ON c.channel_id=l.channel_id
    WHERE l.type=2 AND l.created_at >= %(start)s AND l.created_at < %(end)s
    GROUP BY l.channel_id,l.user_id
)
SELECT c.channel_id,c.channel_name,g.user_id,g.request_count,g.quota,
    COALESCE(NULLIF(btrim(u.username),''),g.logged_username,'未知用户') AS username,
    COALESCE(u.display_name,'') AS display_name
FROM enabled c LEFT JOIN usage g ON g.channel_id=c.channel_id
LEFT JOIN users u ON u.id=g.user_id
ORDER BY c.channel_id,g.quota DESC NULLS LAST,g.user_id
LIMIT %(limit)s
"""


class FlowUnavailable(ValueError):
    pass


def parameters(args):
    if set(args) - {"start", "end"} or any(len(args.getlist(key)) > 1 for key in args):
        raise ValueError("渠道分流只接受开始、结束时间，统计全部启用渠道。")
    start, end = args.get("start", ""), args.get("end", "")
    period(start, end)
    return start, end


def summarize(records):
    """Do not round or reprice charges; each denominator is the whole channel."""
    if len(records) > MAX_ROWS:
        raise FlowUnavailable("渠道分流结果过多，请缩小时间范围后再查询。")
    channels = {}
    for row in records:
        channel = channels.setdefault(
            row["channel_id"],
            dict(
                channel_id=row["channel_id"],
                channel_name=row["channel_name"],
                amount=Decimal(0),
                request_count=0,
                user_count=0,
                users=[],
            ),
        )
        if row["request_count"] is None:  # Keep enabled channels with no logs.
            continue
        amount = Decimal(row["quota"]) / QUOTA_PER_UNIT
        channel["amount"] += amount
        channel["request_count"] += row["request_count"]
        channel["users"].append(
            dict(
                user_id=row["user_id"],
                username=row["username"],
                display_name=row["display_name"],
                amount=amount,
                request_count=row["request_count"],
            )
        )
    for channel in channels.values():
        channel["user_count"] = len(channel["users"])
        for user in channel["users"]:
            user["percentage"] = (
                user["amount"] * 100 / channel["amount"]
                if channel["amount"] != 0
                else None
            )
        channel["users"].sort(
            key=lambda user: (-user["amount"], user["username"], user["user_id"] or 0)
        )
    return sorted(
        channels.values(), key=lambda row: (-row["amount"], row["channel_id"])
    )


def source_connection():
    return psycopg.connect(
        connect_timeout=8,
        row_factory=dict_row,
        options="-c default_transaction_read_only=on -c statement_timeout=60000",
    )


def load(start, end):
    first, last = period(start, end)
    with source_connection() as conn:
        rows = conn.execute(
            SQL, dict(start=first, end=last, limit=MAX_ROWS + 1)
        ).fetchall()
    return dict(start=start, end=end, rows=summarize(rows))
