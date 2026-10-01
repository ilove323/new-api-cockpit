"""Budget ledger in a separate database; New API usage is always read-only."""

import os
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

from new_api_statistics.report import TZ
from new_api_statistics.locks import BALANCE_LOCK


def configured():
    return bool(os.environ.get("MONITOR_DATABASE_URL"))


def connect():
    if not configured():
        raise ValueError("余额监控数据库尚未配置。")
    return psycopg.connect(
        os.environ["MONITOR_DATABASE_URL"],
        connect_timeout=8,
        row_factory=dict_row,
        options="-c statement_timeout=15000 -c lock_timeout=5000",
    )


def initialize():
    with connect() as conn:
        conn.execute("SELECT pg_advisory_xact_lock(%s)", (BALANCE_LOCK,))
        conn.execute("""CREATE TABLE IF NOT EXISTS schema_migrations (
            version text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now()
        )""")
        applied = {
            row["version"]
            for row in conn.execute("SELECT version FROM schema_migrations").fetchall()
        }
        # The lock and transaction make upgrades atomic across web/worker processes.
        for migration in sorted(
            Path(__file__).with_name("migrations").glob("[0-9]*.sql")
        ):
            if migration.name not in applied:
                conn.execute(migration.read_text())
                conn.execute(
                    "INSERT INTO schema_migrations(version) VALUES (%s)",
                    (migration.name,),
                )
        conn.execute(
            "INSERT INTO balance_settings(id,start_month) VALUES (1,%s) ON CONFLICT DO NOTHING",
            (datetime.now(TZ).date().replace(day=1),),
        )


class SchemaUnavailable(ValueError):
    pass


def require_schema():
    """Read-only readiness check; never apply migrations on a request path."""
    expected = {
        p.name for p in Path(__file__).with_name("migrations").glob("[0-9]*.sql")
    }
    try:
        with connect() as conn:
            applied = {
                r["version"]
                for r in conn.execute("SELECT version FROM schema_migrations")
            }
    except psycopg.errors.UndefinedTable:
        raise SchemaUnavailable("监控数据库尚未初始化，请执行启动迁移。") from None
    if not expected <= applied:
        raise SchemaUnavailable("监控数据库需要升级，请执行启动迁移。")


def next_month(month):
    return date(month.year + (month.month == 12), month.month % 12 + 1, 1)


def month_list(first, end):
    result = []
    while first < end:
        result.append(first)
        first = next_month(first)
    return result


def source_channels(*, include_deleted=False):
    """Read the current New API channel catalog without modifying it."""
    with psycopg.connect(
        connect_timeout=8,
        row_factory=dict_row,
        options="-c default_transaction_read_only=on -c statement_timeout=15000",
    ) as conn:
        query = """SELECT id AS channel_id,
                COALESCE(NULLIF(btrim(name),''),'渠道 #' || id::text) AS channel_name,
                status AS channel_status,COALESCE(NULLIF(btrim(tag),''),'') AS tag_value,
                false AS is_deleted FROM channels"""
        if include_deleted:
            query += """ UNION ALL
            SELECT DISTINCT l.channel_id,'渠道 #' || l.channel_id::text,NULL::bigint,''::text,true
            FROM logs l LEFT JOIN channels c ON c.id=l.channel_id
            WHERE c.id IS NULL AND l.channel_id IS NOT NULL"""
        return conn.execute(query + " ORDER BY channel_id").fetchall()


