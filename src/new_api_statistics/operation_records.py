"""Unified, credential-free records for user, KEY and quota operations.

Reads only initiated management and scheduler records. Internal frozen target
lists are not audit entries. Requests are journaled before any upstream call.
"""

import base64
from datetime import datetime
import json
import uuid

from psycopg.types.json import Jsonb

from new_api_statistics import balance


RULE_FIELDS = (
    "enabled",
    "period",
    "operation",
    "amount_units",
    "executor_user_id",
    "executor_username",
    "version",
    "groups",
    "next_run_at",
    "deleted_at",
)


def ready():
    from new_api_statistics.user_management import ManagementError

    if not balance.configured():
        raise ManagementError("写操作需要监控数据库保存操作记录。")
    balance.require_schema()


def begin_quota(operator, username, targets, ids, mode, units):
    ready()
    operation_id = uuid.uuid4()
    with balance.connect() as conn:
        conn.execute(
            """INSERT INTO user_management_operations
            (id,operator_id,operator_name,action,parameters,state,expires_at,confirmed_at)
            VALUES(%s,%s,%s,%s,%s,'running',now(),now())""",
            (
                operation_id,
                operator["id"],
                username,
                "quota." + mode,
                Jsonb({"mode": mode, "amount_units": units}),
            ),
        )
        conn.cursor().executemany(
            """INSERT INTO user_management_operation_targets
            (operation_id,target_type,target_id,user_id,before_data,after_data)
            VALUES(%s,'user',%s,%s,%s,%s)""",
            [
                (
                    operation_id,
                    i,
                    i,
                    Jsonb(
                        {
                            "username": targets[i]["username"],
                            "quota": targets[i]["quota"],
                        }
                    ),
                    Jsonb({"mode": mode, "amount_units": units}),
                )
                for i in ids
            ],
        )
    return operation_id


def start_quota_wave(operation_id, ids):
    with balance.connect() as conn:
        conn.execute(
            "SELECT id FROM user_management_operations WHERE id=%s FOR UPDATE",
            (operation_id,),
        )
        inserted = conn.execute(
            """INSERT INTO user_management_operation_items
            (operation_id,target_type,target_id,user_id,before_data,after_data,state,started_at)
            SELECT operation_id,target_type,target_id,user_id,before_data,after_data,'sending',now()
            FROM user_management_operation_targets WHERE operation_id=%s AND target_id=ANY(%s)""",
            (operation_id, ids),
        )
        if inserted.rowcount != len(ids):
            raise ValueError("待执行名单已失效，未发送当前组请求。")
        conn.execute(
            "DELETE FROM user_management_operation_targets WHERE operation_id=%s AND target_id=ANY(%s)",
            (operation_id, ids),
        )


def finish_quota(operation_id, results, *, final=True):
    with balance.connect() as conn:
        for row in results:
            # No raw upstream error messages or credentials in the audit journal.
            message = (
                ""
                if row["ok"]
                else (
                    "结果不明确，核对实际余额后处理，勿直接重试。"
                    if row["state"] == "unknown"
                    else "请求失败，请核对 New API。"
                )
            )
            conn.execute(
                """UPDATE user_management_operation_items SET state=%s,message=%s,finished_at=now()
                WHERE operation_id=%s AND target_id=%s""",
                (row["state"], message, operation_id, row["id"]),
            )
        if final:
            conn.execute(
                "DELETE FROM user_management_operation_targets WHERE operation_id=%s",
                (operation_id,),
            )
            if not conn.execute(
                "SELECT 1 FROM user_management_operation_items WHERE operation_id=%s LIMIT 1",
                (operation_id,),
            ).fetchone():
                conn.execute(
                    "DELETE FROM user_management_operations WHERE id=%s",
                    (operation_id,),
                )
                return
            conn.execute(
                """UPDATE user_management_operations SET state=CASE WHEN EXISTS
                (SELECT 1 FROM user_management_operation_items WHERE operation_id=%s AND state<>'success')
                THEN 'partial' ELSE 'completed' END,finished_at=now() WHERE id=%s""",
                (operation_id, operation_id),
            )


