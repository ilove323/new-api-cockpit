"""Freeze targets, durably mark sending before HTTP, and never resend uncertain mutations."""

from concurrent.futures import ThreadPoolExecutor

from new_api_statistics import balance, quota
from new_api_statistics import quota_schedule as schedules


def _targets(groups, admin):
    if not groups or not set(groups).issubset(schedules.group_options()):
        raise ValueError("规则中的用户组已不存在，请更新规则；本次未修改额度。")
    with quota.connect() as conn:
        rows = conn.execute(
            """SELECT id,username,COALESCE(display_name,'') AS display_name,
            COALESCE("group",'') AS group_name,role FROM users
            WHERE deleted_at IS NULL AND status=1 AND COALESCE("group",'')=ANY(%s) ORDER BY id""",
            (groups,),
        ).fetchall()
    for row in rows:
        if admin["role"] != 100 and admin["role"] <= row["role"]:
            raise ValueError("组内存在执行管理员无权调整的用户；本次未修改额度。")
    return rows


def _check_wave(ids, groups, admin):
    with quota.connect() as conn:
        rows = conn.execute(
            """SELECT id,role FROM users WHERE id=ANY(%s)
            AND deleted_at IS NULL AND status=1 AND COALESCE("group",'')=ANY(%s)""",
            (ids, groups),
        ).fetchall()
    return {
        row["id"] for row in rows if admin["role"] == 100 or admin["role"] > row["role"]
    }


def _attempt(admin, item):
    try:
        quota._call_manage(
            admin, item["user_id"], item["operation"], item["amount_units"]
        )
    except quota.QuotaRequestUncertain:
        return {
            "user_id": item["user_id"],
            "status": "unknown",
            "message": "请求结果不明确，请核对 New API 额度及审计日志；未自动重试。",
        }
    except quota.QuotaError:
        return {
            "user_id": item["user_id"],
            "status": "failed",
            "message": "New API 拒绝了额度调整，请核对用户权限及服务日志。",
        }
    except Exception:
        # Network/process failures can occur after upstream commit. Do not leak credentials.
        return {
            "user_id": item["user_id"],
            "status": "unknown",
            "message": "请求异常，结果需人工核对；未自动重试。",
        }
    return {"user_id": item["user_id"], "status": "success", "message": ""}


def _abort(run_id, message):
    with balance.connect() as conn:
        run = conn.execute(
            "SELECT status FROM quota_schedule_runs WHERE id=%s FOR UPDATE", (run_id,)
        ).fetchone()
        if not run or run["status"] != "running":
            return
        successes = conn.execute(
            "SELECT count(*) AS n FROM quota_schedule_run_items WHERE run_id=%s AND status='success'",
            (run_id,),
        ).fetchone()["n"]
        schedules.finish_run(
            conn, run_id, message, force_status="partial" if successes else "failed"
        )


