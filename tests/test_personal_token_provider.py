"""No external calls: strict owner/policy intersections and ephemeral keys."""

import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from new_api_cockpit.integrations.new_api.token_provider import TokenProvider
from new_api_cockpit.integrations.new_api.personal_tokens import TokenError
from new_api_cockpit.platform.identity import Principal, RequestAccess


class TokenProviderTest(unittest.TestCase):
    def setUp(self):
        self.access = RequestAccess(Principal(12), "fake-session")
        self.row = dict(
            id=7,
            user_id=12,
            status=1,
            expired_time=-1,
            remain_quota=100,
            unlimited_quota=False,
            group="GPT",
            key="x" * 48,
            model_limits_enabled=True,
            model_limits="allowed",
        )
        self.calls = []

        def upstream(path, credential, uid, **kw):
            self.calls.append(path)
            if path == "/api/token/7":
                return {"data": dict(self.row)}
            if path == "/api/token/7/key":
                return {"data": {"key": "x" * 48}}
            if path == "/v1/models":
                return {
                    "data": [
                        {"id": "allowed", "supported_endpoint_types": ["openai"]},
                        {"id": "denied"},
                    ]
                }
            if path.startswith("/api/token/?"):
                return {"data": {"total": 1, "items": [dict(self.row)]}}
            raise AssertionError(path)

        self.provider = TokenProvider(upstream)

    def test_model_limits_intersection_and_no_key_in_summary(self):
        self.assertEqual(
            self.provider.list_models(self.access, 7),
            [{"id": "allowed", "protocols": ["chat"]}],
        )
        self.assertNotIn("x" * 48, str(self.provider.list_tokens(self.access)))
        with self.provider.lease_key(self.access, 7, group="GPT") as key:
            self.assertEqual(str(key), "sk-" + "x" * 48)
            self.assertNotIn("x" * 48, repr(key))

    def test_owner_status_group_and_malformed_policy_fail_closed(self):
        for update in (
            {"user_id": 99},
            {"status": 2},
            {"group": "OTHER"},
            {"model_limits_enabled": "false"},
        ):
            before = dict(self.row)
            self.row.update(update)
            with self.subTest(update=update), self.assertRaises(TokenError):
                self.provider.list_models(self.access, 7, group="GPT")
            self.row = before

    def test_rechecks_owner_after_key_lookup(self):
        original = self.provider.upstream

        def changed(path, *args, **kwargs):
            result = original(path, *args, **kwargs)
            if path.endswith("/key"):
                self.row["user_id"] = 99
            return result

        self.provider.upstream = changed
        with self.assertRaises(TokenError):
            with self.provider.lease_key(self.access, 7):
                self.fail("must not yield a key after owner changed")

    def test_routing_groups_use_current_token_policy_not_a_named_group(self):
        upstream = self.provider.upstream

        def policy(path, *args, **kwargs):
            if path == "/api/token/auto-groups":
                return {"data": {"groups": ["海外模型", "备用"], "max_count": 2}}
            if path == "/api/user/self/groups":
                return {"data": {"海外模型": {}, "自选": {}, "备用": {}}}
            if path == "/api/user/self":
                return {"data": {"id": 12, "group": "用户默认组"}}
            return upstream(path, *args, **kwargs)

        self.provider.upstream = policy
        for group in ("海外模型", "default", "GPT", "生图专用"):
            self.row["group"] = group
            self.assertEqual(self.provider.routing_groups(self.access, 7), [group])
        self.row["group"] = ""
        self.assertEqual(self.provider.routing_groups(self.access, 7), ["用户默认组"])
        self.row["group"] = "auto"
        self.assertEqual(
            self.provider.routing_groups(self.access, 7), ["海外模型", "备用"]
        )
        self.row["auto_groups"] = ["无权组", "自选", "备用", "海外模型"]
        self.assertEqual(self.provider.routing_groups(self.access, 7), ["自选", "备用"])
        self.row["auto_groups"] = []
        self.assertEqual(self.provider.routing_groups(self.access, 7), [])
        self.assertNotIn("/api/token/7/key", self.calls)

    def test_repeated_catalogues_and_agent_leases_read_the_secret_once(self):
        for _ in range(30):
            self.provider.list_models(self.access, 7)
            with self.provider.lease_key(self.access, 7):
                pass
        self.assertEqual(self.calls.count("/api/token/7/key"), 1)
        self.assertEqual(self.calls.count("/v1/models"), 30)
        self.assertGreaterEqual(self.calls.count("/api/token/7"), 60)
        self.assertNotIn("x" * 48, repr(self.provider._keys))

    def test_warm_cache_still_checks_owner_status_group_and_current_model_limits(self):
        self.provider.list_models(self.access, 7)
        for update in (
            {"user_id": 99},
            {"status": 2},
            {"group": "OTHER"},
            {"expired_time": 1},
            {"remain_quota": 0},
        ):
            before = dict(self.row)
            self.row.update(update)
            with self.subTest(update=update), self.assertRaises(TokenError):
                self.provider.list_models(self.access, 7, group="GPT")
            self.row = before
        self.row["model_limits"] = "denied"
        self.assertEqual(
            self.provider.list_models(self.access, 7),
            [{"id": "denied", "protocols": []}],
        )
        self.assertEqual(self.calls.count("/api/token/7/key"), 1)

    def test_key_reuse_expires_without_sliding_and_is_bounded(self):
        module = "new_api_cockpit.integrations.new_api.token_provider"
        with patch(module + ".time.monotonic", return_value=0):
            self.provider.list_models(self.access, 7)
        with patch(module + ".time.monotonic", return_value=1199):
            self.provider.list_models(self.access, 7)
        self.assertEqual(self.calls.count("/api/token/7/key"), 1)
        with patch(module + ".time.monotonic", return_value=1200):
            self.provider.list_models(self.access, 7)
        self.assertEqual(self.calls.count("/api/token/7/key"), 2)
        with patch(module + ".KEY_CACHE_ENTRIES", 2):
            for credential in ("session-a", "session-b", "session-c"):
                self.provider.list_models(RequestAccess(Principal(12), credential), 7)
        self.assertEqual(len(self.provider._keys), 2)

    def test_credentials_origin_and_key_rotation_cannot_reuse_an_old_entry(self):
        self.provider.list_models(self.access, 7)
        self.provider.list_models(RequestAccess(Principal(12), "new-session"), 7)
        with patch.dict("os.environ", {"NEW_API_INTERNAL_URL": "http://other:3000"}):
            self.provider.list_models(self.access, 7)
        self.row["key"] = "rotated-mask"
        self.provider.list_models(self.access, 7)
        self.assertEqual(self.calls.count("/api/token/7/key"), 4)

    def test_concurrent_calls_single_flight_secret_read(self):
        with ThreadPoolExecutor(max_workers=8) as pool:
            rows = list(
                pool.map(lambda _: self.provider.list_models(self.access, 7), range(24))
            )
        self.assertTrue(all(row == rows[0] for row in rows))
        self.assertEqual(self.calls.count("/api/token/7/key"), 1)

    def test_failed_secret_reads_are_not_cached_or_retried_and_revoked_keys_are_evicted(
        self,
    ):
        upstream = self.provider.upstream

        def limited(path, *args, **kwargs):
            if path.endswith("/key"):
                raise TokenError("rate limited", 429)
            return upstream(path, *args, **kwargs)

        with patch.object(self.provider, "upstream", side_effect=limited) as call:
            with self.assertRaises(TokenError):
                self.provider.list_models(self.access, 7)
            self.assertEqual(
                sum(c.args[0].endswith("/key") for c in call.call_args_list), 1
            )
        self.assertFalse(self.provider._keys)
        self.provider.list_models(self.access, 7)
        with self.assertRaises(TokenError):
            with self.provider.lease_key(self.access, 7):
                raise TokenError("rejected", 403)
        self.assertFalse(self.provider._keys)


if __name__ == "__main__":
    unittest.main()
