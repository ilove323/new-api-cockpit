"""Unified, credential-free records for user, KEY and quota operations.

Reads only initiated management and scheduler records. Internal frozen target
lists are not audit entries. Requests are journaled before any upstream call.
"""

import base64
from datetime import datetime
import json
import uuid

from psycopg.types.json import Jsonb

from new_api_cockpit import balance


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
TARGET_PREVIEW = 3


def ready():
    from new_api_cockpit.user_management import ManagementError

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
            0,  # A rule targets user groups, not its executing administrator.
            Jsonb(safe(before)),
            Jsonb(safe(after)),
        ),
    )


def _operator(username):
    from new_api_cockpit import user_management as management

    operator = management.actor(username)
    ready()
    return operator


def _cursor(value):
    from new_api_cockpit.user_management import ManagementError

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


def _identity(user_id, *snapshots):
    """Names belong to the target ID; never use an actor or a KEY's name."""
    for saved in snapshots:
        if isinstance(saved, dict):
            if saved.get("id") != user_id:
                continue
            name = saved.get("username")
        else:
            name = saved
        if isinstance(name, str) and name:
            return {"id": user_id, "username": name}
    return {"id": user_id, "username": ""}


def _resolve_users(targets):
    """Batch-resolve missing names, without PAT access or history writes."""
    from new_api_cockpit import quota

    missing_names = [u for u in targets if not u["username"] and u["id"] is not None]
    if missing_names:
        with quota.connect() as conn:
            users = {
                user["id"]: user["username"]
                for user in conn.execute(
                    "SELECT id,username FROM users WHERE id=ANY(%s)",
                    (sorted({user["id"] for user in missing_names}),),
                )
            }
        for user in missing_names:
            user["username"] = users.get(user["id"], "")


def _rule_target(rule_id, before, after):
    groups = after.get("groups", before.get("groups"))
    return {
        "id": rule_id,
        "groups": (
            sorted({g for g in groups if isinstance(g, str)})
            if isinstance(groups, list)
            else None
        ),
    }


def _resolve_rules(conn, targets):
    missing = [r for r in targets if r["groups"] is None]
    if missing:
        groups = {
            r["rule_id"]: r["groups"]
            for r in conn.execute(
                """SELECT rule_id,array_agg(group_name ORDER BY group_name) AS groups
                FROM quota_schedule_rule_groups WHERE rule_id=ANY(%s) GROUP BY rule_id""",
                (sorted({r["id"] for r in missing}),),
            )
        }
        for rule in missing:
            rule["groups"] = groups.get(rule["id"], [])


def _header_targets(conn, page, ids, schedule_ids):
    """At most three unique initiated users per header, with an exact count.

    Preview/unsent target tables are deliberately never read. Only slim identity
    projections for this page cross the DB boundary, not every item's payload.
    """
    headers = {(r["source"], r["id"]): r for r in page}
    for row in page:
        row["target_users"] = []
        row["target_user_count"] = 0
    rules = []
    if ids:
        targets = conn.execute(
            """WITH item_targets AS (
                SELECT operation_id,target_id,
                       CASE WHEN target_type='user' THEN NULLIF(target_id,0)
                            ELSE NULLIF(user_id,0) END AS user_id,
                       before_data->'target_user' AS before_user,
                       after_data->'target_user' AS after_user,
                       CASE WHEN target_type='user' THEN before_data->>'username' END AS before_username,
                       CASE WHEN target_type='user' THEN after_data->>'username' END AS after_username
                FROM user_management_operation_items
                WHERE operation_id=ANY(%s) AND target_type IN ('user','token')
            ), owners AS (
                SELECT DISTINCT ON (operation_id,user_id) * FROM item_targets
                ORDER BY operation_id,user_id,target_id
            ), ranked AS (
                SELECT *,count(*) OVER (PARTITION BY operation_id) AS user_count,
                       row_number() OVER (PARTITION BY operation_id ORDER BY user_id) AS sample_number
                FROM owners
            ) SELECT * FROM ranked WHERE sample_number<=%s ORDER BY operation_id,sample_number""",
            (ids, TARGET_PREVIEW),
        )
        for target in targets:
            row = headers[("management", str(target["operation_id"]))]
            parameters = row["parameters"] or {}
            row["target_user_count"] = target["user_count"]
            row["target_users"].append(
                _identity(
                    target["user_id"],
                    target["after_user"],
                    target["before_user"],
                    parameters.get("target_user"),
                    target["before_username"],
                    target["after_username"],
                    parameters.get("username")
                    if row["action"] == "user.create"
                    else None,
                )
            )
        for item in conn.execute(
            """SELECT operation_id,target_id,before_data,after_data
            FROM user_management_operation_items
            WHERE operation_id=ANY(%s) AND target_type='quota_rule'""",
            (ids,),
        ):
            rule = _rule_target(
                item["target_id"], item["before_data"], item["after_data"]
            )
            headers[("management", str(item["operation_id"]))]["target_rule"] = rule
            rules.append(rule)
    if schedule_ids:
        for item in conn.execute(
            """WITH ranked AS (
                SELECT run_id,user_id,username,count(*) OVER (PARTITION BY run_id) AS user_count,
                       row_number() OVER (PARTITION BY run_id ORDER BY user_id) AS sample_number
                FROM quota_schedule_run_items WHERE run_id=ANY(%s)
            ) SELECT * FROM ranked WHERE sample_number<=%s ORDER BY run_id,sample_number""",
            (schedule_ids, TARGET_PREVIEW),
        ):
            row = headers[("schedule", str(item["run_id"]))]
            row["target_user_count"] = item["user_count"]
            row["target_users"].append(_identity(item["user_id"], item["username"]))
    _resolve_rules(conn, rules)


def list_records(username, args):
    from new_api_cockpit.user_management import ManagementError

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
        # Counts and target names must describe the same set of initiated items,
        # even when the next request wave starts while this page is being read.
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
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
        _header_targets(conn, page, ids, schedule_ids)
    for row in page:
        row["counts"] = (by_id if row["source"] == "management" else by_run).get(
            row["id"], {}
        )
    _resolve_users([user for row in page for user in row["target_users"]])
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
    from new_api_cockpit.user_management import ManagementError, Forbidden

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
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        if source == "management":
            owner = conn.execute(
                """SELECT operator_id,parameters FROM user_management_operations o WHERE id=%s
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
        rules, users = [], []
        for row in rows[:100]:
            if row["target_type"] == "quota_rule":
                row["target_rule"] = _rule_target(
                    row["target_id"], row["before_data"], row["after_data"]
                )
                row["user_id"] = None
                rules.append(row["target_rule"])
                continue
            user_id = (
                row["target_id"] if row["target_type"] == "user" else row["user_id"]
            ) or None
            before_data, after_data = row["before_data"], row["after_data"]
            parameters = owner.get("parameters", {})
            row["target_user"] = _identity(
                user_id,
                after_data.get("target_user"),
                before_data.get("target_user"),
                parameters.get("target_user"),
                before_data.get("username") if row["target_type"] == "user" else None,
                after_data.get("username") if row["target_type"] == "user" else None,
                parameters.get("username") if user_id is None else None,
            )
            row["user_id"] = user_id
            users.append(row["target_user"])
        _resolve_rules(conn, rules)
    _resolve_users(users)
    return {
        "rows": rows[:100],
        "next_after": rows[99]["target_id"] if len(rows) > 100 else None,
    }