def execute(run_id, leader_check):
    """Only called by the lock-holding worker; it must stop if its lock connection dies."""
    leader_check()
    with balance.connect() as conn:
        run = conn.execute(
            """SELECT x.*,r.enabled,r.deleted_at FROM quota_schedule_runs x
            JOIN quota_schedule_rules r ON r.id=x.rule_id WHERE x.id=%s FOR UPDATE OF x,r""",
            (run_id,),
        ).fetchone()
        if not run or run["status"] != "queued":
            return
        if not run["enabled"] or run["deleted_at"] is not None:
            schedules.finish_run(
                conn,
                run_id,
            )
            return
        conn.execute(
            "UPDATE quota_schedule_runs SET status='running',started_at=now() WHERE id=%s",
            (run_id,),
        )
        frozen = run["snapshot"]
    try:
        admin = schedules.actor(user_id=frozen["executor_user_id"])
        targets = _targets(frozen["groups"], admin)
    except ValueError as exc:
        _abort(run_id, str(exc))
        return
    leader_check()
    with balance.connect() as conn:
        status = conn.execute(
            "SELECT status FROM quota_schedule_runs WHERE id=%s FOR UPDATE", (run_id,)
        ).fetchone()
        if not status or status["status"] != "running":
            return
        for target in targets:
            conn.execute(
                """INSERT INTO quota_schedule_run_targets
                (run_id,user_id,username,display_name,group_name,operation,amount_units)
                VALUES (%s,%s,%s,%s,%s,%s,%s)""",
                (
                    run_id,
                    target["id"],
                    target["username"],
                    target["display_name"],
                    target["group_name"],
                    frozen["operation"],
                    frozen["amount_units"],
                ),
            )
    # Snapshot rows and sending states are COMMITTED before any external request.
    with ThreadPoolExecutor(max_workers=quota.CONCURRENT_REQUESTS) as pool:
        while True:
            leader_check()
            with balance.connect() as conn:
                current = conn.execute(
                    "SELECT status FROM quota_schedule_runs WHERE id=%s FOR UPDATE",
                    (run_id,),
                ).fetchone()
                if not current or current["status"] != "running":
                    return
                wave = conn.execute(
                    """SELECT * FROM quota_schedule_run_targets WHERE run_id=%s
                    ORDER BY user_id LIMIT %s""",
                    (run_id, quota.CONCURRENT_REQUESTS),
                ).fetchall()
                if not wave:
                    schedules.finish_run(
                        conn, run_id, "暂无符合条件的启用用户。" if not targets else ""
                    )
                    return
            try:
                admin = schedules.actor(user_id=frozen["executor_user_id"])
                eligible = _check_wave(
                    [item["user_id"] for item in wave], frozen["groups"], admin
                )
            except ValueError as exc:
                _abort(run_id, str(exc))
                return
            leader_check()
            with balance.connect() as conn:
                current = conn.execute(
                    "SELECT status FROM quota_schedule_runs WHERE id=%s FOR UPDATE",
                    (run_id,),
                ).fetchone()
                if not current or current["status"] != "running":
                    return
                conn.execute(
                    """INSERT INTO quota_schedule_run_items
                    (run_id,user_id,username,display_name,group_name,operation,amount_units,status,request_started_at)
                    SELECT run_id,user_id,username,display_name,group_name,operation,amount_units,'sending',now()
                    FROM quota_schedule_run_targets WHERE run_id=%s AND user_id=ANY(%s)""",
                    (
                        run_id,
                        [
                            item["user_id"]
                            for item in wave
                            if item["user_id"] in eligible
                        ],
                    ),
                )
                conn.execute(
                    "DELETE FROM quota_schedule_run_targets WHERE run_id=%s AND user_id=ANY(%s)",
                    (
                        run_id,
                        [item["user_id"] for item in wave],
                    ),
                )
            results = list(
                pool.map(
                    lambda item: _attempt(admin, item),
                    [item for item in wave if item["user_id"] in eligible],
                )
            )
            with balance.connect() as conn:
                current = conn.execute(
                    "SELECT status FROM quota_schedule_runs WHERE id=%s FOR UPDATE",
                    (run_id,),
                ).fetchone()
                if not current or current["status"] != "running":
                    return  # Another leader recovered this run. Never overwrite its unknown records.
                for result in results:
                    conn.execute(
                        """UPDATE quota_schedule_run_items SET status=%s,message=%s,finished_at=now()
                        WHERE run_id=%s AND user_id=%s AND status='sending'""",
                        (
                            result["status"],
                            result["message"],
                            run_id,
                            result["user_id"],
                        ),
                    )
                if len(results) != len(wave) or any(
                    result["status"] != "success" for result in results
                ):
                    schedules.finish_run(
                        conn,
                        run_id,
                        "当前组已收集结果，停止后续组；不自动重试或回滚。",
                        force_status="partial"
                        if len(results) != len(wave)
                        and results
                        and all(r["status"] == "success" for r in results)
                        else None,
                    )
                    return