def record_rule(conn, operator, action, rule_id, before, after):
    """Rule and audit changes commit in the same monitoring transaction."""
    operation_id = uuid.uuid4()

    def safe(value):
        return {
            k: (v.isoformat() if isinstance(v, datetime) else v)
            for k, v in (value or {}).items()
            if k in RULE_FIELDS
        }

    conn.execute(
        """INSERT INTO user_management_operations
        (id,operator_id,operator_name,action,state,expires_at,confirmed_at,finished_at)
        VALUES(%s,%s,%s,%s,'completed',now(),now(),now())""",
        (operation_id, operator["id"], operator["username"], action),
    )
    conn.execute(
        """INSERT INTO user_management_operation_items
        (operation_id,target_type,target_id,user_id,before_data,after_data,state,started_at,finished_at)
        VALUES(%s,'quota_rule',%s,%s,%s,%s,'success',now(),now())""",
        (
            operation_id,
            rule_id,
            operator["id"],
            Jsonb(safe(before)),
            Jsonb(safe(after)),
        ),
    )


def _operator(username):
    from new_api_statistics import user_management as management

    operator = management.actor(username)
    ready()
    return operator


def _cursor(value):
    from new_api_statistics.user_management import ManagementError

    try:
        if not isinstance(value, str) or len(value) > 512:
            raise ValueError()
        values = json.loads(base64.b64decode(value, altchars=b"-_", validate=True))
        if (
            not isinstance(values, list)
            or len(values) != 3
            or values[1] not in ("management", "schedule")
        ):
            raise ValueError()
        timestamp = datetime.fromisoformat(values[0])
        if (
            timestamp.tzinfo is None
            or not isinstance(values[2], str)
            or len(values[2]) > 64
        ):
            raise ValueError()
        return timestamp, values[1], values[2]
    except (ValueError, TypeError, KeyError):
        raise ManagementError("操作记录分页参数无效。") from None


def list_records(username, args):
    from new_api_statistics.user_management import ManagementError

    operator = _operator(username)
    params = []
    clauses = []
    if operator["role"] != 100:
        clauses.append("operator_id=%s")
        params.append(operator["id"])
    kind = args.get("kind", "")
    if kind not in ("", "user", "token", "quota", "schedule"):
        raise ManagementError("操作记录类型无效。")
    if kind:
        clauses.append("action LIKE %s")
        params.append(kind + ".%")
    if args.get("before"):
        clauses.append("(occurred_at,source,id)<(%s,%s,%s)")
        params.extend(_cursor(args["before"]))
    where = " AND ".join(clauses) or "TRUE"
    # Page headers first; count details only for these 51 headers, not all history.
    with balance.connect() as conn:
        rows = conn.execute(
            """WITH records AS (
            SELECT id::text,'management'::text AS source,created_at AS occurred_at,
                   operator_id,operator_name,action,state,parameters
            FROM user_management_operations o WHERE EXISTS
                (SELECT 1 FROM user_management_operation_items i WHERE i.operation_id=o.id)
            UNION ALL
            SELECT id::text,'schedule',scheduled_for,
                   (snapshot->>'executor_user_id')::bigint,snapshot->>'executor_username',
                   'schedule.execute',status,snapshot FROM quota_schedule_runs r WHERE EXISTS
                (SELECT 1 FROM quota_schedule_run_items i WHERE i.run_id=r.id)
        ) SELECT * FROM records WHERE """
            + where
            + " ORDER BY occurred_at DESC,source DESC,id DESC LIMIT 51",
            params,
        ).fetchall()
        page = rows[:50]
        ids = [uuid.UUID(r["id"]) for r in page if r["source"] == "management"]
        counts = (
            conn.execute(
                """SELECT operation_id,count(*) AS total,
            count(*) FILTER(WHERE state='success') AS success,
            count(*) FILTER(WHERE state IN ('failed','conflict')) AS failed,
            count(*) FILTER(WHERE state IN ('unknown','sending')) AS uncertain
            FROM user_management_operation_items WHERE operation_id=ANY(%s)
            GROUP BY operation_id""",
                (ids,),
            ).fetchall()
            if ids
            else []
        )
        by_id = {
            str(r["operation_id"]): {k: v for k, v in r.items() if k != "operation_id"}
            for r in counts
        }
        schedule_ids = [int(r["id"]) for r in page if r["source"] == "schedule"]
        schedule = (
            conn.execute(
                """SELECT run_id AS id,count(*) AS total,
            count(*) FILTER(WHERE status='success') AS success,
            count(*) FILTER(WHERE status='failed') AS failed,
            count(*) FILTER(WHERE status IN ('unknown','sending')) AS uncertain
            FROM quota_schedule_run_items WHERE run_id=ANY(%s) GROUP BY run_id""",
                (schedule_ids,),
            ).fetchall()
            if schedule_ids
            else []
        )
        by_run = {
            str(r["id"]): {k: v for k, v in r.items() if k != "id"} for r in schedule
        }
    for row in page:
        row["counts"] = (by_id if row["source"] == "management" else by_run).get(
            row["id"], {}
        )
    next_before = None
    if len(rows) > 50:
        last = page[-1]
        next_before = base64.urlsafe_b64encode(
            json.dumps(
                [last["occurred_at"].isoformat(), last["source"], last["id"]]
            ).encode()
        ).decode()
    return {"rows": page, "next_before": next_before}