def sync_channel_inventory(conn, live_channels):
    """Use the live name for a channel ID across inventory and archived details."""
    tag_ids = {
        r["tag_value"]: r["id"]
        for r in conn.execute(
            "SELECT id,tag_value FROM balance_scopes WHERE kind='tag'"
        )
    }
    stored = {
        r["channel_id"]: r
        for r in conn.execute(
            "SELECT channel_id,channel_name,scope_id FROM balance_channel_inventory"
        )
    }
    for row in live_channels:
        if row.get("is_deleted"):
            if row["channel_id"] not in stored:
                conn.execute(
                    """INSERT INTO balance_channel_inventory(channel_id,channel_name)
                    VALUES (%s,%s) ON CONFLICT DO NOTHING""",
                    (row["channel_id"], row["channel_name"]),
                )
            continue
        tag = (row.get("tag_value") or "").strip() if row["channel_id"] else ""
        if tag and tag not in tag_ids:
            created = conn.execute(
                """INSERT INTO balance_scopes(kind,tag_value)
                VALUES ('tag',%s) ON CONFLICT(kind,tag_value) DO NOTHING
                RETURNING id""",
                (tag,),
            ).fetchone()
            scope_id = (
                created
                or conn.execute(
                    "SELECT id FROM balance_scopes WHERE kind='tag' AND tag_value=%s",
                    (tag,),
                ).fetchone()
            )["id"]
            if created:
                conn.execute(
                    """INSERT INTO balance_month_scopes(month,scope_id,tag_value,amount)
                    SELECT month,%s,%s,0 FROM balance_channel_archive_months
                    ON CONFLICT DO NOTHING""",
                    (scope_id, tag),
                )
            tag_ids[tag] = scope_id
        else:
            scope_id = tag_ids.get(tag, 2)
        previous = stored.get(row["channel_id"])
        if previous and (previous["channel_name"], previous["scope_id"]) == (
            row["channel_name"],
            scope_id,
        ):
            continue
        conn.execute(
            """INSERT INTO balance_channel_inventory(channel_id,channel_name,scope_id)
            VALUES (%s,%s,%s) ON CONFLICT(channel_id) DO UPDATE SET
            channel_name=EXCLUDED.channel_name,scope_id=EXCLUDED.scope_id,last_seen_at=now()
            WHERE (balance_channel_inventory.channel_name,balance_channel_inventory.scope_id)
              IS DISTINCT FROM (EXCLUDED.channel_name,EXCLUDED.scope_id)""",
            (row["channel_id"], row["channel_name"], scope_id),
        )
        conn.execute(
            "UPDATE balance_month_channels SET channel_name=%s WHERE channel_id=%s AND channel_name IS DISTINCT FROM %s",
            (row["channel_name"], row["channel_id"], row["channel_name"]),
        )
    from new_api_statistics.scopes import ensure_settings

    ensure_settings(conn)


def usage_channels_snapshot(scope_id=1):
    """Refresh the channel inventory and mark channels absent from New API as deleted."""
    from new_api_statistics import scopes

    catalog = scopes.refresh_scopes()
    scope_id = scopes.get_scope(scope_id)["id"]
    live_channels = [r for r in catalog if not r.get("is_deleted")]
    live_by_id = {row["channel_id"]: row for row in live_channels}
    with connect() as conn:
        excluded = set()
        inventory = conn.execute(
            "SELECT channel_id,channel_name,scope_id FROM balance_channel_inventory WHERE (%s=1 OR scope_id=%s) ORDER BY channel_id",
            (scope_id, scope_id),
        ).fetchall()
    result = []
    for stored in inventory:
        live = live_by_id.get(stored["channel_id"])
        result.append(
            {
                "channel_id": stored["channel_id"],
                "channel_name": live["channel_name"]
                if live
                else stored["channel_name"],
                "channel_status": live["channel_status"] if live else None,
                "deleted": live is None,
                "included": stored["channel_id"] not in excluded,
            }
        )
    return result


def validate_excluded_channel_ids(value):
    if value is None:
        return None
    if not isinstance(value, list) or len(value) > 5000:
        raise ValueError("请选择有效的消费渠道。")
    result = set()
    for channel_id in value:
        if type(channel_id) is not int or channel_id < 0 or channel_id in result:
            raise ValueError("请选择有效的消费渠道。")
        result.add(channel_id)
    return sorted(result)


def excluded_usage_channel_ids(conn):
    return [
        row["channel_id"]
        for row in conn.execute(
            "SELECT channel_id FROM balance_excluded_channels"
        ).fetchall()
    ]


