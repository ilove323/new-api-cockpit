"""Schedule configuration and audit data live only in the monitoring database."""

import json
from datetime import datetime, timedelta
from decimal import Decimal

from psycopg.types.json import Jsonb

from new_api_statistics import balance, quota
from new_api_statistics.locks import QUOTA_SCHEDULER_LOCK
from new_api_statistics.report import TZ

NOTIFY_CHANNEL = "quota_schedule_changed"
LEADER_LOCK = QUOTA_SCHEDULER_LOCK
START_WINDOW = timedelta(minutes=5)


class ScheduleConflict(ValueError):
    pass


class ScheduleForbidden(ValueError):
    pass


class ScheduleUnavailable(ValueError):
    pass


def initialize():
    if not balance.configured():
        raise ScheduleUnavailable(
            "请先配置 MONITOR_DATABASE_URL，定时规则需要独立监控数据库。"
        )
    try:
        balance.require_schema()
    except balance.SchemaUnavailable as exc:
        raise ScheduleUnavailable(str(exc)) from None


def next_boundary(period, now):
    """Strictly future boundary, always in Asia/Shanghai, never a fixed 30-day month."""
    if period not in {"daily", "weekly", "monthly"}:
        raise ValueError("请选择每天、每周或每月。")
    if now.tzinfo is None:
        raise ValueError("调度时间必须带时区。")
    now = now.astimezone(TZ)
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if period == "daily":
        return midnight + timedelta(days=1)
    if period == "weekly":
        return midnight + timedelta(days=7 - midnight.weekday())
    return midnight.replace(
        year=now.year + (now.month == 12), month=now.month % 12 + 1, day=1
    )


def actor(*, username=None, user_id=None, require_pat=True):
    with quota.connect() as conn:
        row = conn.execute(
            "SELECT id,username,role,status,access_token FROM users "
            "WHERE "
            + ("id=%s" if user_id is not None else "username=%s")
            + " AND deleted_at IS NULL",
            (user_id if user_id is not None else username,),
        ).fetchone()
    if not row or row["role"] < 10 or row["status"] != 1:
        raise quota.QuotaError("执行管理员不存在、已停用或权限不足。")
    if require_pat and not row["access_token"]:
        raise quota.QuotaError("当前管理员尚未生成 New API PAT，请先生成再启用规则。")
    return row


def group_options():
    """Configured user groups plus groups still occupied by undeleted accounts."""
    with quota.connect() as conn:
        names = {
            r["group_name"]
            for r in conn.execute(
                "SELECT DISTINCT COALESCE(\"group\",'') AS group_name FROM users WHERE deleted_at IS NULL"
            ).fetchall()
        }
        option = conn.execute(
            "SELECT value FROM options WHERE key='GroupRatio'"
        ).fetchone()
    if option:
        try:
            configured = json.loads(option["value"] or "{}")
        except (ValueError, TypeError):
            raise ValueError(
                "New API 用户组配置无法解析，请检查 GroupRatio。"
            ) from None
        if not isinstance(configured, dict):
            raise ValueError("New API 用户组配置格式无效。")
        names.update(configured)
    return sorted(names)


def validate_rule(body):
    if not isinstance(body, dict) or type(body.get("enabled")) is not bool:
        raise ValueError("请填写规则并明确是否启用。")
    period = body.get("period")
    if period not in {"daily", "weekly", "monthly"}:
        raise ValueError("请选择每天、每周或每月。")
    _, operation, units, _ = quota.validate_request(
        {
            "user_ids": [1],
            "mode": body.get("operation"),
            "amount_yuan": body.get("amount_yuan"),
        }
    )
    groups = body.get("groups")
    if (
        not isinstance(groups, list)
        or not groups
        or len(groups) > 1000
        or any(not isinstance(g, str) or len(g) > 256 for g in groups)
        or len(set(groups)) != len(groups)
    ):
        raise ValueError("请勾选有效且不重复的用户组。")
    return {
        "enabled": body["enabled"],
        "period": period,
        "operation": operation,
        "amount_units": units,
        "groups": sorted(groups),
    }


