"""Real session transport, delegated validation and external PAT boundaries."""

import base64
import io
import json
import time
import unittest
from contextlib import contextmanager
from urllib.error import HTTPError, URLError
from unittest.mock import patch

from new_api_cockpit import auth
from new_api_cockpit.app import app, safe_next


def token(**changes):
    claims = {
        "iss": "new-api",
        "aud": ["new-api-dashboard"],
        "token_use": "access",
        "sub": "12",
        "sid": "test-session",
        "uv": 1,
        "sv": 1,
        "exp": int(time.time()) + 900,
    }
    claims.update(changes)
    payload = (
        base64.urlsafe_b64encode(json.dumps(claims).encode()).rstrip(b"=").decode()
    )
    return "fixture." + payload + ".signature"


USER = {"id": 12, "username": "admin", "role": 100, "status": 1}
HEADERS = {"Content-Type": "application/json", "X-Cockpit-Auth": "1"}


@contextmanager
def upstream(user=None, *, raw=None, error=None):
    with patch("new_api_cockpit.auth.request.build_opener") as build:
        opener = build.return_value
        if error:
            opener.open.side_effect = error
        else:
            body = (
                raw
                if raw is not None
                else json.dumps({"success": True, "data": user or USER}).encode()
            )
            opener.open.return_value.__enter__.return_value.read.return_value = body
        yield opener


