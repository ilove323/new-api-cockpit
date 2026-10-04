"""No test in this module makes a real quota mutation."""

import json
import threading
import unittest
from unittest.mock import patch

from new_api_statistics import quota
from new_api_statistics.app import app


class FakeConn:
    def __init__(self, rows):
        self.rows = rows
        self.sql = []

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def execute(self, query, params=None):
        self.sql.append((query, params))
        self.current = query
        return self

    def fetchone(self):
        return self.rows["operator"]

    def fetchall(self):
        return self.rows["targets"]


class QuotaTest(unittest.TestCase):
    def setUp(self):
        for name in ("begin_quota", "start_quota_wave", "finish_quota"):
            mock = patch.object(quota.operation_records, name)
            mock.start()
            self.addCleanup(mock.stop)
        self.operator = dict(
            id=1, username="admin", role=100, status=1, access_token="secret-pat"
        )
        self.targets = [
            dict(id=2, username="alice", role=1, quota=580000),
            dict(id=3, username="bob", role=1, quota=1160000),
        ]
        self.body = dict(user_ids=[2, 3], mode="add", amount_yuan="380")

    def test_validate_only_increment_or_decrement_and_exact_units(self):
        self.assertEqual(quota.validate_request(self.body)[2], 190000000)
        for change in (
            dict(mode="override"),
            dict(amount_yuan="0"),
            dict(amount_yuan="0.000001"),
            dict(user_ids=[2, 2]),
            dict(user_ids=[True]),
            dict(user_ids=[]),
        ):
            with self.subTest(change=change), self.assertRaises(quota.QuotaError):
                quota.validate_request({**self.body, **change})

    def test_more_than_100_users_can_be_validated_and_previewed(self):
        ids = list(range(2, 203))
        targets = [dict(id=i, username=f"user-{i}", role=1, quota=500000) for i in ids]
        conn = FakeConn({"operator": self.operator, "targets": targets})
        body = {**self.body, "user_ids": ids}
        self.assertEqual(quota.validate_request(body)[0], ids)
        with patch.object(quota, "connect", return_value=conn):
            self.assertEqual(len(quota.preview("admin", body)["users"]), 201)
        with (
            patch.object(quota, "connect", return_value=conn),
            patch.object(quota, "_call_manage") as call,
        ):
            result = quota.apply("admin", body)
        self.assertTrue(result["completed"])
        self.assertEqual(call.call_count, 201)
        self.assertEqual(len(result["results"]), 201)

    def test_preview_is_read_only_and_does_not_overwrite(self):
        conn = FakeConn({"operator": self.operator, "targets": self.targets})
        with patch.object(quota, "connect", return_value=conn):
            result = quota.preview("admin", self.body)
        self.assertEqual(result["users"][0]["before_yuan"], "1.16")
        self.assertEqual(result["users"][0]["estimated_after_yuan"], "381.16")
        self.assertTrue(
            all("SELECT" in query and "UPDATE" not in query for query, _ in conn.sql)
        )

    def test_user_list_includes_new_api_group(self):
        conn = FakeConn(
            {
                "targets": [
                    dict(
                        id=2,
                        username="alice",
                        display_name="Alice",
                        user_group="team-a",
                        role=1,
                        status=1,
                        quota=580000,
                        used_quota=500000,
                    ),
                ]
            }
        )
        with patch.object(quota, "connect", return_value=conn):
            rows = quota.list_users()
        self.assertEqual(rows[0]["user_group"], "team-a")
        self.assertIn("COALESCE(\"group\",'') AS user_group", conn.sql[0][0])

    def test_apply_calls_new_api_atomic_add_and_stops_after_failure(self):
        conn = FakeConn({"operator": self.operator, "targets": self.targets})

        def mutate(_operator, user_id, _mode, _units):
            if user_id == 3:
                raise quota.QuotaError("失败")

        with (
            patch.object(quota, "connect", return_value=conn),
            patch.object(quota, "_call_manage", side_effect=mutate) as call,
        ):
            result = quota.apply("admin", self.body)
        self.assertFalse(result["completed"])
        self.assertEqual([row["ok"] for row in result["results"]], [True, False])
        self.assertEqual(call.call_count, 2)
        self.assertEqual(call.call_args_list[0].args[2:], ("add", 190000000))
        self.assertTrue(all("UPDATE" not in query for query, _ in conn.sql))

    def test_apply_sends_five_concurrently_and_waits_before_next_wave(self):
        ids = list(range(2, 13))
        targets = [dict(id=i, username=str(i), role=1, quota=1) for i in ids]
        conn = FakeConn({"operator": self.operator, "targets": targets})
        lock = threading.Lock()
        barrier = threading.Barrier(5)
        active = maximum = 0
        finished = []

        def mutate(_operator, user_id, _mode, _units):
            nonlocal active, maximum
            with lock:
                if user_id >= 7:
                    self.assertTrue(set(range(2, 7)).issubset(finished))
                if user_id == 12:
                    self.assertTrue(set(range(2, 12)).issubset(finished))
                active += 1
                maximum = max(maximum, active)
            if user_id != 12:
                barrier.wait(timeout=3)
            with lock:
                active -= 1
                finished.append(user_id)

        with (
            patch.object(quota, "connect", return_value=conn),
            patch.object(quota, "_call_manage", side_effect=mutate) as call,
        ):
            result = quota.apply("admin", {**self.body, "user_ids": ids})
        self.assertTrue(result["completed"])
        self.assertEqual(maximum, 5)
        self.assertEqual(call.call_count, 11)
        self.assertEqual([r["id"] for r in result["results"]], ids)

    def test_failed_wave_reports_all_five_and_never_starts_next_wave(self):
        ids = list(range(2, 13))
        targets = [dict(id=i, username=str(i), role=1, quota=1) for i in ids]
        conn = FakeConn({"operator": self.operator, "targets": targets})

        def mutate(_operator, user_id, _mode, _units):
            if user_id == 3:
                raise quota.QuotaError("失败")

        with (
            patch.object(quota, "connect", return_value=conn),
            patch.object(quota, "_call_manage", side_effect=mutate) as call,
        ):
            result = quota.apply("admin", {**self.body, "user_ids": ids})
        self.assertFalse(result["completed"])
        self.assertEqual(call.call_count, 5)
        self.assertEqual([r["id"] for r in result["results"]], ids[:5])
        self.assertEqual(result["remaining_user_ids"], ids[5:])
        self.assertEqual(sum(r["ok"] for r in result["results"]), 4)

    def test_manage_http_request_never_uses_override(self):
        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *_):
                pass

            def read(self):
                return b'{"success":true}'

        with patch.object(
            quota.urllib.request, "urlopen", return_value=Response()
        ) as urlopen:
            quota._call_manage(self.operator, 2, "subtract", 500000)
        request = urlopen.call_args.args[0]
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(
            json.loads(request.data),
            {"id": 2, "action": "add_quota", "mode": "subtract", "value": 500000},
        )
        self.assertEqual(request.get_header("Authorization"), "Bearer secret-pat")

    def test_page_auth_and_mutation_header(self):
        client = app.test_client()
        self.assertEqual(client.get("/cockpit/users/").status_code, 401)
        with (
            patch("new_api_statistics.app.verify_admin", return_value=True),
            patch("new_api_statistics.app.load_site_name", return_value="Test"),
            patch.object(quota, "preview", return_value={"users": []}) as preview,
        ):
            self.assertEqual(
                client.get("/cockpit/users/", auth=("admin", "password")).status_code,
                200,
            )
            self.assertIn(
                "用户组",
                client.get("/cockpit/users/", auth=("admin", "password")).get_data(
                    as_text=True
                ),
            )
            self.assertEqual(
                client.post(
                    "/cockpit/users/api/preview",
                    json=self.body,
                    auth=("admin", "password"),
                ).status_code,
                403,
            )
            response = client.post(
                "/cockpit/users/api/preview",
                json=self.body,
                headers={"X-Quota-Action": "preview"},
                auth=("admin", "password"),
            )
            self.assertEqual(response.status_code, 200)
            preview.assert_called_once()


if __name__ == "__main__":
    unittest.main()
