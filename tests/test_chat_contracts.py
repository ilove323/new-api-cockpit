"""Offline request contracts: client bodies never choose their owner."""

import unittest

from new_api_cockpit.platform.identity import Principal, RequestAccess
from new_api_cockpit.features.chat import schemas
from new_api_cockpit.features.chat.errors import ChatError


def request_body(**changes):
    return {
        "client_request_id": "44444444-4444-4444-8444-444444444444",
        "expected_revision": 1,
        "token_id": 7,
        "model": "test-chat",
        "capability_revision": "sha256:" + "a" * 64,
        "context_revision": 0,
        "skill_ids": [],
        "mode": "chat",
        "text": "你好",
        "attachment_ids": [],
        **changes,
    }


class ContractsTest(unittest.TestCase):
    def test_valid_request_and_fixed_owner(self):
        self.assertEqual(schemas.parse_run(request_body())["text"], "你好")
        for field in (
            "user_id",
            "owner_id",
            "path",
            "base_url",
            "system_prompt",
            "key",
        ):
            with self.subTest(field=field), self.assertRaises(ChatError):
                schemas.parse_run(request_body(**{field: "forged"}))

    def test_invalid_types_ids_and_limits(self):
        for update in (
            {"token_id": True},
            {"expected_revision": 0},
            {"context_revision": -1},
            {"client_request_id": "../user"},
            {"text": " "},
            {"text": "a" * 32769},
            {"mode": "admin"},
            {"skill_ids": ["44444444-4444-4444-8444-444444444444"] * 2},
            {"attachment_ids": ["44444444-4444-4444-8444-444444444444"] * 2},
            {"capability_revision": "unknown"},
        ):
            with self.subTest(update=list(update)), self.assertRaises(ChatError):
                schemas.parse_run(request_body(**update))

    def test_ordinary_chat_accepts_attachment_ids_without_tools(self):
        ids = ["44444444-4444-4444-8444-444444444444"]
        self.assertEqual(
            schemas.parse_run(request_body(attachment_ids=ids))["attachment_ids"], ids
        )
        with self.assertRaises(ChatError):
            schemas.parse_run(
                request_body(attachment_ids=ids, attachment_texts={ids[0]: "forged"})
            )

    def test_credentials_are_not_in_repr(self):
        for uid, role in ((11, 1), (12, 1), (99, 100)):
            principal = Principal(uid, role=role)
            access = RequestAccess(principal, "private-test-credential")
            self.assertNotIn("private-test-credential", repr(access))
            self.assertEqual(principal.as_user()["id"], uid)
        with self.assertRaises(ValueError):
            Principal(True)


if __name__ == "__main__":
    unittest.main()
