"""Scoped PATs cannot inherit all administrator privileges or bypass upstream."""

import hashlib
import json
import unittest
from unittest.mock import patch

from new_api_cockpit import auth, pat_permissions
from new_api_cockpit.app import app

PAT = "nap_synthetic_scoped_test"
USER = {"id": 12, "username": "synthetic", "role": 10, "status": 1}


class PatPermissionsTest(unittest.TestCase):
    def test_scope_lookup_is_hash_bound_to_verified_owner_and_never_grants_from_sql_alone(
        self,
    ):
        with (
            patch.object(auth.account, "read", return_value=USER) as read,
            patch.object(auth.psycopg, "connect") as connect,
        ):
            conn = connect.return_value.__enter__.return_value
            conn.execute.return_value.fetchone.return_value = {
                "scopes": '["profile:read","log:read"]'
            }
            actor = auth.verify_pat(PAT)
            self.assertEqual(actor.scopes, {"profile:read", "log:read"})
            self.assertEqual(
                json.loads(json.dumps(actor)),
                {k: USER[k] for k in ("id", "username", "role")},
            )
            query, parameters = conn.execute.call_args.args
            self.assertNotIn(PAT, query)
            self.assertEqual(
                parameters[:2], (hashlib.sha256(PAT.encode()).hexdigest(), USER["id"])
            )
            self.assertIn(
                "default_transaction_read_only=on", connect.call_args.kwargs["options"]
            )
            self.assertEqual(read.call_count, 1)
        with (
            patch.object(
                auth.account,
                "read",
                side_effect=auth.account.AccountError("revoked", 401),
            ),
            patch.object(auth.psycopg, "connect") as connect,
        ):
            with self.assertRaises(auth.LoginRequired):
                auth.verify_pat(PAT)
            connect.assert_not_called()

    def test_missing_or_malformed_scope_data_fails_closed(self):
        for row in (None, {"scopes": "{}"}, {"scopes": "[1]"}, {"scopes": "not-json"}):
            with (
                patch.object(auth.account, "read", return_value=USER),
                patch.object(auth.psycopg, "connect") as connect,
            ):
                connect.return_value.__enter__.return_value.execute.return_value.fetchone.return_value = row
                with self.assertRaises((auth.LoginRequired, auth.LoginForbidden)):
                    auth.verify_pat(PAT)

    def test_scoped_reader_cannot_write_or_call_an_undeclared_endpoint(self):
        actor = auth.VerifiedPat(USER, frozenset({"profile:read", "log:read"}))
        pat_permissions.require_access(actor, "usage", "GET")
        for endpoint in (
            "quota_apply",
            "management_action",
            "balance_settings",
            "future_endpoint",
        ):
            with self.assertRaises(auth.LoginForbidden):
                pat_permissions.require_access(actor, endpoint, "POST")
        with self.assertRaises(auth.LoginForbidden):
            pat_permissions.require_secret_access(actor)

    def test_every_current_pat_endpoint_has_explicit_policy(self):
        for rule in app.url_map.iter_rules():
            if (
                not rule.rule.startswith("/cockpit/api/")
                or "/chat/" in rule.rule
                or rule.endpoint == "browser_session"
            ):
                continue
            for method in rule.methods - {"OPTIONS"}:
                self.assertIsNotNone(
                    pat_permissions.required_scopes(rule.endpoint, method),
                    (rule.endpoint, method),
                )

    def test_http_scope_rejection_precedes_business_action(self):
        actor = auth.VerifiedPat(USER, frozenset({"profile:read", "log:read"}))
        with (
            patch("new_api_cockpit.app.verify_pat", return_value=actor),
            patch("new_api_cockpit.app.quota_backend.apply") as write,
        ):
            response = app.test_client().post(
                "/cockpit/api/users/quota/apply",
                json={},
                headers={"Authorization": "Bearer " + PAT},
            )
        self.assertEqual(response.status_code, 403)
        write.assert_not_called()