class AuthTest(unittest.TestCase):
    def setUp(self):
        self.client = app.test_client()
        self.token = token()

    def login_cookie(self):
        self.client.set_cookie(auth.COOKIE_NAME, self.token, path=auth.COOKIE_PATH)

    def test_session_is_validated_upstream_not_by_untrusted_claims(self):
        for role in (10, 100):
            with upstream({**USER, "role": role}) as opener:
                user = auth.verify_session(self.token)
                self.assertEqual(user["username"], "admin")
                self.assertEqual(user["role"], role)
                req = opener.open.call_args.args[0]
                self.assertTrue(req.full_url.endswith("/api/user/self"))
                self.assertEqual(
                    req.get_header("Authorization"), "Bearer " + self.token
                )
                self.assertIsNone(req.data)
                self.assertEqual(opener.open.call_args.kwargs["timeout"], 5)
        forged = token(role=100)
        with upstream({**USER, "role": 1}), self.assertRaises(auth.LoginForbidden):
            auth.verify_session(forged)

    def test_pat_expired_wrong_audience_and_malformed_tokens_never_reach_upstream(self):
        values = [
            None,
            "",
            "pat-fixture",
            "sk-fixture",
            "a.b.c",
            "x" * 3501,
            token(exp=int(time.time()) - 1),
            token(exp=True),
            token(sub="-1"),
            token(iss="elsewhere"),
            token(aud="other"),
            token(sid=""),
            token(token_use="security_proof"),
        ]
        with patch("new_api_cockpit.auth.request.build_opener") as build:
            for value in values:
                with (
                    self.subTest(value=str(value)[:20]),
                    self.assertRaises(auth.LoginRequired),
                ):
                    auth.verify_session(value)
            build.assert_not_called()

    def test_disabled_non_admin_and_inconsistent_response_fail_closed(self):
        for change in ({"status": 2}, {"role": 1}):
            with upstream({**USER, **change}), self.assertRaises(auth.LoginForbidden):
                auth.verify_session(self.token)
        for body in (
            b"null",
            b"[]",
            b"{}",
            b"not-json",
            b"x" * 65537,
            b'{"success":true,"data":{"id":99}}',
        ):
            with upstream(raw=body), self.assertRaises(auth.AuthUnavailable):
                auth.verify_session(self.token)

    def test_upstream_errors_are_sanitized_and_distinguish_expiry_from_outage(self):
        for status, cls in (
            (401, auth.LoginRequired),
            (403, auth.LoginForbidden),
            (429, auth.AuthUnavailable),
            (500, auth.AuthUnavailable),
            (302, auth.AuthUnavailable),
        ):
            error = HTTPError(
                "http://fixture",
                status,
                "private-secret",
                {},
                io.BytesIO(b"private-secret"),
            )
            with upstream(error=error), self.assertRaises(cls) as caught:
                auth.verify_session(self.token)
            self.assertNotIn("private-secret", str(caught.exception))
        with (
            upstream(error=URLError("private-secret")),
            self.assertRaises(auth.AuthUnavailable),
        ):
            auth.verify_session(self.token)
        self.assertIsNone(
            auth.NoRedirect().redirect_request(
                None, None, 302, "", {}, "https://other.invalid"
            )
        )

    def test_cookie_exchange_checks_origin_header_payload_and_uses_secure_short_cookie(
        self,
    ):
        with upstream():
            response = self.client.post(
                "/cockpit/auth/session",
                json={"access_token": self.token},
                headers=HEADERS,
            )
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("access_token", response.json)
        self.assertNotIn(self.token, response.get_data(as_text=True))
        cookie = response.headers["Set-Cookie"]
        for attribute in ("HttpOnly", "Secure", "SameSite=Strict", "Path=/cockpit"):
            self.assertIn(attribute, cookie)
        with patch("new_api_cockpit.app.verify_session") as verify:
            for headers, body, expected in (
                ({}, {"access_token": self.token}, 403),
                (
                    {**HEADERS, "Origin": "https://foreign.invalid"},
                    {"access_token": self.token},
                    403,
                ),
                (
                    {**HEADERS, "Sec-Fetch-Site": "cross-site"},
                    {"access_token": self.token},
                    403,
                ),
                (HEADERS, {"access_token": self.token, "role": 100}, 400),
                (HEADERS, {"password": "never-accepted"}, 400),
                (HEADERS, [], 400),
                (HEADERS, {"access_token": "x" * 9000}, 413),
            ):
                self.assertEqual(
                    self.client.post(
                        "/cockpit/auth/session", json=body, headers=headers
                    ).status_code,
                    expected,
                )
            verify.assert_not_called()
        with patch.dict("os.environ", {"COCKPIT_COOKIE_SECURE": "false"}), upstream():
            response = self.client.post(
                "/cockpit/auth/session",
                json={"access_token": self.token},
                headers=HEADERS,
            )
            self.assertNotIn("; Secure", response.headers["Set-Cookie"])

    def test_login_and_assets_are_public_pages_redirect_apis_stay_json(self):
        with patch("new_api_cockpit.app.load_site_name", return_value="Fixture <site>"):
            response = self.client.get("/cockpit/login?next=//foreign.invalid")
            self.assertEqual(response.status_code, 200)
            self.assertIn("Fixture &lt;site&gt;", response.text)
            self.assertIn('data-next="/cockpit/statistics/"', response.text)
        for path in (
            "/cockpit/statistics/",
            "/cockpit/users/",
            "/cockpit/keys/",
            "/cockpit/operations/",
        ):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 302)
            self.assertTrue(
                response.headers["Location"].startswith("/cockpit/login?next=")
            )
            self.assertNotIn("WWW-Authenticate", response.headers)
        with self.client.get("/cockpit/static/auth.js") as response:
            self.assertEqual(response.status_code, 200)
        for suffix in ("usage", "balance/status", "usage/by-token", "usage/tokens"):
            response = self.client.get("/cockpit/statistics/api/" + suffix)
            self.assertEqual(response.status_code, 401)
            self.assertEqual(response.json["code"], "AUTH_REQUIRED")
        for value in (
            "https://foreign.invalid",
            "//foreign.invalid",
            "/cockpit\\evil",
            "/cockpit/login",
            "/cockpit/auth/session",
            "/cockpit/users/\n",
            "/cockpit/users/\t",
        ):
            self.assertEqual(safe_next(value), "/cockpit/statistics/")
        self.assertEqual(
            safe_next("/cockpit/keys/?user_id=2"), "/cockpit/keys/?user_id=2"
        )

    def test_basic_and_pat_never_grant_browser_access(self):
        for path in ("/cockpit/users/api/users", "/cockpit/statistics/api/usage"):
            with patch("new_api_cockpit.app.verify_session") as verify:
                response = self.client.get(path, auth=("admin", "password"))
                self.assertEqual(response.status_code, 401)
                verify.assert_not_called()
            self.assertEqual(
                self.client.get(
                    path, headers={"Authorization": "Bearer pat-fixture"}
                ).status_code,
                401,
            )

    def test_every_request_rechecks_session_and_account_switch_blocks_writes(self):
        self.login_cookie()
        with (
            upstream(),
            patch("new_api_cockpit.app.quota_backend.list_users", return_value=[]),
        ):
            self.assertEqual(
                self.client.get("/cockpit/users/api/users").status_code, 200
            )
        with upstream(error=HTTPError("http://fixture", 401, "", {}, io.BytesIO())):
            self.assertEqual(
                self.client.get("/cockpit/users/api/users").status_code, 401
            )
        self.login_cookie()
        with upstream(), patch("new_api_cockpit.app.quota_backend.apply") as apply:
            response = self.client.post(
                "/cockpit/users/api/apply",
                json={},
                headers={
                    "X-Quota-Action": "confirm",
                    "X-Cockpit-Session": "previous-session",
                },
            )
            self.assertEqual(response.status_code, 409)
            self.assertEqual(response.json["code"], "AUTH_SESSION_CHANGED")
            apply.assert_not_called()

    def test_cached_old_basic_header_does_not_break_new_valid_cookie(self):
        self.login_cookie()
        with (
            upstream() as opener,
            patch("new_api_cockpit.app.quota_backend.list_users", return_value=[]),
        ):
            response = self.client.get(
                "/cockpit/users/api/users", auth=("old-admin", "obsolete-password")
            )
            self.assertEqual(response.status_code, 200)
            self.assertEqual(
                opener.open.call_args.args[0].get_header("Authorization"),
                "Bearer " + self.token,
            )

    def test_unauthorized_forbidden_and_unavailable_have_distinct_cookie_behavior(self):
        self.login_cookie()
        with upstream(
            error=HTTPError("http://fixture", 500, "private", {}, io.BytesIO())
        ):
            response = self.client.get("/cockpit/users/api/users")
            self.assertEqual(response.status_code, 503)
            self.assertNotIn("Set-Cookie", response.headers)
        with upstream({**USER, "role": 1}):
            response = self.client.get("/cockpit/users/api/users")
            self.assertEqual(response.status_code, 403)
            self.assertIn("Max-Age=0", response.headers["Set-Cookie"])
        self.login_cookie()
        response = self.client.post(
            "/cockpit/users/api/apply",
            json={},
            headers={"X-Quota-Action": "confirm", "Origin": "https://foreign.invalid"},
        )
        self.assertEqual(response.status_code, 403)
        self.assertNotIn("Set-Cookie", response.headers)

    def test_browser_bearer_is_validated_and_audit_uses_official_identity(self):
        with (
            upstream(),
            patch(
                "new_api_cockpit.app.operation_records.list_records",
                return_value={"rows": []},
            ) as records,
        ):
            response = self.client.get(
                "/cockpit/operations/api/records",
                headers={"Authorization": "Bearer " + self.token},
            )
            self.assertEqual(response.status_code, 200)
            self.assertEqual(records.call_args.args[0], "admin")

    def test_logout_bridge_can_clear_expired_credentials_without_newapi_or_pat(self):
        self.login_cookie()
        with patch("new_api_cockpit.app.verify_session") as verify:
            response = self.client.delete(
                "/cockpit/auth/session", json={}, headers=HEADERS
            )
            self.assertEqual(response.status_code, 200)
            self.assertIn("Max-Age=0", response.headers["Set-Cookie"])
            verify.assert_not_called()

    def test_external_balance_alert_remain_pat_only_even_with_valid_browser_cookie(
        self,
    ):
        self.login_cookie()
        with (
            patch("new_api_cockpit.app.verify_session") as verify,
            patch("new_api_cockpit.app.verify_api_key", return_value=False),
        ):
            for suffix in ("balance", "alert"):
                response = self.client.get("/cockpit/statistics/api/" + suffix)
                self.assertEqual(response.status_code, 401)
                self.assertEqual(response.headers["WWW-Authenticate"], "Bearer")
            verify.assert_not_called()

    def test_api_key_retains_existing_admin_pat_sql_and_no_prefix_rewriting(self):
        with patch("new_api_cockpit.auth.psycopg.connect") as connect:
            conn = connect.return_value.__enter__.return_value
            conn.execute.return_value.fetchone.return_value = {"id": 12}
            self.assertTrue(auth.verify_api_key("sk-fixture"))
            query, params = conn.execute.call_args.args
            self.assertIn("access_token=%s", query)
            self.assertIn("role>=10 AND status=1 AND deleted_at IS NULL", query)
            self.assertEqual(params[0], "sk-fixture")
            conn.execute.return_value.fetchone.return_value = None
            self.assertFalse(auth.verify_api_key("fixture"))
        with patch("new_api_cockpit.auth.psycopg.connect") as connect:
            for value in (None, "", "x" * 257):
                self.assertFalse(auth.verify_api_key(value))
            connect.assert_not_called()


if __name__ == "__main__":
    unittest.main()
