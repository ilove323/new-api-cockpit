"""No external calls: strict owner/policy intersections and ephemeral keys."""

import unittest

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


if __name__ == "__main__":
    unittest.main()