def validate_settings(body, today=None):
    if not isinstance(body, dict) or type(body.get("enabled")) is not bool:
        raise ValueError("请填写有效的监控设置。")
    values = {}
    for key in ("budget", "threshold"):
        try:
            value = Decimal(str(body.get(key)))
        except InvalidOperation:
            raise ValueError("额度和阈值必须是非负金额。") from None
        if (
            not value.is_finite()
            or not 0 <= value <= Decimal("999999999999")
            or value != value.quantize(Decimal(".01"))
        ):
            raise ValueError("额度和阈值最多两位小数，且不能为负数或过大。")
        values[key] = value
    try:
        month = datetime.strptime(body["start_month"], "%Y-%m").date()
        if month.strftime("%Y-%m") != body["start_month"]:
            raise ValueError()
    except (KeyError, TypeError, ValueError):
        raise ValueError("累计起始月份格式应为 YYYY-MM。") from None
    if (
        not date(1970, 1, 1)
        <= month
        <= (today or datetime.now(TZ).date()).replace(day=1)
    ):
        raise ValueError("累计起始月份不能晚于本月。")
    if type(body.get("version")) is not int or body["version"] < 1:
        raise ValueError("设置版本无效，请重新打开设置。")
    return dict(
        values,
        start_month=month,
        enabled=body["enabled"],
        version=body["version"],
        excluded_channel_ids=validate_excluded_channel_ids(
            body.get("excluded_channel_ids")
        ),
    )


class SettingsConflict(Exception):
    """Another administrator changed the same settings."""


class CheckBusy(Exception):
    """An in-progress check must not be reported as a completed manual check."""


class ArchiveDataMissing(Exception):
    """Per-channel archives cannot be safely rebuilt from incomplete source logs."""


class HistoryPreviewChanged(Exception):
    """The source data or settings changed after the history preview."""


def replace_excluded_channels(conn, channel_ids, username):
    if channel_ids is None:
        return
    conn.execute("DELETE FROM balance_excluded_channels")
    if channel_ids:
        conn.execute(
            """INSERT INTO balance_excluded_channels(channel_id,updated_by)
               SELECT channel_id,%s FROM unnest(%s::bigint[]) AS channel_id""",
            (username, channel_ids),
        )


def update_settings(conn, values, username, scope_id=1):
    row = conn.execute(
        """UPDATE balance_settings SET budget=%(budget)s,
        threshold=%(threshold)s,start_month=%(start_month)s,enabled=%(enabled)s,
        version=version+1,updated_at=now(),updated_by=%(username)s
        WHERE scope_id=%(scope_id)s AND version=%(version)s RETURNING *""",
        dict(values, username=username, scope_id=scope_id),
    ).fetchone()
    if not row:
        raise SettingsConflict()
    return row


def audit_settings(conn, row, username):
    conn.execute(
        """INSERT INTO balance_settings_audit(username,version,budget,threshold,start_month,enabled,scope_id)
        VALUES (%s,%s,%s,%s,%s,%s,%s)""",
        (
            username,
            row["version"],
            row["budget"],
            row["threshold"],
            row["start_month"],
            row["enabled"],
            row["scope_id"],
        ),
    )


def save_settings(body, username, scope_id=1):
    from new_api_statistics import scopes

    scopes.refresh_scopes()
    scope_id = scopes.get_scope(scope_id)["id"]
    values = validate_settings(body)
    values.pop("excluded_channel_ids")
    with connect() as conn:
        conn.execute("SELECT pg_advisory_xact_lock(%s)", (BALANCE_LOCK,))
        row = update_settings(conn, values, username, scope_id)
        audit_settings(conn, row, username)
        if not row["enabled"]:
            conn.execute("DELETE FROM balance_alerts WHERE scope_id=%s", (scope_id,))
        archive_missing(conn, row["start_month"], datetime.now(TZ))
    return row


def history_calculation(conn, body, now, scope_id=1):
    """Read complete per-channel history and build a protected monthly comparison."""
    values = validate_settings(body)
    excluded_channel_ids = values.pop("excluded_channel_ids")
    current = now.date().replace(day=1)
    months = month_list(values["start_month"], current)
    if not months:
        raise ValueError("当前起始月份没有可追溯的历史归档。")
    settings = conn.execute(
        "SELECT version FROM balance_settings WHERE scope_id=%s", (scope_id,)
    ).fetchone()
    if not settings or settings["version"] != values["version"]:
        raise SettingsConflict()
    archived = {
        r["month"]: r["amount"]
        for r in archived_month_rows(
            conn, values["start_month"], current, scope_id=scope_id
        )
    }
    details = source_channel_amounts(months, now)
    baseline = {
        r["month"]: r["amount"]
        for r in conn.execute(
            "SELECT month,amount FROM balance_months WHERE month=ANY(%s::date[])",
            (months,),
        ).fetchall()
    }
    raw = {
        m: sum((r["amount"] for r in details.get(m, [])), Decimal(0)) for m in months
    }
    if any(m in baseline and raw[m] < baseline[m] for m in months) or not any(
        details.values()
    ):
        raise ArchiveDataMissing()
    mapping = {
        r["channel_id"]: r["scope_id"]
        for r in conn.execute(
            "SELECT channel_id,scope_id FROM balance_channel_inventory"
        ).fetchall()
    }
    amounts = {
        m: sum(
            (
                r["amount"]
                for r in details.get(m, [])
                if scope_id == 1 or mapping.get(r["channel_id"], 2) == scope_id
            ),
            Decimal(0),
        )
        for m in months
    }

    return values, excluded_channel_ids, months, archived, details, amounts


