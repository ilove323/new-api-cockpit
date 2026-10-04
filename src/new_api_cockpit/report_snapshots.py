"""Short-lived immutable tooltip DTOs in the monitoring database, not log caches."""

import json
import uuid
from datetime import datetime, timedelta
from decimal import Decimal

from psycopg.types.json import Jsonb

from new_api_cockpit import balance
from new_api_cockpit.report import TZ
from new_api_cockpit.locks import REPORT_SNAPSHOT_NAMESPACE

TTL = timedelta(minutes=15)
MAX_REPORTS_PER_OWNER = 10
HEAVY_FIELDS = {"cost_formula", "tier_usage", "pricing_buckets", "price_tiers"}


class SnapshotExpired(ValueError):
    pass


def dumps(value):
    def encode(obj):
        if isinstance(obj, Decimal):
            return str(obj)
        raise TypeError(type(obj).__name__)

    return json.dumps(value, default=encode, ensure_ascii=False)


def create(rows, owner, scope_id):
    balance.require_schema()
    report_id = uuid.uuid4()
    now = datetime.now(TZ)
    with balance.connect() as conn:
        # Serialize creation per owner, bounding concurrent retained reports.
        conn.execute(
            "SELECT pg_advisory_xact_lock(%s,hashtext(%s))",
            (REPORT_SNAPSHOT_NAMESPACE, owner),
        )
        conn.execute(
            """DELETE FROM report_snapshots WHERE id IN (
            SELECT id FROM report_snapshots WHERE expires_at<=%s ORDER BY expires_at LIMIT 100)""",
            (now,),
        )
        conn.execute(
            """DELETE FROM report_snapshots WHERE id IN (
            SELECT id FROM report_snapshots WHERE owner_name=%s
            ORDER BY created_at DESC,id DESC OFFSET %s)""",
            (owner, MAX_REPORTS_PER_OWNER - 1),
        )
        conn.execute(
            "INSERT INTO report_snapshots(id,owner_name,scope_id,expires_at) VALUES (%s,%s,%s,%s)",
            (report_id, owner, scope_id, now + TTL),
        )
        with conn.cursor() as cursor:
            cursor.executemany(
                "INSERT INTO report_snapshot_rows(report_id,row_id,detail) VALUES (%s,%s,%s)",
                (
                    (
                        report_id,
                        index,
                        Jsonb({"cost_formula": row.get("cost_formula")}, dumps=dumps),
                    )
                    for index, row in enumerate(rows)
                ),
            )
    return [
        dict(
            {key: value for key, value in row.items() if key not in HEAVY_FIELDS},
            has_pricing_buckets=bool(row.get("pricing_buckets")),
            price_tier_count=len(row.get("price_tiers") or []),
            report_id=str(report_id),
            row_id=index,
        )
        for index, row in enumerate(rows)
    ]


def fetch(body, owner, scope_id):
    if not isinstance(body, dict):
        raise ValueError("请选择有效的报表详情。")
    try:
        report_id = uuid.UUID(str(body.get("report_id", "")))
    except ValueError:
        raise ValueError("请选择有效的报表详情。") from None
    ids = body.get("row_ids")
    if (
        not isinstance(ids, list)
        or not 1 <= len(ids) <= 500
        or any(type(i) is not int or not 0 <= i <= 2147483647 for i in ids)
    ):
        raise ValueError("请选择有效的报表行。")
    ids = sorted(set(ids))
    with balance.connect() as conn:
        rows = conn.execute(
            """SELECT d.row_id,d.detail FROM report_snapshots s
            JOIN report_snapshot_rows d ON d.report_id=s.id
            WHERE s.id=%s AND s.owner_name=%s AND s.scope_id=%s AND s.expires_at>now()
              AND d.row_id=ANY(%s::integer[]) ORDER BY d.row_id""",
            (report_id, owner, scope_id, ids),
        ).fetchall()
    if len(rows) != len(ids):
        raise SnapshotExpired("报表详情已过期或不可访问，请重新查询。")
    return rows
