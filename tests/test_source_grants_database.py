"""Source grants against a NEW, network-disabled PostgreSQL fixture only.

TEST_SOURCE_GRANTS_DATABASE_URL must name cockpit_grants_fixture_<suffix>,
owned by fixture_admin. Never use a site database: this suite owns public
tables and fixture roles, and rejects other databases before any writes.
"""

import hashlib
import json
import os
from pathlib import Path
import time
import unittest
from unittest.mock import patch

import psycopg
from psycopg.rows import dict_row

from new_api_cockpit import auth, pat_permissions


DSN = os.environ.get("TEST_SOURCE_GRANTS_DATABASE_URL")
CONNECT = psycopg.connect
ROOT = Path(__file__).resolve().parents[1]
USER = {"id": 1, "username": "fixture-admin", "role": 100, "status": 1}
PAT = "nap_synthetic_source_grants_fixture"


@unittest.skipUnless(DSN, "Requires a new, isolated source-grants PostgreSQL fixture")
class SourceGrantsDatabaseTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with CONNECT(DSN) as conn:
            database, user, listeners = conn.execute(
                "SELECT current_database(), current_user, current_setting('listen_addresses')"
            ).fetchone()
            if (
                not database.startswith("cockpit_grants_fixture_")
                or user != "fixture_admin"
                or listeners != ""
            ):
                raise RuntimeError("Refusing a non-fixture or network-enabled database")
            if conn.execute(
                "SELECT EXISTS(SELECT 1 FROM pg_tables WHERE schemaname='public')"
            ).fetchone()[0]:
                raise RuntimeError("Fixture must not contain existing public tables")
            conn.execute("CREATE ROLE newapi_owner NOLOGIN")
            conn.execute("CREATE ROLE cockpit_reader NOLOGIN")
            conn.execute("CREATE ROLE fixture_anon NOLOGIN")
            conn.execute("GRANT USAGE, CREATE ON SCHEMA public TO newapi_owner")
            conn.execute("SET ROLE newapi_owner")
            conn.execute("""CREATE TABLE public.users(
                id bigint PRIMARY KEY, role integer, status integer,
                deleted_at timestamptz, access_token text UNIQUE,
                access_token_created_at bigint);
                CREATE TABLE public.tokens(id bigint);
                CREATE TABLE public.logs(id bigint);
                CREATE TABLE public.options(id bigint);
                CREATE TABLE public.channels(id bigint);
                CREATE TABLE public.abilities(
                    "group" text, model text, enabled boolean, channel_id bigint,
                    priority bigint, weight bigint, unrelated text);
                CREATE TABLE public.user_access_tokens(
                    token_hash text, user_id bigint, scopes text, expires_at bigint,
                    unrelated text);
                INSERT INTO public.users(id,role,status)
                    VALUES(1,100,1),(2,1,1),(3,10,1);""")
            conn.execute((ROOT / "sql/source_pat_function.sql").read_text())
            cls.grants = "\n".join(
                line
                for line in (ROOT / "deploy/full-stack/source-grants.sql")
                .read_text()
                .splitlines()
                if not line.startswith("\\set ")
            )
            conn.execute(cls.grants)
            conn.execute(
                "INSERT INTO public.user_access_tokens VALUES(%s,1,%s,-1,'not-readable')",
                (
                    hashlib.sha256(PAT.encode()).hexdigest(),
                    json.dumps(["profile:read", "log:read"]),
                ),
            )

    @classmethod
    def tearDownClass(cls):
        with CONNECT(DSN) as conn:
            conn.execute(
                "DROP FUNCTION public.statistics_ensure_user_pat(bigint,bigint,text)"
            )
            conn.execute("""DROP TABLE public.user_access_tokens, public.abilities,
                public.users, public.tokens, public.logs, public.options, public.channels""")
            conn.execute("DROP OWNED BY cockpit_reader, newapi_owner, fixture_anon")
            conn.execute("DROP ROLE cockpit_reader, newapi_owner, fixture_anon")

    def reader(self, **kwargs):
        options = kwargs.pop("options", "") + " -c role=cockpit_reader"
        return CONNECT(DSN, options=options, **kwargs)

    def test_reader_can_select_only_the_declared_pat_and_ability_columns(self):
        with self.reader(row_factory=dict_row) as conn:
            row = conn.execute(
                "SELECT token_hash,user_id,scopes,expires_at FROM user_access_tokens"
            ).fetchone()
            self.assertEqual(row["user_id"], 1)
            conn.execute(
                'SELECT "group",model,enabled,channel_id,priority,weight FROM abilities'
            )
            for table in ("users", "tokens", "logs", "options", "channels"):
                conn.execute("SELECT * FROM " + table)
        for query in (
            "SELECT * FROM user_access_tokens",
            "SELECT unrelated FROM user_access_tokens",
            "SELECT unrelated FROM abilities",
            "UPDATE user_access_tokens SET scopes='[\"*\"]'",
            "INSERT INTO user_access_tokens(token_hash) VALUES('synthetic')",
            "DELETE FROM user_access_tokens",
            "UPDATE users SET role=100",
            "UPDATE tokens SET id=2",
        ):
            with self.subTest(query=query), self.reader() as conn:
                with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                    conn.execute(query)

    def test_auth_uses_real_reader_grants_and_rejects_expiry_owner_and_revocation(self):
        def fixture_connect(*args, **kwargs):
            self.assertFalse(args)
            return self.reader(**kwargs)

        with (
            patch.object(auth.account, "read", return_value=USER),
            patch.object(auth.psycopg, "connect", side_effect=fixture_connect),
        ):
            actor = auth.verify_pat(PAT)
            self.assertEqual(actor.scopes, {"profile:read", "log:read"})
            pat_permissions.require_access(actor, "usage", "GET")
            with self.assertRaises(auth.LoginForbidden):
                pat_permissions.require_access(actor, "management_action", "POST")
            for expires_at, owner_id in ((int(time.time()) - 60, 1), (-1, 2)):
                with CONNECT(DSN) as conn:
                    conn.execute(
                        "UPDATE user_access_tokens SET expires_at=%s,user_id=%s",
                        (expires_at, owner_id),
                    )
                try:
                    with self.assertRaises(auth.LoginRequired):
                        auth.verify_pat(PAT)
                finally:
                    with CONNECT(DSN) as conn:
                        conn.execute(
                            "UPDATE user_access_tokens SET expires_at=-1,user_id=1"
                        )
        with (
            patch.object(
                auth.account,
                "read",
                side_effect=auth.account.AccountError("fixture-revoked", 401),
            ),
            patch.object(auth.psycopg, "connect") as connect,
        ):
            with self.assertRaises(auth.LoginRequired):
                auth.verify_pat(PAT)
            connect.assert_not_called()

    def test_grants_are_idempotent_and_allow_an_old_schema_without_scoped_pats(self):
        with CONNECT(DSN) as conn:
            conn.execute("SET ROLE newapi_owner")
            conn.execute(self.grants)
            conn.execute("ALTER TABLE user_access_tokens RENAME TO fixture_scoped_pats")
            conn.execute(self.grants)
            self.assertIsNone(
                conn.execute(
                    "SELECT to_regclass('public.user_access_tokens')"
                ).fetchone()[0]
            )
            # Do not leave the temporary old-schema case visible to another test.
            conn.rollback()

    def test_function_is_not_public_and_cannot_grant_scoped_token_write_access(self):
        with self.reader() as conn:
            self.assertEqual(
                conn.execute(
                    "SELECT statistics_ensure_user_pat(1,2,%s)", ("F" * 32,)
                ).fetchone()[0],
                "F" * 32,
            )
        with self.reader() as conn:
            with self.assertRaises(psycopg.errors.RaiseException):
                conn.execute("SELECT statistics_ensure_user_pat(2,1,%s)", ("G" * 32,))
        with CONNECT(DSN) as conn:
            permitted = conn.execute(
                """SELECT has_function_privilege('fixture_anon',
                    'public.statistics_ensure_user_pat(bigint,bigint,text)','EXECUTE')"""
            ).fetchone()[0]
            self.assertFalse(permitted)


if __name__ == "__main__":
    unittest.main()