def history_preview(body, now=None, scope_id=1):
    """Return the complete monthly comparison that an administrator must confirm."""
    from new_api_statistics import scopes

    scopes.refresh_scopes()
    scope_id = scopes.get_scope(scope_id)["id"]
    now = (now or datetime.now(TZ)).astimezone(TZ)
    with connect() as conn:
        conn.execute("SELECT pg_advisory_xact_lock(%s)", (BALANCE_LOCK,))
        values, _, months, archived, _, amounts = history_calculation(
            conn, body, now, scope_id
        )
    return {
        "version": values["version"],
        "scope_id": scope_id,
        "rebuild_scope": "all",
        "affects_all_scopes": True,
        "warning": "将重建这些月份所有账本的归档，不仅当前账本。",
        "rows": [
            {
                "month": month.strftime("%Y-%m"),
                "before": str(archived.get(month, Decimal(0))),
                "after": str(amounts[month]),
            }
            for month in months
        ],
    }


def validate_history_preview(rows):
    if not isinstance(rows, list) or not rows or len(rows) > 1200:
        raise ValueError("历史计费预览无效，请重新预览。")
    result = {}
    try:
        for row in rows:
            month = datetime.strptime(row["month"], "%Y-%m").date()
            before = Decimal(str(row["before"]))
            after = Decimal(str(row["after"]))
            if (
                month in result
                or month.strftime("%Y-%m") != row["month"]
                or not before.is_finite()
                or not after.is_finite()
                or before < 0
                or after < 0
            ):
                raise ValueError()
            result[month] = (before, after)
    except (InvalidOperation, KeyError, TypeError, ValueError):
        raise ValueError("历史计费预览无效，请重新预览。") from None
    return result


def recalculate_history(body, preview_rows, username, now=None, scope_id=1):
    """Replace history with complete per-channel data after exact monthly review."""
    expected = validate_history_preview(preview_rows)
    from new_api_statistics import scopes

    scopes.refresh_scopes()
    scope_id = scopes.get_scope(scope_id)["id"]
    now = (now or datetime.now(TZ)).astimezone(TZ)
    with connect() as conn:
        conn.execute("SELECT pg_advisory_xact_lock(%s)", (BALANCE_LOCK,))
        values, excluded, months, archived, details, amounts = history_calculation(
            conn, body, now, scope_id
        )
        actual = {
            month: (archived.get(month, Decimal(0)), amounts[month]) for month in months
        }
        if expected != actual:
            raise HistoryPreviewChanged()
        row = update_settings(conn, values, username, scope_id)
        audit_settings(conn, row, username)
        write_channel_archives(conn, months, details)
        sync_channel_inventory(conn, source_channels())
        if not row["enabled"]:
            conn.execute("DELETE FROM balance_alerts WHERE scope_id=%s", (scope_id,))
    return {"version": row["version"], "months": len(months)}


def archived_month_rows(conn, first, end, excluded_channel_ids=None, scope_id=1):
    """Aggregate retained monthly channel fees using current channel membership."""
    return conn.execute(
        """SELECT m.month,
        COALESCE(SUM(CASE WHEN %s=1 OR COALESCE(i.scope_id,2)=%s
                    THEN d.amount ELSE 0 END),0) AS amount,
        MAX(d.archived_at) AS archived_at
        FROM balance_channel_archive_months m
        LEFT JOIN balance_month_channels d ON d.month=m.month
        LEFT JOIN balance_channel_inventory i ON i.channel_id=d.channel_id
        WHERE m.month >= %s AND m.month < %s
        GROUP BY m.month ORDER BY m.month DESC""",
        (scope_id, scope_id, first, end),
    ).fetchall()