def _owned(row, admin):
    if not row or row["deleted_at"] is not None:
        raise ValueError("规则不存在或已删除。")
    if admin["role"] != 100 and admin["id"] != row["executor_user_id"]:
        raise ScheduleForbidden("只能修改自己的规则；超级管理员可管理全部规则。")


def _version(body, row):
    if type(body.get("version")) is not int or body["version"] != row["version"]:
        raise ScheduleConflict("规则已被其他管理员修改，请重新读取。")


def _notify(conn):
    conn.execute("SELECT pg_notify(%s, '')", (NOTIFY_CHANNEL,))


def _serialize_rule(row):
    result = dict(row)
    result["amount_yuan"] = str(
        Decimal(result.pop("amount_units")) / quota.QUOTA_PER_YUAN
    )
    result.pop("deleted_at", None)
    return result


def list_rules(username):
    if not balance.configured():
        return {
            "configured": False,
            "rows": [],
            "groups": [],
            "timezone": "Asia/Shanghai",
        }
    initialize()
    admin = actor(username=username, require_pat=False)
    groups = group_options()
    with balance.connect() as conn:
        rows = conn.execute("""SELECT r.*,
            ARRAY(SELECT group_name FROM quota_schedule_rule_groups g WHERE g.rule_id=r.id ORDER BY group_name) AS groups,
            (SELECT status FROM quota_schedule_runs x WHERE x.rule_id=r.id ORDER BY id DESC LIMIT 1) AS last_status
            FROM quota_schedule_rules r WHERE deleted_at IS NULL ORDER BY id""").fetchall()
    serialized = []
    for row in rows:
        row = _serialize_rule(row)
        row["can_edit"] = admin["role"] == 100 or admin["id"] == row["executor_user_id"]
        row["missing_groups"] = [g for g in row["groups"] if g not in groups]
        serialized.append(row)
    return {
        "configured": True,
        "rows": serialized,
        "groups": groups,
        "timezone": "Asia/Shanghai",
        "current_user_id": admin["id"],
        "current_username": admin["username"],
    }


