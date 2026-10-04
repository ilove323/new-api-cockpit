"""Real SQL against isolated fixture schemas; upstream mutations are ALWAYS mocked."""

import os
import threading
import time
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from decimal import Decimal
from unittest.mock import patch

import psycopg
from psycopg import sql
from psycopg.rows import dict_row
from cryptography.fernet import Fernet

from new_api_statistics import balance, locks, notifications, quota, operation_records
from new_api_statistics import quota_timer
from new_api_statistics import (
    quota_schedule as schedules,
    quota_schedule_executor as executor,
)
from new_api_statistics.report import TZ

DSN = os.environ.get("TEST_SCHEDULE_DATABASE_URL") or os.environ.get(
    "MONITOR_DATABASE_URL"
)


@unittest.skipUnless(
    DSN, "Schedule SQL integration needs an isolated PostgreSQL test database"
)
class ScheduleDatabaseTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.schema = "quota_schedule_test_" + uuid.uuid4().hex
        with psycopg.connect(DSN) as conn:
            conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(cls.schema)))

    @classmethod
    def tearDownClass(cls):
        with psycopg.connect(DSN) as conn:
            conn.execute(
                sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(cls.schema))
            )

    def connect(self):
        return psycopg.connect(
            DSN, row_factory=dict_row, options="-c search_path=" + self.schema
        )

    def run_state(self, run_id):
        # Inspect internal execution state directly, not a redundant public API.
        with self.connect() as conn:
            run = conn.execute(
                "SELECT * FROM quota_schedule_runs WHERE id=%s", (run_id,)
            ).fetchone()
            if not run:
                raise ValueError("执行记录不存在。")
            rows = conn.execute(
                "SELECT * FROM quota_schedule_run_items WHERE run_id=%s ORDER BY user_id",
                (run_id,),
            ).fetchall()
        for row in rows:
            row["amount_yuan"] = str(
                Decimal(row["amount_units"]) / quota.QUOTA_PER_YUAN
            )
        return {"run": run, "rows": rows}

    def source_connect(self):
        return psycopg.connect(
            DSN,
            row_factory=dict_row,
            options="-c search_path="
            + self.schema
            + " -c default_transaction_read_only=on",
        )

    def setUp(self):
        self.patches = [
            patch.object(balance, "configured", return_value=True),
            patch.object(balance, "connect", self.connect),
            patch.object(quota, "connect", self.source_connect),
        ]
        for mocked in self.patches:
            mocked.start()
            self.addCleanup(mocked.stop)
        balance.initialize()
        with self.connect() as conn:
            conn.execute("TRUNCATE user_management_operations CASCADE")
            conn.execute(
                "TRUNCATE quota_schedule_run_targets,quota_schedule_run_items,quota_schedule_runs,quota_schedule_rule_groups,quota_schedule_rules RESTART IDENTITY"
            )
            conn.execute("""CREATE TABLE IF NOT EXISTS users (id bigint PRIMARY KEY,username text,display_name text,
                "group" text,role integer,status integer,access_token text,quota bigint,deleted_at timestamptz);
                CREATE TABLE IF NOT EXISTS options (key text PRIMARY KEY,value text)""")
            conn.execute("TRUNCATE users,options")
            conn.execute("""INSERT INTO users VALUES (1,'admin','Administrator','admins',100,1,'fixture-pat',0,NULL),
                (20,'manager','','admins',10,1,'fixture-manager-pat',0,NULL);
                INSERT INTO options VALUES ('GroupRatio','{"team-a":1,"empty":1,"admins":1}')""")
            for user_id in range(2, 14):
                conn.execute(
                    "INSERT INTO users VALUES (%s,%s,'Display','team-a',1,1,'',0,NULL)",
                    (user_id, f"user-{user_id}"),
                )
            conn.execute(
                "INSERT INTO users VALUES (14,'disabled','','team-a',1,2,'',0,NULL),(15,'deleted','','team-a',1,1,'',0,now()),(16,'other','','TEAM-A',1,1,'',0,NULL)"
            )
        self.now = datetime(2026, 10, 2, tzinfo=TZ)
        self.body = {
            "enabled": True,
            "period": "daily",
            "operation": "add",
            "amount_yuan": "100.01",
            "groups": ["team-a"],
        }

    def rule(self):
        return schedules.save_rule(
            "admin", self.body, now=self.now - timedelta(hours=1)
        )["id"]

    def queued(self):
        self.rule()
        return schedules.claim_due(self.now)[0]

    def test_create_save_version_pause_delete_history_and_secret_boundaries(self):
        rule_id = self.rule()
        response = schedules.list_rules("admin")
        rule = response["rows"][0]
        self.assertEqual(rule["amount_yuan"], "100.01")
        self.assertEqual(rule["next_run_at"], self.now)
        self.assertNotIn("fixture-pat", str(response))
        with self.assertRaises(schedules.ScheduleConflict):
            schedules.save_rule("admin", {**self.body, "version": 0}, rule_id, self.now)
        with self.assertRaises(schedules.ScheduleForbidden):
            schedules.save_rule(
                "manager", {**self.body, "version": 1}, rule_id, self.now
            )
        schedules.set_enabled("admin", rule_id, {"version": 1, "enabled": False})
        self.assertFalse(schedules.list_rules("admin")["rows"][0]["enabled"])
        with self.connect() as conn:
            conn.execute("UPDATE users SET access_token='' WHERE id=1")
        schedules.delete_rule("admin", rule_id, {"version": 2})
        self.assertEqual(schedules.list_rules("admin")["rows"], [])

    def test_claim_is_unique_under_concurrent_workers_and_advances_calendar(self):
        self.rule()
        with ThreadPoolExecutor(max_workers=2) as pool:
            claimed = list(pool.map(schedules.claim_due, [self.now, self.now]))
        self.assertEqual(sum(len(ids) for ids in claimed), 1)
        self.assertEqual(schedules.claim_due(self.now), [])
        self.assertEqual(schedules.next_due(), self.now + timedelta(days=1))

    def test_missed_cycles_have_no_operation_record_and_are_never_replayed(self):
        self.rule()
        self.assertEqual(schedules.claim_due(self.now + timedelta(days=40)), [])
        rows = [
            r
            for r in operation_records.list_records("admin", {"kind": "schedule"})[
                "rows"
            ]
            if r["source"] == "schedule"
        ]
        self.assertEqual(rows, [])
        with self.connect() as conn:
            self.assertEqual(
                conn.execute(
                    "SELECT count(*) AS n FROM quota_schedule_runs"
                ).fetchone()["n"],
                0,
            )
        self.assertEqual(schedules.next_due(), self.now + timedelta(days=41))

    def test_live_members_enabled_filter_five_concurrency_and_durable_sending_state(
        self,
    ):
        run_id = self.queued()
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO users VALUES (17,'new-user','','team-a',1,1,'',0,NULL)"
            )
        lock = threading.Lock()
        barrier = threading.Barrier(5)
        active = maximum = 0
        calls = []

        def mutate(admin, user_id, operation, units):
            nonlocal active, maximum
            with self.connect() as conn:
                state = conn.execute(
                    "SELECT status FROM quota_schedule_run_items WHERE run_id=%s AND user_id=%s",
                    (run_id, user_id),
                ).fetchone()
                self.assertEqual(state["status"], "sending")
            with lock:
                active += 1
                maximum = max(maximum, active)
                calls.append(user_id)
            if user_id < 12:
                barrier.wait(timeout=5)
            with lock:
                active -= 1
            self.assertEqual((operation, units), ("add", 50005000))

        with patch.object(quota, "_call_manage", side_effect=mutate):
            executor.execute(run_id, lambda: None)
        self.assertEqual(maximum, 5)
        self.assertEqual(set(calls), {*range(2, 14), 17})
        detail = self.run_state(run_id)
        self.assertEqual(detail["run"]["status"], "success")
        self.assertEqual(detail["run"]["success_count"], 13)
        self.assertEqual(len(detail["rows"]), 13)
        self.assertTrue(all(item["amount_yuan"] == "100.01" for item in detail["rows"]))
        with self.connect() as conn:
            self.assertEqual(
                conn.execute("SELECT sum(quota) AS n FROM users").fetchone()["n"], 0
            )

    def test_failure_or_uncertain_result_stops_future_waves_and_preserves_success(self):
        for error, status in [
            (quota.QuotaError("rejected"), "partial"),
            (quota.QuotaRequestUncertain("timeout"), "unknown"),
        ]:
            with self.subTest(status=status):
                # A new due occurrence is explicit test setup, not retry logic.
                run_id = self.queued()

                def mutate(admin, user_id, operation, units):
                    if user_id == 3:
                        raise error

                with patch.object(quota, "_call_manage", side_effect=mutate) as call:
                    executor.execute(run_id, lambda: None)
                    self.assertEqual(call.call_count, 5)
                detail = self.run_state(run_id)
                self.assertEqual(detail["run"]["status"], status)
                self.assertEqual(detail["run"]["success_count"], 4)
                self.assertEqual(detail["run"]["total_count"], 5)
                self.assertEqual(len(detail["rows"]), 5)
                self.assertNotIn("skipped_count", detail["run"])
                self.assert_plans_empty(run_id)
                with patch.object(quota, "_call_manage") as call:
                    executor.execute(run_id, lambda: None)
                    call.assert_not_called()

    def test_disabled_executor_or_missing_pat_fails_without_mutation(self):
        run_id = self.queued()
        with self.connect() as conn:
            conn.execute("UPDATE users SET status=2 WHERE id=1")
        with patch.object(quota, "_call_manage") as call:
            executor.execute(run_id, lambda: None)
            call.assert_not_called()
        self.assert_no_record(run_id)

    def test_admin_pat_is_reread_between_waves_and_not_saved_in_history(self):
        run_id = self.queued()
        tokens = []

        def mutate(admin, user_id, operation, units):
            tokens.append(admin["access_token"])
            if user_id == 6:
                with self.connect() as conn:
                    conn.execute(
                        "UPDATE users SET access_token='fixture-rotated-pat' WHERE id=1"
                    )

        with patch.object(quota, "_call_manage", side_effect=mutate):
            executor.execute(run_id, lambda: None)
        self.assertEqual(tokens[:5], ["fixture-pat"] * 5)
        self.assertTrue(all(value == "fixture-rotated-pat" for value in tokens[5:]))
        self.assertNotIn("fixture-rotated-pat", str(self.run_state(run_id)))

    def test_rule_edits_do_not_change_existing_run_snapshot(self):
        run_id = self.queued()
        schedules.save_rule(
            "admin", {**self.body, "amount_yuan": "200", "version": 1}, 1, self.now
        )
        with patch.object(quota, "_call_manage") as call:
            executor.execute(run_id, lambda: None)
        self.assertTrue(all(c.args[3] == 50005000 for c in call.call_args_list))
        self.assertEqual(schedules.list_rules("admin")["rows"][0]["amount_yuan"], "200")

    def test_pause_before_execution_and_permission_preflight_fail_closed(self):
        run_id = self.queued()
        schedules.set_enabled("admin", 1, {"enabled": False, "version": 1})
        with patch.object(quota, "_call_manage") as call:
            executor.execute(run_id, lambda: None)
            call.assert_not_called()
        self.assert_no_record(run_id)
        with self.connect() as conn:
            conn.execute(
                "UPDATE quota_schedule_rules SET enabled=true,next_run_at=%s WHERE id=1",
                (self.now,),
            )
            conn.execute("UPDATE users SET role=10 WHERE id=1")
            conn.execute("UPDATE users SET role=10 WHERE id=2")
        run_id = schedules.claim_due(self.now)[0]
        with patch.object(quota, "_call_manage") as call:
            executor.execute(run_id, lambda: None)
            call.assert_not_called()
        self.assert_no_record(run_id)

    def test_interrupted_sending_is_unknown_and_pending_is_never_resumed(self):
        run_id = self.queued()
        with self.connect() as conn:
            conn.execute(
                "UPDATE quota_schedule_runs SET status='running' WHERE id=%s", (run_id,)
            )
            conn.execute(
                """INSERT INTO quota_schedule_run_items(run_id,user_id,username,group_name,operation,amount_units,status)
                VALUES (%s,2,'sent','team-a','add',500000,'sending')""",
                (run_id,),
            )
            conn.execute(
                """INSERT INTO quota_schedule_run_targets(run_id,user_id,username,group_name,operation,amount_units)
                VALUES (%s,3,'pending','team-a','add',500000)""",
                (run_id,),
            )
        schedules.recover_interrupted()
        detail = self.run_state(run_id)
        self.assertEqual(detail["run"]["status"], "unknown")
        self.assertEqual([item["status"] for item in detail["rows"]], ["unknown"])
        self.assert_plans_empty(run_id)
        with patch.object(quota, "_call_manage") as call:
            executor.execute(run_id, lambda: None)
            call.assert_not_called()

    def test_two_session_leader_lock_and_distinct_transaction_locks(self):
        with self.connect() as first, self.connect() as second:
            self.assertTrue(
                first.execute(
                    "SELECT pg_try_advisory_lock(%s) AS ok", (schedules.LEADER_LOCK,)
                ).fetchone()["ok"]
            )
            self.assertFalse(
                second.execute(
                    "SELECT pg_try_advisory_lock(%s) AS ok", (schedules.LEADER_LOCK,)
                ).fetchone()["ok"]
            )
            for lock in (locks.BALANCE_LOCK, locks.NOTIFICATION_LOCK):
                self.assertTrue(
                    second.execute(
                        "SELECT pg_try_advisory_xact_lock(%s) AS ok", (lock,)
                    ).fetchone()["ok"]
                )

    def test_both_notification_channels_send_while_quota_leader_lock_is_held(self):
        with self.connect() as conn:
            conn.execute("DELETE FROM balance_alerts")
            conn.execute("UPDATE balance_settings SET enabled=true WHERE scope_id=1")
            conn.execute(
                """INSERT INTO balance_alerts(scope_id,remaining,threshold,spent,budget)
                VALUES (1,5,10,95,100)"""
            )

        def delivery_connect():
            conn = self.connect()
            # A regression must fail quickly instead of hanging on the session lock.
            conn.execute("SET lock_timeout='250ms'")
            return conn

        configs = {
            "feishu_app": {
                "app_id": "cli_fixture",
                "app_secret": "fixture-secret",
                "receive_id_type": "chat_id",
                "receive_id": "oc_fixture",
            },
            "dingtalk_webhook": {
                "webhook_url": "https://oapi.dingtalk.com/robot/send?access_token=fixture-token",
                "signing_enabled": False,
                "signing_secret": "",
            },
        }
        with (
            patch.dict(
                os.environ,
                {"NOTIFICATION_ENCRYPTION_KEY": Fernet.generate_key().decode()},
            ),
            patch.object(
                notifications, "load_site_name", return_value="Fixture Gateway"
            ),
            self.connect() as leader,
        ):
            leader.autocommit = True
            self.assertTrue(
                leader.execute(
                    "SELECT pg_try_advisory_lock(%s) AS ok", (schedules.LEADER_LOCK,)
                ).fetchone()["ok"]
            )
            for name, config in configs.items():
                with self.subTest(channel=name):
                    notifications.save(
                        {
                            "enabled": True,
                            "channel": name,
                            "version": notifications.snapshot()["version"],
                            **config,
                        },
                        "admin",
                    )
                    version = notifications.snapshot()["version"]
                    with (
                        patch.object(balance, "connect", delivery_connect),
                        patch.object(notifications.CHANNELS[name], "send") as send,
                    ):
                        notifications.deliver(test=True, expected_version=version)
                        notifications.deliver(scope_id=1)
                    self.assertEqual(send.call_count, 2)
                    self.assertIn("余额监控测试", send.call_args_list[0].args[2])
                    self.assertIn("余额不足报警", send.call_args_list[1].args[2])
                    state = notifications.snapshot()
                    self.assertIsNotNone(state["last_success_at"])
                    self.assertIsNone(state["last_error"])
            with self.connect() as other:
                self.assertFalse(
                    other.execute(
                        "SELECT pg_try_advisory_lock(%s) AS ok",
                        (schedules.LEADER_LOCK,),
                    ).fetchone()["ok"]
                )

    def test_worker_executes_once_and_is_woken_via_notify(self):
        self.rule()
        stopped = threading.Event()
        errors = []

        def run():
            try:
                quota_timer.serve(stopped)
            except Exception as exc:
                if not stopped.is_set():
                    errors.append(type(exc).__name__)

        with (
            patch.object(quota_timer, "datetime") as clock,
            patch.object(quota, "_call_manage") as call,
        ):
            clock.now.return_value = self.now
            thread = threading.Thread(target=run, daemon=True)
            thread.start()
            try:
                deadline = time.monotonic() + 5
                finished = False
                while time.monotonic() < deadline:
                    with self.connect() as conn:
                        row = conn.execute(
                            "SELECT status FROM quota_schedule_runs ORDER BY id DESC LIMIT 1"
                        ).fetchone()
                    if row and row["status"] == "success":
                        finished = True
                        break
                    time.sleep(0.02)
                self.assertTrue(finished, errors)
                self.assertEqual(call.call_count, 12)
            finally:
                stopped.set()
                with self.connect() as conn:
                    conn.execute("SELECT pg_notify(%s,'')", (schedules.NOTIFY_CHANNEL,))
                thread.join(timeout=3)
            self.assertFalse(thread.is_alive())
            self.assertEqual(errors, [])

    def test_disabling_does_not_require_pat_or_working_group_configuration(self):
        rule_id = self.rule()
        with self.connect() as conn:
            conn.execute("UPDATE users SET access_token='' WHERE id=1")
            conn.execute("UPDATE options SET value='invalid' WHERE key='GroupRatio'")
        schedules.set_enabled("admin", rule_id, {"enabled": False, "version": 1})
        with self.connect() as conn:
            self.assertFalse(
                conn.execute(
                    "SELECT enabled FROM quota_schedule_rules WHERE id=%s", (rule_id,)
                ).fetchone()["enabled"]
            )

    def test_permission_or_pat_changes_mid_run_preserve_successful_wave(self):
        run_id = self.queued()

        def mutate(admin, user_id, operation, units):
            if user_id == 6:
                with self.connect() as conn:
                    conn.execute("UPDATE users SET access_token='' WHERE id=1")

        with patch.object(quota, "_call_manage", side_effect=mutate) as call:
            executor.execute(run_id, lambda: None)
            self.assertEqual(call.call_count, 5)
        detail = self.run_state(run_id)
        self.assertEqual(detail["run"]["status"], "partial")
        self.assertEqual(detail["run"]["success_count"], 5)
        self.assertEqual(detail["run"]["total_count"], 5)
        self.assert_plans_empty(run_id)

    def test_disabled_or_moved_user_is_skipped_at_wave_recheck(self):
        run_id = self.queued()

        def mutate(admin, user_id, operation, units):
            if user_id == 6:
                with self.connect() as conn:
                    conn.execute("UPDATE users SET status=2 WHERE id=7")

        with patch.object(quota, "_call_manage", side_effect=mutate) as call:
            executor.execute(run_id, lambda: None)
        self.assertNotIn(7, [c.args[1] for c in call.call_args_list])
        self.assertEqual(call.call_count, 9)
        detail = self.run_state(run_id)
        self.assertNotIn(7, [item["user_id"] for item in detail["rows"]])
        self.assertEqual(detail["run"]["status"], "partial")
        self.assertEqual(detail["run"]["total_count"], 9)
        self.assert_plans_empty(run_id)

    def test_queued_and_preparation_interruptions_are_not_reported_as_success(self):
        first = self.queued()
        second = self.queued()
        with self.connect() as conn:
            conn.execute(
                "UPDATE quota_schedule_runs SET status='running' WHERE id=%s", (second,)
            )
        schedules.recover_interrupted()
        self.assert_no_record(first)
        self.assert_no_record(second)

    def assert_plans_empty(self, run_id):
        with self.connect() as conn:
            self.assertEqual(
                conn.execute(
                    "SELECT count(*) AS n FROM quota_schedule_run_targets WHERE run_id=%s",
                    (run_id,),
                ).fetchone()["n"],
                0,
            )

    def assert_no_record(self, run_id):
        with self.connect() as conn:
            self.assertIsNone(
                conn.execute(
                    "SELECT id FROM quota_schedule_runs WHERE id=%s", (run_id,)
                ).fetchone()
            )
        with self.assertRaisesRegex(ValueError, "不存在"):
            self.run_state(run_id)
        self.assert_plans_empty(run_id)

    def test_no_eligible_users_produces_no_operation_record(self):
        run_id = self.queued()
        with self.connect() as conn:
            conn.execute("UPDATE users SET status=2 WHERE role=1")
        with patch.object(quota, "_call_manage") as call:
            executor.execute(run_id, lambda: None)
            call.assert_not_called()
        self.assert_no_record(run_id)

    def test_plan_is_not_a_record_and_inflight_count_is_uncertain(self):
        from new_api_statistics import operation_records

        run_id = self.queued()
        self.assertFalse(
            [
                r
                for r in operation_records.list_records("admin", {"kind": "schedule"})[
                    "rows"
                ]
                if r["source"] == "schedule"
            ]
        )
        barrier = threading.Barrier(5)

        def mutate(admin, user_id, operation, units):
            if user_id <= 6:
                barrier.wait(timeout=5)
                rows = operation_records.list_records("admin", {"kind": "schedule"})[
                    "rows"
                ]
                record = next(
                    row for row in rows if row["action"] == "schedule.execute"
                )
                self.assertEqual(record["counts"]["uncertain"], 5)
                self.assertEqual(record["counts"]["total"], 5)
                self.assertNotIn("pending", record["counts"])
                barrier.wait(timeout=5)

        with patch.object(quota, "_call_manage", side_effect=mutate):
            executor.execute(run_id, lambda: None)
        self.assertEqual(self.run_state(run_id)["run"]["success_count"], 12)


if __name__ == "__main__":
    unittest.main()