def source_channel_amounts(months, now):
    """Read all channel amounts without applying status or monitoring exclusions."""
    if not months:
        return {}
    ranges = []
    params = []
    for month in sorted(set(months)):
        first = datetime.combine(month, datetime.min.time(), tzinfo=TZ)
        last = datetime.combine(next_month(month), datetime.min.time(), tzinfo=TZ)
        ranges.append("(created_at >= %s AND created_at < %s)")
        params.extend(
            [
                int(first.timestamp()),
                min(int(last.timestamp()), int(now.timestamp()) + 1),
            ]
        )
    with psycopg.connect(
        connect_timeout=8,
        row_factory=dict_row,
        options="-c default_transaction_read_only=on -c statement_timeout=60000",
    ) as conn:
        rows = conn.execute(
            """SELECT date_trunc('month',to_timestamp(created_at)
               AT TIME ZONE 'Asia/Shanghai')::date AS month,channel_id,
               COALESCE(MAX(NULLIF(btrim(channel_name),'')),
                 CASE WHEN channel_id IS NULL THEN '未标记渠道'
                      ELSE '渠道 #' || channel_id::text END) AS channel_name,
               SUM(quota::numeric)/500000 AS amount
               FROM logs WHERE type=2 AND ("""
            + " OR ".join(ranges)
            + ") GROUP BY 1,channel_id",
            params,
        ).fetchall()
    result = {month: [] for month in months}
    for row in rows:
        result[row["month"]].append(row)
    return result


def write_channel_archives(conn, months, details):
    """Replace selected closed months with complete, unfiltered channel archives."""
    if not months:
        return
    sync_channel_inventory(conn, source_channels())
    conn.execute(
        "DELETE FROM balance_channel_archive_months WHERE month = ANY(%s::date[])",
        (months,),
    )
    for month in months:
        conn.execute(
            "INSERT INTO balance_channel_archive_months(month) VALUES (%s)",
            (month,),
        )
        raw_amount = sum((row["amount"] for row in details.get(month, [])), Decimal(0))
        conn.execute(
            """INSERT INTO balance_months(month,amount,archived_at) VALUES (%s,%s,now())
               ON CONFLICT(month) DO UPDATE SET amount=EXCLUDED.amount,archived_at=now()""",
            (month, raw_amount),
        )
        for detail in details.get(month, []):
            conn.execute(
                """INSERT INTO balance_month_channels
                   (month,channel_id,channel_name,amount,scope_id,tag_value)
                   VALUES (%s,%s,%s,%s,
                     COALESCE((SELECT scope_id FROM balance_channel_inventory WHERE channel_id=%s),2),
                     COALESCE((SELECT s.tag_value FROM balance_channel_inventory i
                       JOIN balance_scopes s ON s.id=i.scope_id WHERE i.channel_id=%s),''))""",
                (
                    month,
                    detail["channel_id"],
                    detail["channel_name"],
                    detail["amount"],
                    detail["channel_id"],
                    detail["channel_id"],
                ),
            )
            if detail["channel_id"] is not None:
                conn.execute(
                    """INSERT INTO balance_channel_inventory(channel_id,channel_name)
                       VALUES (%s,%s) ON CONFLICT(channel_id) DO NOTHING""",
                    (detail["channel_id"], detail["channel_name"]),
                )
    conn.execute(
        """UPDATE balance_month_channels detail SET channel_name=inventory.channel_name
           FROM balance_channel_inventory inventory
           WHERE detail.channel_id=inventory.channel_id
             AND detail.channel_name IS DISTINCT FROM inventory.channel_name"""
    )

    from new_api_statistics.scopes import rebuild_month_scopes

    rebuild_month_scopes(conn, months)


