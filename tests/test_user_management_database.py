"""Disposable PostgreSQL fixture only; all New API mutation calls are mocked."""

from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import threading
import unittest
import uuid
from unittest.mock import patch

import psycopg
from psycopg import sql
from psycopg.rows import dict_row

from new_api_cockpit import (
    balance,
    quota,
    operation_records,
    user_management as manage,
)

DSN = os.environ.get("TEST_SCHEDULE_DATABASE_URL")


@unittest.skipUnless(DSN, "Requires an isolated PostgreSQL fixture database")
class UserManagementDatabaseTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.schema = "management_fixture_" + uuid.uuid4().hex
        cls.source_schema = "management_source_" + uuid.uuid4().hex
        cls.role = "management_reader_" + uuid.uuid4().hex[:12]
        cls.sql_function = (
            Path(__file__).resolve().parents[1] / "sql/source_pat_function.sql"
        ).read_text()
        # public.users is required by the deliberately fully-qualified function.
        # This suite is run only by the disposable local-Postgres harness.
        with psycopg.connect(DSN) as conn:
            conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(cls.schema)))
            conn.execute("""CREATE TABLE public.users(id bigint PRIMARY KEY,username text UNIQUE,display_name text,
                "group" text,role integer,status integer,quota bigint DEFAULT 0,used_quota bigint DEFAULT 0,
                remark text DEFAULT '',password text DEFAULT 'fixture-hash',access_token text UNIQUE,
                access_token_created_at bigint,deleted_at timestamptz);
                CREATE TABLE public.tokens(id bigint PRIMARY KEY,user_id bigint,key text UNIQUE,name text,status integer,
                created_time bigint DEFAULT 0,accessed_time bigint DEFAULT 0,expired_time bigint DEFAULT -1,
                remain_quota bigint DEFAULT 0,used_quota bigint DEFAULT 0,unlimited_quota boolean DEFAULT false,
                model_limits_enabled boolean DEFAULT false,model_limits text DEFAULT '',allow_ips text DEFAULT '',
                "group" text,cross_group_retry boolean DEFAULT false,auto_groups text DEFAULT '',deleted_at timestamptz);
                CREATE TABLE public.options(key text PRIMARY KEY,value text);""")
            conn.execute(cls.sql_function)
            conn.execute(sql.SQL("CREATE ROLE {}").format(sql.Identifier(cls.role)))
            conn.execute(
                sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(
                    sql.Identifier(cls.role)
                )
            )
            conn.execute(
                sql.SQL(
                    "GRANT SELECT ON public.users,public.tokens,public.options TO {}"
                ).format(sql.Identifier(cls.role))
            )
            conn.execute(
                sql.SQL(
                    "GRANT EXECUTE ON FUNCTION public.statistics_ensure_user_pat(bigint,bigint,text) TO {}"
                ).format(sql.Identifier(cls.role))
            )

    @classmethod
    def tearDownClass(cls):
        with psycopg.connect(DSN) as conn:
            conn.execute(
                "DROP FUNCTION public.statistics_ensure_user_pat(bigint,bigint,text)"
            )
            conn.execute("DROP TABLE public.tokens,public.users,public.options")
            conn.execute(
                sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(cls.schema))
            )
            conn.execute(sql.SQL("DROP OWNED BY {}").format(sql.Identifier(cls.role)))
            conn.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(cls.role)))

    def source(self):
        return psycopg.connect(
            DSN,
            row_factory=dict_row,
            options="-c search_path=public -c default_transaction_read_only=on",
        )

    def monitor(self):
        return psycopg.connect(
            DSN, row_factory=dict_row, options="-c search_path=" + self.schema
        )

    def setUp(self):
        for mock in [
            patch.object(quota, "connect", self.source),
            patch.object(balance, "connect", self.monitor),
            patch.object(balance, "configured", return_value=True),
        ]:
            mock.start()
            self.addCleanup(mock.stop)
        balance.initialize()
        with self.monitor() as c:
            c.execute(
                "TRUNCATE quota_schedule_run_items,quota_schedule_runs,quota_schedule_rule_groups,quota_schedule_rules CASCADE"
            )
            c.execute(
                "TRUNCATE user_management_operation_items,user_management_operations CASCADE"
            )
        with psycopg.connect(DSN) as c:
            c.execute("TRUNCATE public.users,public.tokens,public.options")
            c.execute("""INSERT INTO users(id,username,display_name,"group",role,status,access_token)
                VALUES(1,'admin','Admin','admins',100,1,'fixture-root-pat'),(2,'alice','Alice','a',1,1,NULL),
                (3,'manager','','admins',10,1,'fixture-manager-pat'),(4,'disabled','','a',1,2,NULL);
                INSERT INTO options VALUES('GroupRatio','{"a":1,"b":2,"admins":1}'),('UserUsableGroups','{"a":"a","b":"b"}');
                INSERT INTO tokens(id,user_id,key,name,status,remain_quota,"group")
                VALUES(10,2,'fixture-secret-one','ask_copy',1,1000000,'a'),(11,2,'fixture-secret-two','other',1,1000000,'a'),
                (12,4,'fixture-disabled-key','disabled-token',1,1000000,'a');""")
        self.operator = manage.actor("admin")

    def test_sql_query_masks_keys_statuses_and_search_are_literal(self):
        result = manage.list_grouped("admin", {})
        self.assertEqual(result["total_keys"], 2)
        self.assertNotIn("key", result["rows"][0])
        self.assertNotIn("fixture-secret-two", str(result))
        self.assertEqual(manage.list_grouped("admin", {"search": "sk"})["total"], 1)
        self.assertEqual(manage.list_grouped("admin", {"search": "%"})["total"], 0)
        self.assertEqual(
            manage.list_grouped("admin", {"user_statuses": []})["total"], 0
        )
        self.assertEqual(
            manage.list_grouped("admin", {"user_statuses": [2]})["total"], 1
        )

    def test_quick_group_options_are_permission_checked_without_pat_or_audit_writes(
        self,
    ):
        with (
            patch.object(manage, "call_api") as upstream,
            patch.object(manage, "owner_pat") as pat,
        ):
            result = manage.token_group_options("admin", 10)
            self.assertEqual(
                result,
                {
                    "id": 10,
                    "group": "a",
                    "user_status": 1,
                    "available_groups": ["a", "b"],
                },
            )
            self.assertEqual(manage.token_group_options("admin", 12)["user_status"], 2)
            with self.assertRaises(manage.ManagementError):
                manage.token_group_options("admin", 9999)
            upstream.assert_not_called()
            pat.assert_not_called()
        with self.source() as conn:
            self.assertIsNone(
                conn.execute("SELECT access_token FROM users WHERE id=2").fetchone()[
                    "access_token"
                ]
            )
        with self.monitor() as conn:
            self.assertEqual(
                conn.execute(
                    "SELECT count(*) AS n FROM user_management_operations"
                ).fetchone()["n"],
                0,
            )
        with psycopg.connect(DSN) as conn:
            conn.execute(
                "INSERT INTO tokens(id,user_id,key,name,status,\"group\") VALUES(20,1,'fixture-admin-key','admin-key',1,'admins')"
            )
        with self.assertRaises(manage.Forbidden):
            manage.token_group_options("manager", 20)

    def test_pat_function_is_narrow_and_concurrent_creation_never_overwrites(self):
        candidates = [manage.generate_pat() for _ in range(10)]

        def ensure(candidate):
            with psycopg.connect(DSN) as c:
                c.execute(
                    sql.SQL("SET LOCAL ROLE {}").format(sql.Identifier(self.role))
                )
                return c.execute(
                    "SELECT public.statistics_ensure_user_pat(%s,%s,%s)",
                    (1, 2, candidate),
                ).fetchone()[0]

        with ThreadPoolExecutor(max_workers=5) as p:
            results = list(p.map(ensure, candidates))
        self.assertEqual(len(set(results)), 1)
        self.assertIn(results[0], candidates)
        with self.source() as c:
            row = c.execute("SELECT * FROM users WHERE id=2").fetchone()
            self.assertIsNotNone(row["access_token_created_at"])
            self.assertEqual(row["password"], "fixture-hash")
            self.assertEqual(row["quota"], 0)
        with psycopg.connect(DSN) as c:
            c.execute(sql.SQL("SET LOCAL ROLE {}").format(sql.Identifier(self.role)))
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                c.execute("UPDATE tokens SET name='forbidden' WHERE id=10")
        for operator, target in [(2, 2), (3, 1), (1, 4)]:
            with psycopg.connect(DSN) as c:
                with self.assertRaises(psycopg.Error):
                    c.execute(
                        "SELECT public.statistics_ensure_user_pat(%s,%s,%s)",
                        (operator, target, manage.generate_pat()),
                    )

    def test_owner_pat_missing_created_then_latest_rotation_read(self):
        pat = manage.owner_pat(self.operator, 2)
        self.assertTrue(pat)
        with psycopg.connect(DSN) as c:
            c.execute("UPDATE users SET access_token='fixture-rotated' WHERE id=2")
        self.assertEqual(manage.owner_pat(self.operator, 2), "fixture-rotated")
        with self.assertRaises(manage.ManagementError):
            manage.owner_pat(self.operator, 4)

    def test_pat_records_show_target_identity_and_preserve_legacy_user_ids(self):
        pat = manage.owner_pat(self.operator, 2)
        record = operation_records.list_records("admin", {"kind": "user"})["rows"][0]
        self.assertEqual(record["operator_name"], "admin")
        self.assertEqual(record["target_users"], [{"id": 2, "username": "alice"}])
        self.assertEqual(record["target_user_count"], 1)
        self.assertNotIn(pat, str(record))
        with psycopg.connect(DSN) as conn:
            conn.execute("UPDATE users SET username='alice-renamed' WHERE id=2")
        renamed = operation_records.list_records("admin", {})["rows"][0]
        self.assertEqual(renamed["target_users"][0]["username"], "alice")
        details = operation_records.detail("admin", "management", record["id"])
        self.assertEqual(details["rows"][0]["after_data"]["username"], "alice")
        self.assertEqual(
            details["rows"][0]["target_user"], {"id": 2, "username": "alice"}
        )

        # Existing rows have only an item user_id and the PAT-created flag.
        with self.monitor() as conn:
            conn.execute(
                "UPDATE user_management_operations SET parameters='{}' WHERE id=%s",
                (record["id"],),
            )
            conn.execute(
                "UPDATE user_management_operation_items SET after_data='{\"pat_created\":true}' WHERE operation_id=%s",
                (record["id"],),
            )
        with patch.object(manage, "owner_pat") as ensure:
            legacy = operation_records.list_records("admin", {})["rows"][0]
            self.assertEqual(
                legacy["target_users"], [{"id": 2, "username": "alice-renamed"}]
            )
            with psycopg.connect(DSN) as conn:
                conn.execute("DELETE FROM users WHERE id=2")
            deleted = operation_records.list_records("admin", {})["rows"][0]
            self.assertEqual(deleted["target_users"], [{"id": 2, "username": ""}])
            ensure.assert_not_called()
        with self.monitor() as conn:
            self.assertEqual(
                conn.execute(
                    "SELECT parameters FROM user_management_operations WHERE id=%s",
                    (record["id"],),
                ).fetchone()["parameters"],
                {},
            )

    def test_all_single_actions_snapshot_targets_without_substituting_the_actor(self):
        actions = {
            "user": ["edit", "password", "enable", "disable", "delete"],
            "token": [
                "edit",
                "quota",
                "enable",
                "disable",
                "delete",
                "reveal",
                "group",
            ],
        }
        with patch.object(manage, "_perform", return_value=None):
            for kind, kinds in actions.items():
                for action in kinds:
                    manage.single_action(
                        "admin", kind, 2 if kind == "user" else 10, {"action": action}
                    )
            manage.single_action("admin", "token", 2, {"action": "create"})
            new_user = manage.single_action(
                "admin",
                "user",
                0,
                {
                    "action": "create",
                    "changes": {"username": "new-user", "password": "fixture-password"},
                },
            )
        with patch.object(manage, "_perform", side_effect=manage.Uncertain("需核对")):
            with self.assertRaises(manage.Uncertain):
                manage.single_action("admin", "token", 11, {"action": "group"})
        with patch.object(quota, "_call_manage", return_value=None):
            for mode in ("add", "subtract"):
                quota.apply(
                    "admin", {"user_ids": [2], "mode": mode, "amount_yuan": "1"}
                )
        with psycopg.connect(DSN) as conn:
            conn.execute("DELETE FROM users WHERE id=2")
        headers = operation_records.list_records("admin", {})["rows"]
        self.assertEqual(len(headers), 17)
        for header in headers:
            with self.subTest(action=header["action"]):
                identity = (
                    {"id": None, "username": "new-user"}
                    if header["action"] == "user.create"
                    else {"id": 2, "username": "alice"}
                )
                self.assertEqual(header["target_users"], [identity])
                self.assertEqual(header["target_user_count"], 1)
                self.assertEqual(header["operator_name"], "admin")
                item = operation_records.detail("admin", "management", header["id"])[
                    "rows"
                ][0]
                self.assertEqual(item["target_user"], identity)
                self.assertEqual(item["user_id"], identity["id"])
                self.assertNotIn("fixture-password", str(item))
        with self.monitor() as conn:
            created = conn.execute(
                "SELECT user_id FROM user_management_operation_items WHERE operation_id=%s",
                (new_user["operation_id"],),
            ).fetchone()
            self.assertEqual(created["user_id"], 0)
            # The previous format used the administrator as a placeholder ID.
            # Reading it must not turn the administrator into the target user.
            conn.execute(
                "UPDATE user_management_operation_items SET user_id=1,after_data=after_data-'target_user' WHERE operation_id=%s",
                (new_user["operation_id"],),
            )
        legacy = next(
            r
            for r in operation_records.list_records("admin", {})["rows"]
            if r["id"] == new_user["operation_id"]
        )
        self.assertEqual(legacy["target_users"], [{"id": None, "username": "new-user"}])
        self.assertIsNone(
            operation_records.detail("admin", "management", legacy["id"])["rows"][0][
                "user_id"
            ]
        )

    def test_batch_targets_deduplicate_users_and_exclude_unsent_owners(self):
        with psycopg.connect(DSN) as conn:
            for user_id in (5, 6, 7):
                conn.execute(
                    "INSERT INTO users(id,username,role,status,\"group\") VALUES(%s,%s,1,1,'a')",
                    (user_id, f"user-{user_id}"),
                )
            for token_id, owner in ((20, 3), (21, 5), (22, 6), (23, 7)):
                conn.execute(
                    "INSERT INTO tokens(id,user_id,key,name,status,\"group\") VALUES(%s,%s,%s,'fixture',1,'a')",
                    (token_id, owner, f"fixture-batch-{token_id}"),
                )
        plan = manage.preview_group(
            "admin", {"target_group": "b", "token_ids": [10, 11, 20, 21, 22, 23]}
        )
        with patch.object(manage, "_perform", return_value=None):
            manage.apply_wave("admin", plan["operation_id"])
        with psycopg.connect(DSN) as conn:
            conn.execute("UPDATE users SET username='renamed' WHERE id=2")
        header = operation_records.list_records("admin", {})["rows"][0]
        self.assertEqual(header["target_user_count"], 4)
        self.assertEqual(
            header["target_users"],
            [
                {"id": 2, "username": "alice"},
                {"id": 3, "username": "manager"},
                {"id": 5, "username": "user-5"},
            ],
        )
        self.assertEqual(header["counts"]["total"], 5)
        items = operation_records.detail("admin", "management", plan["operation_id"])[
            "rows"
        ]
        self.assertEqual([r["target_user"]["id"] for r in items], [2, 2, 3, 5, 6])
        self.assertEqual(items[0]["target_user"]["username"], "alice")
        with self.monitor() as conn:
            self.assertEqual(
                conn.execute(
                    "SELECT user_id FROM user_management_operation_targets WHERE operation_id=%s",
                    (plan["operation_id"],),
                ).fetchone()["user_id"],
                7,
            )

    def test_legacy_key_targets_use_one_name_lookup_and_keep_deleted_user_ids(self):
        with patch.object(manage, "_perform", return_value=None):
            for action in ("edit", "quota", "enable", "disable", "delete", "group"):
                manage.single_action("admin", "token", 10, {"action": action})
        with self.monitor() as conn:
            conn.execute(
                "UPDATE user_management_operation_items SET after_data=after_data-'target_user'"
            )
        with (
            patch.object(quota, "connect", wraps=self.source) as source,
            patch.object(manage, "owner_pat") as pat,
        ):
            headers = operation_records.list_records("admin", {})["rows"]
            self.assertEqual(
                source.call_count, 2
            )  # Actor + one identity query for the whole page.
            for row in headers:
                self.assertEqual(row["target_users"], [{"id": 2, "username": "alice"}])
            pat.assert_not_called()
        with psycopg.connect(DSN) as conn:
            conn.execute("DELETE FROM users WHERE id=2")
        deleted = operation_records.list_records("admin", {})["rows"]
        self.assertEqual(deleted[0]["target_users"], [{"id": 2, "username": ""}])
        item = operation_records.detail("admin", "management", deleted[0]["id"])[
            "rows"
        ][0]
        self.assertEqual(item["target_user"], {"id": 2, "username": ""})
        with self.monitor() as conn:
            self.assertEqual(
                conn.execute(
                    "SELECT count(*) AS n FROM user_management_operation_items WHERE after_data ? 'target_user'"
                ).fetchone()["n"],
                0,
            )

    def test_special_group_empty_and_null_values_keep_normal_group_permissions(self):
        for value in ("", "null", '{"a":null}', '{"a":{}}'):
            with psycopg.connect(DSN) as c:
                c.execute(
                    "INSERT INTO options VALUES('group_ratio_setting.group_special_usable_group',%s) ON CONFLICT(key) DO UPDATE SET value=EXCLUDED.value",
                    (value,),
                )
            self.assertEqual(
                manage.selectable_groups(self.operator, {"group": "a"}), ["a", "b"]
            )

    def test_bulk_frozen_ids_pagination_over_100_and_five_concurrent(self):
        with psycopg.connect(DSN) as c:
            for id in range(20, 225):
                c.execute(
                    "INSERT INTO tokens(id,user_id,key,name,status,remain_quota,\"group\") VALUES(%s,2,%s,'bulk',1,1000000,'a')",
                    (id, "fixture-" + str(id)),
                )
        plan = manage.preview_group(
            "admin",
            {
                "target_group": "b",
                "all_filtered": True,
                "filters": {"token_statuses": [1]},
            },
        )
        self.assertEqual(plan["count"], 207)
        self.assertEqual(len(plan["rows"]), 100)
        active = maximum = 0
        mutex = threading.Lock()
        barrier = threading.Barrier(5)

        def perform(*_):
            nonlocal active, maximum
            with mutex:
                active += 1
                maximum = max(maximum, active)
            barrier.wait(timeout=5)
            with mutex:
                active -= 1

        with patch.object(manage, "_perform", side_effect=perform):
            wave = manage.apply_wave("admin", plan["operation_id"])
        self.assertEqual(len(wave["results"]), 5)
        self.assertEqual(maximum, 5)
        self.assertEqual(wave["remaining"], 202)
        self.assertFalse(wave["done"])
        detail = operation_records.detail("admin", "management", plan["operation_id"])
        self.assertEqual(len(detail["rows"]), 5)

    def test_failed_wave_stops_and_never_retries_unknown(self):
        plan = manage.preview_group(
            "admin", {"target_group": "b", "token_ids": [10, 11]}
        )
        with patch.object(
            manage, "_perform", side_effect=manage.Uncertain("fixture uncertain")
        ) as call:
            result = manage.apply_wave("admin", plan["operation_id"])
            self.assertTrue(result["done"])
            manage.apply_wave("admin", plan["operation_id"])
            self.assertEqual(call.call_count, 2)
        self.assertTrue(
            all(
                r["state"] == "unknown"
                for r in operation_records.detail(
                    "admin", "management", plan["operation_id"]
                )["rows"]
            )
        )

    def test_preview_and_uninitiated_remaining_targets_are_not_operation_records(self):
        from new_api_cockpit import operation_records as records

        with psycopg.connect(DSN) as c:
            c.cursor().executemany(
                """INSERT INTO tokens(id,user_id,key,name,status,remain_quota,"group")
                VALUES(%s,2,%s,'fixture-extra',1,1000000,'a')""",
                [(i, "fixture-key-" + str(i)) for i in range(20, 25)],
            )
        plan = manage.preview_group(
            "admin", {"target_group": "b", "token_ids": [10, 11, *range(20, 25)]}
        )
        self.assertEqual(records.list_records("admin", {})["rows"], [])
        self.assertEqual(operation_records.list_records("admin", {})["rows"], [])
        with self.assertRaises(manage.Forbidden):
            records.detail("admin", "management", plan["operation_id"])
        with self.monitor() as c:
            self.assertEqual(
                c.execute(
                    "SELECT count(*) AS n FROM user_management_operation_items"
                ).fetchone()["n"],
                0,
            )
            self.assertEqual(
                c.execute(
                    "SELECT count(*) AS n FROM user_management_operation_targets"
                ).fetchone()["n"],
                7,
            )

        def perform(operator, kind, object_id, *args):
            if object_id == 10:
                raise manage.Uncertain("fixture uncertainty")

        with patch.object(manage, "_perform", side_effect=perform) as call:
            result = manage.apply_wave("admin", plan["operation_id"])
            self.assertTrue(result["done"])
            manage.apply_wave("admin", plan["operation_id"])
            self.assertEqual(call.call_count, 5)
        header = records.list_records("admin", {})["rows"][0]
        self.assertEqual(
            header["counts"], {"total": 5, "success": 4, "failed": 0, "uncertain": 1}
        )
        self.assertEqual(
            len(records.detail("admin", "management", plan["operation_id"])["rows"]), 5
        )
        with self.monitor() as c:
            self.assertEqual(
                c.execute(
                    "SELECT count(*) AS n FROM user_management_operation_targets"
                ).fetchone()["n"],
                0,
            )

    def test_manual_quota_stops_without_journaling_unsent_waves(self):
        from new_api_cockpit import operation_records as records

        with psycopg.connect(DSN) as c:
            c.cursor().executemany(
                """INSERT INTO users(id,username,role,status,"group") VALUES(%s,%s,1,1,'a')""",
                [(i, "fixture-extra-user-" + str(i)) for i in range(20, 27)],
            )

        def mutate(admin, user_id, *args):
            if user_id == 2:
                raise quota.QuotaRequestUncertain("fixture uncertainty")

        with patch.object(quota, "_call_manage", side_effect=mutate) as call:
            result = quota.apply(
                "admin",
                {"user_ids": [2, 3, *range(20, 27)], "mode": "add", "amount_yuan": "1"},
            )
            self.assertEqual(call.call_count, 5)
        self.assertEqual(len(result["remaining_user_ids"]), 4)
        header = records.list_records("admin", {"kind": "quota"})["rows"][0]
        self.assertEqual(
            header["counts"], {"total": 5, "success": 4, "failed": 0, "uncertain": 1}
        )
        with self.monitor() as c:
            self.assertEqual(
                c.execute(
                    "SELECT count(*) AS n FROM user_management_operation_targets"
                ).fetchone()["n"],
                0,
            )

    def test_new_preview_cleans_expired_internal_plan_without_audit(self):
        plan = manage.preview_group("admin", {"target_group": "b", "token_ids": [10]})
        with self.monitor() as c:
            c.execute(
                "UPDATE user_management_operations SET expires_at=now()-interval '1 second' WHERE id=%s",
                (plan["operation_id"],),
            )
        next_plan = manage.preview_group(
            "admin", {"target_group": "b", "token_ids": [11]}
        )
        with self.monitor() as c:
            self.assertIsNone(
                c.execute(
                    "SELECT id FROM user_management_operations WHERE id=%s",
                    (plan["operation_id"],),
                ).fetchone()
            )
            rows = c.execute(
                "SELECT operation_id FROM user_management_operation_targets"
            ).fetchall()
            self.assertEqual(
                [str(r["operation_id"]) for r in rows], [next_plan["operation_id"]]
            )
        self.assertFalse(operation_records.list_records("admin", {})["rows"])

    def test_admin_scope_expired_preview_and_source_group_permissions(self):
        self.assertEqual(
            manage.list_grouped("manager", {"search": "ask_copy"})["rows"][0]["id"],
            2,
        )
        with self.assertRaises(manage.Forbidden):
            manage.target(manage.actor("manager"), 1)
        with self.assertRaises(manage.ManagementError):
            manage.preview_group(
                "admin", {"target_group": "nonexistent", "token_ids": [10]}
            )
        plan = manage.preview_group("admin", {"target_group": "b", "token_ids": [10]})
        with self.monitor() as c:
            c.execute(
                "UPDATE user_management_operations SET expires_at=now()-interval '1 second' WHERE id=%s",
                (plan["operation_id"],),
            )
        with self.assertRaises(manage.Conflict):
            manage.apply_wave("admin", plan["operation_id"])
        with self.assertRaises(manage.Forbidden):
            manage.apply_wave("manager", plan["operation_id"])

    def test_single_password_and_reveal_never_persist_credentials(self):
        with patch.object(manage, "_perform", return_value=None):
            manage.single_action(
                "admin",
                "user",
                2,
                {
                    "action": "password",
                    "changes": {
                        "password": "fixture-super-secret",
                        "password_confirm": "fixture-super-secret",
                    },
                },
            )
        with patch.object(
            manage, "_perform", return_value={"key": "fixture-complete-key"}
        ):
            result = manage.single_action(
                "admin", "token", 10, {"action": "reveal", "changes": {}}
            )
        self.assertEqual(result["result"]["key"], "fixture-complete-key")
        records = operation_records.list_records("admin", {})["rows"]
        self.assertEqual(len(records), 2)
        for record in records:
            for item in operation_records.detail(
                "admin", "management", str(record["id"])
            )["rows"]:
                self.assertNotIn("fixture-super-secret", str(item))
                self.assertNotIn("fixture-complete-key", str(item))
                self.assertNotIn("access_token", item["before_data"])
                self.assertEqual(item["state"], "success")

    def test_key_only_grouped_list_pages_owners_without_splitting_children(self):
        result = manage.list_grouped("admin", {"page_size": 1})
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["total_keys"], 2)
        self.assertEqual(len(result["rows"]), 1)
        self.assertEqual([t["id"] for t in result["rows"][0]["tokens"]], [11, 10])
        self.assertEqual(
            manage.list_grouped("admin", {"page": 2, "page_size": 1})["rows"],
            [],
        )
        self.assertNotIn("fixture-secret", str(result))
        with self.source() as c:
            self.assertIsNone(
                c.execute("SELECT access_token FROM users WHERE id=2").fetchone()[
                    "access_token"
                ]
            )
        for keyword, count in (("sk", 1), ("%", 0), ("a.*", 0), ("Alice", 2)):
            self.assertEqual(
                manage.list_grouped("admin", {"search": keyword})["total_keys"],
                count,
            )
        self.assertEqual(
            manage.list_grouped("admin", {"token_statuses": []})["total"],
            0,
        )
        disabled = manage.list_grouped("admin", {"user_statuses": [2]})
        self.assertEqual(disabled["rows"][0]["id"], 4)

    def test_grouped_query_has_no_per_owner_100_key_truncation(self):
        with psycopg.connect(DSN) as c:
            c.execute("""INSERT INTO tokens(id,user_id,key,name,status,remain_quota,\"group\")
                SELECT n,2,'fixture-bulk-'||n,'bulk-'||n,1,1000000,'a'
                FROM generate_series(100,229) n""")
        result = manage.list_grouped("manager", {"page_size": 1})
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["total_keys"], 132)
        self.assertEqual(len(result["rows"][0]["tokens"]), 132)
        self.assertEqual(manage.list_grouped("manager", {"user_id": 1})["total"], 0)

    def test_manual_quota_records_before_sending_and_after_results_without_source_writes(
        self,
    ):
        from new_api_cockpit import operation_records as records

        observed = []

        def mutate(_operator, user_id, _mode, _units):
            with self.monitor() as c:
                row = c.execute(
                    """SELECT i.state FROM user_management_operation_items i
                    JOIN user_management_operations o ON o.id=i.operation_id
                    WHERE o.action='quota.add' AND i.target_id=%s""",
                    (user_id,),
                ).fetchone()
                observed.append(row["state"])
            if user_id == 3:
                raise quota.QuotaRequestUncertain("fixture-secret-must-not-be-audited")

        with patch.object(quota, "_call_manage", side_effect=mutate):
            result = quota.apply(
                "admin", {"user_ids": [2, 3], "mode": "add", "amount_yuan": "12.345678"}
            )
        self.assertFalse(result["completed"])
        self.assertEqual(observed, ["sending", "sending"])
        header = records.list_records("admin", {"kind": "quota"})["rows"][0]
        self.assertEqual(header["parameters"]["amount_units"], 6172839)
        self.assertEqual(header["counts"]["success"], 1)
        self.assertEqual(header["counts"]["uncertain"], 1)
        detail = records.detail("admin", "management", result["operation_id"])
        self.assertEqual([i["state"] for i in detail["rows"]], ["success", "unknown"])
        self.assertNotIn("fixture-secret", str(detail))
        self.assertNotIn("access_token", str(detail))
        self.assertEqual(records.list_records("manager", {})["rows"], [])
        with self.assertRaises(manage.Forbidden):
            records.detail("manager", "management", result["operation_id"])
        with self.source() as c:
            self.assertEqual(
                c.execute("SELECT sum(quota) AS n FROM users").fetchone()["n"], 0
            )

    def test_unified_history_pagination_handles_same_timestamp_and_keeps_legacy_runs(
        self,
    ):
        from new_api_cockpit import operation_records as records
        from psycopg.types.json import Jsonb

        with self.monitor() as c:
            rule = c.execute("""INSERT INTO quota_schedule_rules(period,operation,amount_units,
                executor_user_id,executor_username,created_by_user_id)
                VALUES('daily','add',500000,1,'admin',1) RETURNING id""").fetchone()[
                "id"
            ]
            run = c.execute(
                """INSERT INTO quota_schedule_runs(rule_id,scheduled_for,snapshot,status)
                VALUES(%s,'2026-10-01T00:00:00+08:00',%s,'success') RETURNING id""",
                (
                    rule,
                    Jsonb(
                        {
                            "executor_user_id": 1,
                            "executor_username": "admin",
                            "amount_units": 500000,
                            "operation": "add",
                        }
                    ),
                ),
            ).fetchone()["id"]
            c.execute(
                """INSERT INTO quota_schedule_run_items
                (run_id,user_id,username,group_name,operation,amount_units,status)
                VALUES(%s,2,'alice','a','add',500000,'success')""",
                (run,),
            )
            operation_ids = [uuid.uuid4() for _ in range(70)]
            c.cursor().executemany(
                """INSERT INTO user_management_operations
                (id,operator_id,operator_name,action,state,created_at,expires_at)
                VALUES(%s,1,'admin','user.edit','completed','2026-10-01T00:00:00+08:00',now())""",
                [(i,) for i in operation_ids],
            )
            c.cursor().executemany(
                """INSERT INTO user_management_operation_items
                (operation_id,target_type,target_id,user_id,state,started_at,finished_at)
                VALUES(%s,'user',2,2,'success',now(),now())""",
                [(i,) for i in operation_ids],
            )
        first = records.list_records("admin", {})
        second = records.list_records("admin", {"before": first["next_before"]})
        all_rows = first["rows"] + second["rows"]
        self.assertEqual(len(first["rows"]), 50)
        self.assertEqual(len(all_rows), 71)
        self.assertEqual(len({(r["source"], r["id"]) for r in all_rows}), 71)
        self.assertIsNone(second["next_before"])
        self.assertTrue(
            any(r["source"] == "schedule" and r["id"] == str(run) for r in all_rows)
        )
        self.assertEqual(
            len(records.list_records("admin", {"kind": "user"})["rows"]), 50
        )
        self.assertEqual(
            len(records.list_records("admin", {"kind": "schedule"})["rows"]), 1
        )
        for cursor in ("bad", "%%", "x" * 513):
            with self.assertRaises(manage.ManagementError):
                records.list_records("admin", {"before": cursor})

    def test_scheduled_rule_changes_and_audit_commit_together(self):
        from new_api_cockpit import quota_schedule, operation_records as records

        values = {
            "groups": ["a"],
            "period": "daily",
            "operation": "add",
            "amount_yuan": "1.25",
            "enabled": False,
        }
        created = quota_schedule.save_rule("admin", values)
        quota_schedule.set_enabled(
            "admin", created["id"], {"version": 1, "enabled": True}
        )
        quota_schedule.delete_rule("admin", created["id"], {"version": 2})
        actions = [
            r["action"]
            for r in records.list_records("admin", {"kind": "schedule"})["rows"]
        ]
        self.assertIn("schedule.create", actions)
        self.assertIn("schedule.enable", actions)
        self.assertIn("schedule.delete", actions)
        with patch.object(
            records, "record_rule", side_effect=RuntimeError("fixture rollback")
        ):
            with self.assertRaises(RuntimeError):
                quota_schedule.save_rule("admin", values)
        with self.monitor() as c:
            count = c.execute(
                "SELECT count(*) AS n FROM quota_schedule_rules WHERE deleted_at IS NULL"
            ).fetchone()["n"]
            self.assertEqual(count, 0)


if __name__ == "__main__":
    unittest.main()
