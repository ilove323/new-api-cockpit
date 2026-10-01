"""Channel-tag ledgers. Only the monitoring database is ever modified."""

from datetime import datetime
from new_api_statistics import balance
from new_api_statistics.locks import BALANCE_LOCK

ALL = 1
UNGROUPED = 2


def scope_name(scope):
    return {"all": "全部", "ungrouped": "未分组"}.get(scope["kind"], scope["tag_value"])


def get_scope(scope_id=ALL, conn=None):
    if isinstance(scope_id, bool) or not isinstance(scope_id, (int, str)):
        raise ValueError("无效的账本。")
    try:
        scope_id = int(scope_id)
    except (ValueError, TypeError):
        raise ValueError("无效的账本。") from None
    if conn is None:
        with balance.connect() as connection:
            return get_scope(scope_id, connection)
    row = conn.execute(
        "SELECT * FROM balance_scopes WHERE id=%s", (scope_id,)
    ).fetchone()
    if not row:
        raise ValueError("账本不存在。")
    return row


def ensure_settings(conn):
    conn.execute(
        """INSERT INTO balance_settings(id,scope_id,start_month)
        SELECT id,id,%s FROM balance_scopes ON CONFLICT DO NOTHING""",
        (datetime.now(balance.TZ).date().replace(day=1),),
    )


def refresh_scopes():
    live = balance.source_channels()
    with balance.connect() as conn:
        conn.execute("SELECT pg_advisory_xact_lock(%s)", (BALANCE_LOCK,))
        balance.sync_channel_inventory(conn, live)
        tags = sorted(
            {
                str(row.get("tag_value") or "").strip()
                for row in live
                if not row.get("is_deleted")
            }
        )
        conn.execute(
            """UPDATE balance_scopes SET is_visible=
            (kind<>'tag' OR tag_value=ANY(%s::text[]))""",
            (tags,),
        )
        ensure_settings(conn)
        # Deleted channels found only in archives retain ID/name and default to ungrouped.
        conn.execute("""INSERT INTO balance_channel_inventory(channel_id,channel_name)
            SELECT DISTINCT ON (channel_id) channel_id,channel_name FROM balance_month_channels
            WHERE channel_id IS NOT NULL ORDER BY channel_id,month DESC
            ON CONFLICT(channel_id) DO NOTHING""")
        marker = conn.execute(
            "SELECT completed FROM balance_scope_backfill WHERE id=1 FOR UPDATE"
        ).fetchone()
        if not marker["completed"]:
            conn.execute("""UPDATE balance_month_channels d SET scope_id=COALESCE(i.scope_id,2),
                tag_value=COALESCE(s.tag_value,'') FROM
                (SELECT d.month,d.channel_id,i.scope_id FROM balance_month_channels d
                 LEFT JOIN balance_channel_inventory i ON i.channel_id=d.channel_id) i
                LEFT JOIN balance_scopes s ON s.id=i.scope_id
                WHERE d.month=i.month AND d.channel_id IS NOT DISTINCT FROM i.channel_id
                  AND d.scope_id IS NULL""")
            rebuild_month_scopes(conn)
            conn.execute("UPDATE balance_scope_backfill SET completed=true WHERE id=1")
        # Keep legacy monthly scope rows for schema compatibility; reads use
        # channel archives joined with current inventory, not these rows.
        conn.execute("""INSERT INTO balance_month_scopes(month,scope_id,tag_value,amount)
            SELECT m.month,s.id,s.tag_value,0 FROM balance_channel_archive_months m
            CROSS JOIN balance_scopes s ON CONFLICT DO NOTHING""")


def rebuild_month_scopes(conn, months=None):
    """Aggregate immutable channel snapshots, never reassign them from live tags."""
    conn.execute(
        """INSERT INTO balance_month_scopes(month,scope_id,tag_value,amount)
        SELECT m.month,s.id,s.tag_value,COALESCE(SUM(d.amount),0)
        FROM balance_channel_archive_months m CROSS JOIN balance_scopes s
        LEFT JOIN balance_month_channels d ON d.month=m.month
          AND (s.kind='all' OR d.scope_id=s.id)
        WHERE (%s::date[] IS NULL OR m.month=ANY(%s::date[]))
        GROUP BY m.month,s.id,s.tag_value
        ON CONFLICT(month,scope_id) DO UPDATE SET amount=EXCLUDED.amount,
          tag_value=EXCLUDED.tag_value,archived_at=now()""",
        (months, months),
    )


def list_scopes(refresh=True):
    if refresh:
        refresh_scopes()
    with balance.connect() as conn:
        ensure_settings(conn)
        return conn.execute("""SELECT s.*,b.enabled FROM balance_scopes s
            JOIN balance_settings b ON b.scope_id=s.id
            WHERE s.is_visible
            ORDER BY CASE s.kind WHEN 'all' THEN 0 WHEN 'tag' THEN 1 ELSE 2 END,s.tag_value""").fetchall()


def channel_filter(scope_id=ALL, refresh=True):
    if refresh:
        refresh_scopes()
    with balance.connect() as conn:
        scope = get_scope(scope_id, conn)
        if scope["kind"] == "all":
            return None
        ids = [
            r["channel_id"]
            for r in conn.execute(
                "SELECT channel_id FROM balance_channel_inventory WHERE scope_id=%s",
                (scope["id"],),
            ).fetchall()
        ]
        if scope["kind"] == "ungrouped":
            if 0 not in ids:
                ids.append(0)
            ids.append(None)
        return ids