def rebuild_channel_archives(now=None):
    """One-time safe upgrade from monthly totals to unfiltered channel archives."""
    now = (now or datetime.now(TZ)).astimezone(TZ)
    current = now.date().replace(day=1)
    with connect() as conn:
        conn.execute("SELECT pg_advisory_xact_lock(%s)", (BALANCE_LOCK,))
        settings = conn.execute(
            "SELECT start_month FROM balance_settings WHERE id=1"
        ).fetchone()
        months = month_list(settings["start_month"], current)
        baseline = {
            row["month"]: row["amount"]
            for row in conn.execute(
                "SELECT month,amount FROM balance_months WHERE month >= %s AND month < %s",
                (settings["start_month"], current),
            ).fetchall()
        }
        if any(month not in baseline for month in months):
            raise ArchiveDataMissing()
        details = source_channel_amounts(months, now)
        raw = {
            month: sum((row["amount"] for row in details.get(month, [])), Decimal(0))
            for month in months
        }
        if any(raw[month] < baseline[month] for month in months):
            raise ArchiveDataMissing()
        write_channel_archives(conn, months, details)
        sync_channel_inventory(conn, source_channels())
    return {
        "months": len(months),
        "channels": len(
            {
                row["channel_id"]
                for rows in details.values()
                for row in rows
                if row["channel_id"] is not None
            }
        ),
    }


def archive_missing(conn, first, now):
    """Backfill absent closed months with every channel, regardless of status."""
    current = now.date().replace(day=1)
    archived = {
        row["month"]
        for row in conn.execute(
            "SELECT month FROM balance_channel_archive_months"
        ).fetchall()
    }
    missing = [m for m in month_list(first, current) if m not in archived]
    if not missing:
        return
    details = source_channel_amounts(missing, now)
    baseline = {
        row["month"]: row["amount"]
        for row in conn.execute(
            "SELECT month,amount FROM balance_months WHERE month = ANY(%s::date[])",
            (missing,),
        ).fetchall()
    }
    raw = {
        month: sum((row["amount"] for row in details.get(month, [])), Decimal(0))
        for month in missing
    }
    if any(month in baseline and raw[month] < baseline[month] for month in missing):
        raise ArchiveDataMissing()
    write_channel_archives(conn, missing, details)
    sync_channel_inventory(conn, source_channels())


def source_amounts_with_raw(months, now, excluded_channel_ids=None):
    """Return raw and filtered billing in one read-only source query."""
    if not months:
        return {}
    ranges = []
    params = []
    filtered_sum = "SUM(quota::numeric)/500000"
    if excluded_channel_ids:
        filtered_sum = (
            "SUM(quota::numeric) FILTER (WHERE channel_id IS NULL "
            "OR NOT (channel_id = ANY(%s::bigint[])))/500000"
        )
        params.append(excluded_channel_ids)
    for month in sorted(set(months)):
        first = datetime.combine(month, datetime.min.time(), tzinfo=TZ)
        last = datetime.combine(next_month(month), datetime.min.time(), tzinfo=TZ)
        ranges.append("(created_at >= %s AND created_at < %s)")
        params.extend(
            [
                int(first.timestamp()),
                min(int(last.timestamp()), int(now.timestamp()) + 1),
            ]
        )
    with psycopg.connect(
        connect_timeout=8,
        row_factory=dict_row,
        options="-c default_transaction_read_only=on -c statement_timeout=60000",
    ) as conn:
        rows = conn.execute(
            """SELECT date_trunc('month',to_timestamp(created_at)
            AT TIME ZONE 'Asia/Shanghai')::date AS month,
            SUM(quota::numeric)/500000 AS raw_amount,"""
            + filtered_sum
            + """ AS filtered_amount FROM logs
            WHERE type=2 AND ("""
            + " OR ".join(ranges)
            + ")"
            + " GROUP BY 1",
            params,
        ).fetchall()
    found = {
        row["month"]: {"raw": row["raw_amount"], "filtered": row["filtered_amount"]}
        for row in rows
    }
    zero = {"raw": Decimal(0), "filtered": Decimal(0)}
    return {month: found.get(month, zero.copy()) for month in months}


def source_amounts(months, now, excluded_channel_ids=None):
    """One SQL aggregate, no request-log pagination; half-open Beijing months."""
    return {
        month: amounts["filtered"]
        for month, amounts in source_amounts_with_raw(
            months, now, excluded_channel_ids
        ).items()
    }


