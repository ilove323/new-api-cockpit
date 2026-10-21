"""Independent review: real HTTP identity precedence, never real credentials."""

import importlib
import os
import tempfile
import unittest
from unittest.mock import patch

from chat_fixtures import access, create, make_service
from new_api_cockpit.auth import COOKIE_NAME, LoginRequired
from new_api_cockpit.platform.identity import RequestAccess

host = importlib.import_module("new_api_cockpit.app")


class ChatAuthBoundaryReviewTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.service, _, self.stream = make_service(self.tmp.name)
        self.actor_a = RequestAccess(access(12).principal, "synthetic-access-a")
        self.actor_b = RequestAccess(access(13).principal, "synthetic-access-b")
        self.cid_a = create(self.service, self.actor_a, title="private-a")
        self.cid_b = create(self.service, self.actor_b, title="private-b")
        self.client = host.app.test_client()
        self.client.set_cookie(COOKIE_NAME, self.actor_a.credential, path="/cockpit")
        self.enterContext(
            patch.dict(
                os.environ, {"COCKPIT_PROCESS_ROLE": "chat", "CHAT_ENABLED": "true"}
            )
        )
        self.enterContext(
            patch(
                "new_api_cockpit.composition.get_chat_service",
                return_value=self.service,
            )
        )
        self.verify = self.enterContext(
            patch.object(host, "verify_session", side_effect=self.verify_fixture)
        )
        self.pat = self.enterContext(
            patch.object(host, "verify_pat", side_effect=AssertionError("PAT lookup"))
        )

    def tearDown(self):
        self.stream.release.set()
        self.service.shutdown()
        self.service.store.close()
        self.tmp.cleanup()

    def verify_fixture(self, credential, **_):
        for actor in (self.actor_a, self.actor_b):
            if credential == actor.credential:
                return actor.principal.as_user()
        raise LoginRequired("synthetic invalid credential")

    def get(self, suffix="/conversations", *, actor=None, bearer=None):
        actor = actor or self.actor_a
        headers = {"X-Cockpit-Session": actor.principal.session_id}
        if bearer is not None:
            headers["Authorization"] = "Bearer " + bearer
        return self.client.get("/cockpit/api/chat" + suffix, headers=headers)

    def test_explicit_other_session_never_uses_cookie_identity_or_stale_stamp(self):
        mismatch = self.get(bearer=self.actor_b.credential)
        self.assertEqual(mismatch.status_code, 409, mismatch.json)
        self.assertEqual(mismatch.json["code"], "AUTH_SESSION_CHANGED")
        own_b = self.get(actor=self.actor_b, bearer=self.actor_b.credential)
        self.assertEqual(own_b.status_code, 200, own_b.json)
        self.assertIn(self.cid_b, str(own_b.json))
        self.assertNotIn(self.cid_a, str(own_b.json))
        forbidden_a = self.get(
            "/conversations/" + self.cid_a,
            actor=self.actor_b,
            bearer=self.actor_b.credential,
        )
        self.assertEqual(forbidden_a.status_code, 404, forbidden_a.json)
        self.pat.assert_not_called()

    def test_invalid_explicit_bearer_cannot_fall_back_or_erase_valid_cookie(self):
        invalid = self.get(bearer="synthetic-revoked-access")
        self.assertEqual(invalid.status_code, 401, invalid.json)
        self.assertFalse(invalid.headers.getlist("Set-Cookie"))
        cookie = self.get()
        self.assertEqual(cookie.status_code, 200, cookie.json)
        self.assertIn(self.cid_a, str(cookie.json))
        self.assertNotIn(self.cid_b, str(cookie.json))
        self.pat.assert_not_called()

    def test_cookie_identity_never_accepts_other_user_stamp_or_missing_stamp(self):
        missing = self.client.get("/cockpit/api/chat/conversations")
        wrong = self.get(actor=self.actor_b)
        self.assertEqual(missing.status_code, 403, missing.json)
        self.assertEqual(wrong.status_code, 409, wrong.json)
        self.assertNotIn(self.cid_a, str(missing.json))
        self.assertNotIn(self.cid_b, str(wrong.json))
        self.pat.assert_not_called()


if __name__ == "__main__":
    unittest.main()
