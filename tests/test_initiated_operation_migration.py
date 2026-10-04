"""Upgrade fixture: delete only uninitiated audit data, never source or money data."""

import os
from pathlib import Path
import unittest
import uuid

import psycopg
from psycopg import sql
from psycopg.rows import dict_row

DSN = os.environ.get("TEST_SCHEDULE_DATABASE_URL")
MIGRATIONS = Path(__file__).resolve().parents[1] / "src/new_api_cockpit/migrations"


@unittest.skipUnless(DSN, "Requires an isolated PostgreSQL fixture database")
class InitiatedOperationMigrationTest(unittest.TestCase):
    def test_upgrade_removes_uninitiated_data_and_preserves_sent_records(self):
        schema = "initiated_migration_" + uuid.uuid4().hex
        with psycopg.connect(DSN) as c:
            c.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        self.addCleanup(self.drop_schema, schema)
        ids = [uuid.uuid4() for _ in range(3)]
        with psycopg.connect(
            DSN, row_factory=dict_row, options="-c search_path=" + schema
        ) as c:
            for name in ("007_quota_schedules.sql", "009_user_management.sql"):
                c.execute((MIGRATIONS / name).read_text())
            c.execute("""CREATE TABLE users(id bigint, quota bigint);
                INSERT INTO users VALUES(1,1234567);
                CREATE TABLE balance_months(amount numeric);
                INSERT INTO balance_months VALUES(87654.321);
                INSERT INTO quota_schedule_rules(id,period,operation,amount_units,executor_user_id,
                    executor_username,created_by_user_id,next_run_at)
                    VALUES(1,'daily','add',500000,1,'admin',1,'2026-10-05T00:00:00+08:00');
                INSERT INTO quota_schedule_runs(id,rule_id,scheduled_for,snapshot,status)
                    VALUES(1,1,'2026-09-01','{}','missed'),
                    (2,1,'2026-09-02','{}','partial'),(3,1,'2026-09-03','{}','queued');""")
            for i, state in zip(ids, ("preview", "partial", "running")):
                c.execute(
                    """INSERT INTO user_management_operations
                    (id,operator_id,operator_name,action,state,expires_at)
                    VALUES(%s,1,'admin','token.group',%s,now())""",
                    (i, state),
                )
            for op, target, state, started in [
                (ids[0], 10, "pending", False),
                (ids[1], 11, "success", True),
                (ids[1], 12, "failed", True),
                (ids[1], 13, "unknown", True),
                (ids[1], 14, "conflict", True),
                (ids[1], 15, "pending", False),
                (ids[1], 16, "pending", True),
                (ids[2], 17, "sending", True),
                (ids[2], 18, "pending", False),
            ]:
                c.execute(
                    """INSERT INTO user_management_operation_items
                    (operation_id,target_type,target_id,user_id,state,started_at,before_data,after_data,message)
                    VALUES(%s,'token',%s,2,%s,CASE WHEN %s THEN now() END,
                    '{"group":"a"}','{"group":"b"}','fixture-safe-message')""",
                    (op, target, state, started),
                )
            for run, user, status, started in [
                (2, 1, "success", True),
                (2, 2, "failed", True),
                (2, 3, "unknown", True),
                (2, 4, "sending", True),
                (2, 5, "pending", False),
                (2, 6, "skipped", False),
                (2, 7, "skipped", True),
                (3, 8, "pending", False),
            ]:
                c.execute(
                    """INSERT INTO quota_schedule_run_items
                    (run_id,user_id,username,group_name,operation,amount_units,status,request_started_at)
                    VALUES(%s,%s,'fixture-user','a','add',500000,%s,CASE WHEN %s THEN now() END)""",
                    (run, user, status, started),
                )
            sent_before = c.execute("""SELECT * FROM user_management_operation_items
                WHERE state NOT IN ('pending') ORDER BY target_id""").fetchall()
            schedule_before = c.execute("""SELECT * FROM quota_schedule_run_items
                WHERE status NOT IN ('pending','skipped') ORDER BY user_id""").fetchall()
            c.execute((MIGRATIONS / "010_initiated_operation_records.sql").read_text())
            self.assertIsNone(
                c.execute(
                    "SELECT id FROM user_management_operations WHERE id=%s", (ids[0],)
                ).fetchone()
            )
            self.assertEqual(
                c.execute(
                    "SELECT * FROM user_management_operation_items WHERE target_id NOT IN (16) ORDER BY target_id"
                ).fetchall(),
                sent_before,
            )
            self.assertEqual(
                c.execute(
                    "SELECT state FROM user_management_operation_items WHERE target_id=16"
                ).fetchone()["state"],
                "unknown",
            )
            self.assertEqual(
                c.execute(
                    "SELECT * FROM quota_schedule_run_items WHERE user_id NOT IN (7) ORDER BY user_id"
                ).fetchall(),
                schedule_before,
            )
            self.assertEqual(
                c.execute(
                    "SELECT status FROM quota_schedule_run_items WHERE user_id=7"
                ).fetchone()["status"],
                "unknown",
            )
            self.assertEqual(
                [
                    r["id"]
                    for r in c.execute("SELECT id FROM quota_schedule_runs").fetchall()
                ],
                [2],
            )
            run = c.execute("SELECT * FROM quota_schedule_runs WHERE id=2").fetchone()
            self.assertEqual(
                (
                    run["total_count"],
                    run["success_count"],
                    run["failed_count"],
                    run["unknown_count"],
                ),
                (5, 1, 1, 2),
            )
            self.assertNotIn("skipped_count", run)
            self.assertEqual(
                c.execute("SELECT quota FROM users").fetchone()["quota"], 1234567
            )
            self.assertEqual(
                str(
                    c.execute("SELECT amount FROM balance_months").fetchone()["amount"]
                ),
                "87654.321",
            )
            self.assertEqual(
                c.execute("SELECT count(*) AS n FROM quota_schedule_rules").fetchone()[
                    "n"
                ],
                1,
            )
            self.assertEqual(
                c.execute(
                    "SELECT count(*) AS n FROM user_management_operation_targets"
                ).fetchone()["n"],
                0,
            )
            self.assertEqual(
                c.execute(
                    "SELECT count(*) AS n FROM quota_schedule_run_targets"
                ).fetchone()["n"],
                0,
            )
            for statement, params in [
                (
                    """INSERT INTO user_management_operation_items(operation_id,target_type,target_id,user_id,state)
                    VALUES(%s,'user',99,2,'pending')""",
                    (ids[1],),
                ),
                (
                    """INSERT INTO quota_schedule_run_items(run_id,user_id,username,group_name,operation,amount_units,status)
                    VALUES(2,99,'fixture','a','add',500000,'skipped')""",
                    (),
                ),
            ]:
                with self.assertRaises(psycopg.errors.CheckViolation):
                    with c.transaction():
                        c.execute(statement, params)

    def drop_schema(self, schema):
        with psycopg.connect(DSN) as c:
            c.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))