def current_scope_amount(conn, scope_id, current, now):
    """Aggregate source rows once, using retained channel tags for deleted channels."""
    if scope_id == 1:
        return source_amounts([current], now)[current]
    details = source_channel_amounts([current], now)[current]
    mapping = {
        r["channel_id"]: r["scope_id"]
        for r in conn.execute(
            "SELECT channel_id,scope_id FROM balance_channel_inventory"
        ).fetchall()
    }
    return sum(
        (
            r["amount"]
            for r in details
            if scope_id == 1 or mapping.get(r["channel_id"], 2) == scope_id
        ),
        Decimal(0),
    )


class CurrentMonthBatch:
    """A single daily invocation, NOT a balance cache across requests/runs."""

    def __init__(self):
        self.amounts = None
        self.error = None

    def amount(self, conn, scope_id, current, now):
        if self.error:
            raise self.error
        if self.amounts is None:
            try:
                details = source_channel_amounts([current], now)[current]
                mapping = {
                    r["channel_id"]: r["scope_id"]
                    for r in conn.execute(
                        "SELECT channel_id,scope_id FROM balance_channel_inventory"
                    )
                }
                amounts = {1: Decimal(0)}
                for row in details:
                    ledger = mapping.get(row["channel_id"], 2)
                    amounts[1] += row["amount"]
                    if ledger != 1:
                        amounts[ledger] = (
                            amounts.get(ledger, Decimal(0)) + row["amount"]
                        )
                self.amounts = amounts
            except Exception as exc:
                self.error = exc
                raise
        return self.amounts.get(scope_id, Decimal(0))


def check_once(now=None, source=None, daily=True, scope_id=1, _batch=None):
    """Check exactly one ledger; commit alert before global-channel delivery."""
    from new_api_statistics import scopes

    if _batch is None:
        scopes.refresh_scopes()
    scope = scopes.get_scope(scope_id)
    if not scope["is_visible"]:
        return None
    scope_id = scope["id"]
    now = (now or datetime.now(TZ)).astimezone(TZ)
    current = now.date().replace(day=1)
    with connect() as conn:
        if not conn.execute(
            "SELECT pg_try_advisory_xact_lock(%s) AS locked", (BALANCE_LOCK,)
        ).fetchone()["locked"]:
            if not daily:
                raise CheckBusy()
            return
        settings = conn.execute(
            "SELECT * FROM balance_settings WHERE scope_id=%s", (scope_id,)
        ).fetchone()
        if not settings or not settings["enabled"]:
            return
        if (
            daily
            and conn.execute(
                "SELECT 1 FROM balance_daily_runs WHERE scope_id=%s AND day=%s",
                (scope_id, now.date()),
            ).fetchone()
        ):
            return
        if source:
            archived = {
                r["month"]
                for r in conn.execute(
                    "SELECT month FROM balance_channel_archive_months"
                ).fetchall()
            }
            missing = [
                m
                for m in month_list(settings["start_month"], current)
                if m not in archived
            ]
            amounts = source(missing + [current], now)
            write_channel_archives(
                conn,
                missing,
                {
                    m: [
                        dict(
                            channel_id=None,
                            channel_name="未标记渠道",
                            amount=amounts[m],
                        )
                    ]
                    for m in missing
                },
            )
            amount = amounts[current]
        else:
            archive_missing(conn, settings["start_month"], now)
            amount = (
                current_scope_amount(conn, scope_id, current, now)
                if _batch is None
                else _batch.amount(conn, scope_id, current, now)
            )
        latest = conn.execute(
            "SELECT version FROM balance_settings WHERE scope_id=%s FOR UPDATE",
            (scope_id,),
        ).fetchone()
        if latest["version"] != settings["version"]:
            if not daily:
                raise CheckBusy()
            return
        historical = sum(
            (
                r["amount"]
                for r in archived_month_rows(
                    conn, settings["start_month"], current, scope_id=scope_id
                )
            ),
            Decimal(0),
        )
        spent = historical + amount
        remaining = settings["budget"] - spent
        conn.execute(
            """INSERT INTO balance_state(id,scope_id,version,current_month,current_amount,
            archived_amount,remaining,checked_at,last_error) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,NULL)
            ON CONFLICT(scope_id) DO UPDATE SET version=EXCLUDED.version,current_month=EXCLUDED.current_month,
            current_amount=EXCLUDED.current_amount,archived_amount=EXCLUDED.archived_amount,
            remaining=EXCLUDED.remaining,checked_at=EXCLUDED.checked_at,last_error=NULL""",
            (
                scope_id,
                scope_id,
                settings["version"],
                current,
                amount,
                historical,
                remaining,
                now,
            ),
        )
        if remaining < settings["threshold"]:
            conn.execute(
                """INSERT INTO balance_alerts(scope_id,remaining,threshold,spent,budget,updated_at)
                VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT(scope_id) DO UPDATE SET
                remaining=EXCLUDED.remaining,threshold=EXCLUDED.threshold,spent=EXCLUDED.spent,
                budget=EXCLUDED.budget,updated_at=EXCLUDED.updated_at,resolved_at=NULL""",
                (
                    scope_id,
                    remaining,
                    settings["threshold"],
                    spent,
                    settings["budget"],
                    now,
                ),
            )
        else:
            conn.execute("DELETE FROM balance_alerts WHERE scope_id=%s", (scope_id,))
        if daily:
            conn.execute(
                "INSERT INTO balance_daily_runs(scope_id,day) VALUES (%s,%s) ON CONFLICT DO NOTHING",
                (scope_id, now.date()),
            )
    from new_api_statistics.notifications import notify_safely

    notify_safely(scope_id=scope_id)
    return True