def detail(username, source, record_id, after=-1):
    from new_api_statistics.user_management import ManagementError, Forbidden

    operator = _operator(username)
    try:
        if source not in ("management", "schedule"):
            raise ValueError()
        object_id = uuid.UUID(record_id) if source == "management" else int(record_id)
        after = int(after)
        if after < -1 or (source == "schedule" and object_id <= 0):
            raise ValueError()
    except (TypeError, ValueError, AttributeError):
        raise ManagementError("操作记录参数无效。") from None
    with balance.connect() as conn:
        if source == "management":
            owner = conn.execute(
                """SELECT operator_id FROM user_management_operations o WHERE id=%s
                AND EXISTS (SELECT 1 FROM user_management_operation_items i WHERE i.operation_id=o.id)""",
                (object_id,),
            ).fetchone()
        else:
            owner = conn.execute(
                """SELECT (snapshot->>'executor_user_id')::bigint AS operator_id FROM quota_schedule_runs r WHERE id=%s
                AND EXISTS (SELECT 1 FROM quota_schedule_run_items i WHERE i.run_id=r.id)""",
                (object_id,),
            ).fetchone()
        if not owner or (
            operator["role"] != 100 and owner["operator_id"] != operator["id"]
        ):
            raise Forbidden("无权查看该操作记录。")
        if source == "management":
            rows = conn.execute(
                """SELECT target_type,target_id,user_id,before_data,after_data,state,message
                FROM user_management_operation_items WHERE operation_id=%s AND target_id>%s
                ORDER BY target_id LIMIT 101""",
                (object_id, after),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT 'user' AS target_type,user_id AS target_id,user_id,
                jsonb_build_object('username',username,'display_name',display_name,'group',group_name) AS before_data,
                jsonb_build_object('operation',operation,'amount_units',amount_units) AS after_data,
                status AS state,message FROM quota_schedule_run_items WHERE run_id=%s AND user_id>%s
                ORDER BY user_id LIMIT 101""",
                (object_id, after),
            ).fetchall()
    return {
        "rows": rows[:100],
        "next_after": rows[99]["target_id"] if len(rows) > 100 else None,
    }
