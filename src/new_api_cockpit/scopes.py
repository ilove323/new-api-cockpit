"""Channel-tag ledgers. Only the monitoring database is ever modified."""

from datetime import datetime
from new_api_cockpit import balance
from new_api_cockpit.locks import BALANCE_LOCK

ALL = 1


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
        SELECT id,id,%s FROM balance_scopes s
        WHERE NOT EXISTS (SELECT 1 FROM balance_settings b WHERE b.scope_id=s.id)
        ON CONFLICT DO NOTHING""",
        (datetime.now(balance.TZ).date().replace(day=1),),
    )


def refresh_scopes(*, full_discovery=False):
    balance.require_schema()
    with balance.connect() as conn:
        conn.execute("SELECT pg_advisory_xact_lock(%s)", (BALANCE_LOCK,))
        state = conn.execute(
            "SELECT history_discovered FROM channel_catalog_sync_state WHERE id=1"
        ).fetchone()
        discover = full_discovery or not state["history_discovered"]
        # A historical scan is permitted once on initialization or explicitly.
        # Routine catalog refreshes read only the small channels table.
        live = balance.source_channels(include_deleted=discover)
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
            (kind<>'tag' OR tag_value=ANY(%s::text[]))
            WHERE is_visible IS DISTINCT FROM (kind<>'tag' OR tag_value=ANY(%s::text[]))""",
            (tags, tags),
        )
        ensure_settings(conn)
        # Deleted channels found only in archives retain ID/name and default to ungrouped.
        if discover:
            conn.execute("""INSERT INTO balance_channel_inventory(channel_id,channel_name)
            SELECT DISTINCT ON (channel_id) channel_id,channel_name FROM balance_month_channels
            WHERE channel_id IS NOT NULL ORDER BY channel_id,month DESC
            ON CONFLICT(channel_id) DO NOTHING""")
            conn.execute(
                "UPDATE channel_catalog_sync_state SET history_discovered=true WHERE id=1 AND NOT history_discovered"
            )
    return live


def list_scopes(refresh=True):
    if refresh:
        refresh_scopes()
    with balance.connect() as conn:
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