def check_all_enabled(now=None, _stopped=None):
    """Daily worker: one failed ledger must not prevent the remaining checks."""
    from new_api_statistics import scopes
    import logging

    results = {}
    now = (now or datetime.now(TZ)).astimezone(TZ)
    batch = CurrentMonthBatch()
    for scope in scopes.list_scopes(refresh=True):
        if _stopped is not None and _stopped.is_set():
            break
        if scope["enabled"]:
            try:
                results[scope["id"]] = check_once(
                    now=now, scope_id=scope["id"], _batch=batch
                )
            except Exception as exc:
                logging.error(
                    "Balance scope %s failed: %s", scope["id"], type(exc).__name__
                )
                try:
                    record_failure(scope_id=scope["id"])
                except Exception:
                    logging.error("Unable to persist scope failure")
                results[scope["id"]] = False
    return results


def record_failure(scope_id=1):
    with connect() as conn:
        conn.execute(
            """INSERT INTO balance_state(id,scope_id,last_error)
            VALUES (%s,%s,'监控查询失败，请检查服务日志。')
            ON CONFLICT(scope_id) DO UPDATE SET last_error=EXCLUDED.last_error""",
            (scope_id, scope_id),
        )


def snapshot(live=False, scope_id=1):
    if not configured():
        return dict(configured=False)
    from new_api_statistics import scopes

    scopes.refresh_scopes()
    now = datetime.now(TZ)
    current = now.date().replace(day=1)
    with connect() as conn:
        scope = scopes.get_scope(scope_id, conn)
        scope_id = scope["id"]
        settings = conn.execute(
            "SELECT * FROM balance_settings WHERE scope_id=%s", (scope_id,)
        ).fetchone()
        state = conn.execute(
            "SELECT * FROM balance_state WHERE scope_id=%s", (scope_id,)
        ).fetchone()
        months = archived_month_rows(
            conn, settings["start_month"], current, scope_id=scope_id
        )
        alerts = conn.execute(
            "SELECT * FROM balance_alerts WHERE scope_id=%s", (scope_id,)
        ).fetchall()
        if live:
            conn.execute("SELECT pg_advisory_xact_lock(%s)", (BALANCE_LOCK,))
            archive_missing(conn, settings["start_month"], now)
            months = archived_month_rows(
                conn, settings["start_month"], current, scope_id=scope_id
            )
            amount = current_scope_amount(conn, scope_id, current, now)
            historical = sum((m["amount"] for m in months), Decimal(0))
            state = dict(
                version=settings["version"],
                current_month=current,
                current_amount=amount,
                archived_amount=historical,
                remaining=settings["budget"] - historical - amount,
                checked_at=now,
                last_error=None,
            )
    valid = bool(
        state
        and state["checked_at"]
        and state["version"] == settings["version"]
        and state["current_month"] == current
    )
    stale = (
        not valid
        or (now - state["checked_at"]).total_seconds() > 90000
        or bool(state["last_error"])
    )
    return dict(
        configured=True,
        scope=scope,
        settings=settings,
        state=state,
        months=months,
        alerts=alerts,
        valid=valid,
        stale=stale,
    )