def save_rule(username, body, rule_id=None, now=None):
    initialize()
    values = validate_rule(body)
    admin = actor(username=username, require_pat=values["enabled"])
    if values["enabled"] and not set(values["groups"]).issubset(set(group_options())):
        raise ValueError("所选用户组已不存在，请刷新组列表后再启用。")
    now = now or datetime.now(TZ)
    with balance.connect() as conn:
        old = None
        if rule_id is not None:
            old = conn.execute(
                "SELECT * FROM quota_schedule_rules WHERE id=%s FOR UPDATE", (rule_id,)
            ).fetchone()
            _owned(old, admin)
            _version(body, old)
        next_run = next_boundary(values["period"], now) if values["enabled"] else None
        # Preserve an already due boundary if only groups/amount/operation are edited.
        if (
            old
            and old["enabled"]
            and values["enabled"]
            and old["period"] == values["period"]
        ):
            next_run = old["next_run_at"]
        params = (
            values["enabled"],
            values["period"],
            values["operation"],
            values["amount_units"],
            admin["id"],
            admin["username"],
            next_run,
        )
        if old:
            conn.execute(
                """UPDATE quota_schedule_rules SET enabled=%s,period=%s,operation=%s,amount_units=%s,
                executor_user_id=%s,executor_username=%s,next_run_at=%s,version=version+1,updated_at=now()
                WHERE id=%s""",
                (*params, rule_id),
            )
            conn.execute(
                "DELETE FROM quota_schedule_rule_groups WHERE rule_id=%s", (rule_id,)
            )
        else:
            rule_id = conn.execute(
                """INSERT INTO quota_schedule_rules
                (enabled,period,operation,amount_units,executor_user_id,executor_username,next_run_at,created_by_user_id)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
                (*params, admin["id"]),
            ).fetchone()["id"]
        for group in values["groups"]:
            conn.execute(
                "INSERT INTO quota_schedule_rule_groups(rule_id,group_name) VALUES (%s,%s)",
                (rule_id, group),
            )
        _notify(conn)
    return {"id": rule_id, "next_run_at": next_run}


def delete_rule(username, rule_id, body):
    initialize()
    if not isinstance(body, dict):
        raise ValueError("请先读取规则再删除。")
    admin = actor(username=username, require_pat=False)
    with balance.connect() as conn:
        row = conn.execute(
            "SELECT * FROM quota_schedule_rules WHERE id=%s FOR UPDATE", (rule_id,)
        ).fetchone()
        _owned(row, admin)
        _version(body, row)
        conn.execute(
            """UPDATE quota_schedule_rules SET enabled=false,next_run_at=NULL,
            deleted_at=now(),updated_at=now(),version=version+1 WHERE id=%s""",
            (rule_id,),
        )
        _notify(conn)


def set_enabled(username, rule_id, body):
    initialize()
    if not isinstance(body, dict) or type(body.get("enabled")) is not bool:
        raise ValueError("请明确是否启用规则。")
    with balance.connect() as conn:
        row = conn.execute(
            "SELECT * FROM quota_schedule_rules WHERE id=%s AND deleted_at IS NULL",
            (rule_id,),
        ).fetchone()
        if not row:
            raise ValueError("规则不存在或已删除。")
        groups = [
            g["group_name"]
            for g in conn.execute(
                "SELECT group_name FROM quota_schedule_rule_groups WHERE rule_id=%s",
                (rule_id,),
            ).fetchall()
        ]
    return save_rule(
        username,
        {
            "enabled": body["enabled"],
            "version": body.get("version"),
            "groups": groups,
            "period": row["period"],
            "operation": row["operation"],
            "amount_yuan": str(Decimal(row["amount_units"]) / quota.QUOTA_PER_YUAN),
        },
        rule_id,
    )


def list_runs(before=None):
    initialize()
    if before is not None and (type(before) is not int or before <= 0):
        raise ValueError("执行记录分页参数无效。")
    with balance.connect() as conn:
        rows = conn.execute(
            """SELECT * FROM quota_schedule_runs
            WHERE (%s::bigint IS NULL OR id < %s) ORDER BY id DESC LIMIT 51""",
            (before, before),
        ).fetchall()
    return {
        "rows": rows[:50],
        "next_before": rows[49]["id"] if len(rows) > 50 else None,
    }


def run_detail(run_id, after=0):
    initialize()
    if type(after) is not int or after < 0:
        raise ValueError("用户记录分页参数无效。")
    with balance.connect() as conn:
        row = conn.execute(
            "SELECT * FROM quota_schedule_runs WHERE id=%s", (run_id,)
        ).fetchone()
        if not row:
            raise ValueError("执行记录不存在。")
        items = conn.execute(
            """SELECT * FROM quota_schedule_run_items
            WHERE run_id=%s AND user_id>%s ORDER BY user_id LIMIT 201""",
            (run_id, after),
        ).fetchall()
    for item in items:
        item["amount_yuan"] = str(
            Decimal(item.pop("amount_units")) / quota.QUOTA_PER_YUAN
        )
    return {
        "run": row,
        "rows": items[:200],
        "next_after": items[199]["user_id"] if len(items) > 200 else None,
    }


def claim_due(now):
    """Freeze all due rules atomically; unique occurrence IDs plus a leader avoid duplicates."""
    ids = []
    with balance.connect() as conn:
        rules = conn.execute(
            """SELECT * FROM quota_schedule_rules
            WHERE enabled AND deleted_at IS NULL AND next_run_at<=%s
            ORDER BY next_run_at,id FOR UPDATE SKIP LOCKED""",
            (now,),
        ).fetchall()
        for rule in rules:
            groups = [
                g["group_name"]
                for g in conn.execute(
                    "SELECT group_name FROM quota_schedule_rule_groups WHERE rule_id=%s ORDER BY group_name",
                    (rule["id"],),
                ).fetchall()
            ]
            frozen = {
                k: rule[k]
                for k in (
                    "period",
                    "operation",
                    "amount_units",
                    "executor_user_id",
                    "executor_username",
                    "version",
                )
            }
            frozen["groups"] = groups
            missed = now - rule["next_run_at"] > START_WINDOW
            run = conn.execute(
                """INSERT INTO quota_schedule_runs(rule_id,scheduled_for,snapshot,status,message,finished_at)
                VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING RETURNING id""",
                (
                    rule["id"],
                    rule["next_run_at"],
                    Jsonb(frozen),
                    "missed" if missed else "queued",
                    "错过执行窗口，不自动补发；期间错过的周期一并跳过。"
                    if missed
                    else "",
                    now if missed else None,
                ),
            ).fetchone()
            conn.execute(
                "UPDATE quota_schedule_rules SET next_run_at=%s WHERE id=%s",
                (next_boundary(rule["period"], now), rule["id"]),
            )
            if run and not missed:
                ids.append(run["id"])
    return ids


def finish_run(conn, run_id, message="", force_status=None):
    counts = conn.execute(
        """SELECT count(*)::integer AS total_count,
        count(*) FILTER (WHERE status='success')::integer AS success_count,
        count(*) FILTER (WHERE status='failed')::integer AS failed_count,
        count(*) FILTER (WHERE status='unknown')::integer AS unknown_count,
        count(*) FILTER (WHERE status='skipped')::integer AS skipped_count
        FROM quota_schedule_run_items WHERE run_id=%s""",
        (run_id,),
    ).fetchone()
    if force_status:
        status = force_status
    elif counts["unknown_count"]:
        status = "unknown"
    elif counts["success_count"] == counts["total_count"]:
        status = "success"
    else:
        status = "partial" if counts["success_count"] else "failed"
    conn.execute(
        """UPDATE quota_schedule_runs SET status=%s,finished_at=now(),message=%s,
        total_count=%s,success_count=%s,failed_count=%s,unknown_count=%s,skipped_count=%s
        WHERE id=%s""",
        (
            status,
            message,
            *(
                counts[k]
                for k in (
                    "total_count",
                    "success_count",
                    "failed_count",
                    "unknown_count",
                    "skipped_count",
                )
            ),
            run_id,
        ),
    )


def recover_interrupted():
    """Only the new lock-holding leader may recover; no mutation is ever resent."""
    with balance.connect() as conn:
        runs = conn.execute(
            "SELECT id,status FROM quota_schedule_runs WHERE status IN ('queued','running') FOR UPDATE"
        ).fetchall()
        for run in runs:
            conn.execute(
                """UPDATE quota_schedule_run_items SET status='unknown',finished_at=now(),
                message='执行进程中断，请核对 New API 额度和审计日志；未自动重试。'
                WHERE run_id=%s AND status='sending'""",
                (run["id"],),
            )
            conn.execute(
                """UPDATE quota_schedule_run_items SET status='skipped',finished_at=now(),message='执行进程中断，未发起。'
                WHERE run_id=%s AND status='pending'""",
                (run["id"],),
            )
            has_items = conn.execute(
                "SELECT EXISTS(SELECT 1 FROM quota_schedule_run_items WHERE run_id=%s) AS present",
                (run["id"],),
            ).fetchone()["present"]
            finish_run(
                conn,
                run["id"],
                "进程中断，不自动补发或续跑。",
                force_status="missed"
                if run["status"] == "queued"
                else "failed"
                if not has_items
                else None,
            )


def next_due():
    with balance.connect() as conn:
        return conn.execute("""SELECT min(next_run_at) AS due FROM quota_schedule_rules
            WHERE enabled AND deleted_at IS NULL""").fetchone()["due"]
