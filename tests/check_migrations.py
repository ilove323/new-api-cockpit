#!/usr/bin/env python3
# Usage: MONITOR_DATABASE_URL=<disposable database> python tests/check_migrations.py
"""Verify fresh installs, legacy upgrades and migration version recording."""

import os
from pathlib import Path
import uuid
from unittest.mock import patch

import psycopg
from psycopg import sql
from psycopg.rows import dict_row

from new_api_statistics import balance


def main():
    dsn = os.environ["MONITOR_DATABASE_URL"]
    for legacy in (False, True):
        schema = "migration_test_" + uuid.uuid4().hex
        with psycopg.connect(dsn) as conn:
            conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))

        def connect():
            return psycopg.connect(
                dsn, row_factory=dict_row, options="-c search_path=" + schema
            )

        try:
            if legacy:
                with connect() as conn:
                    conn.execute("""CREATE TABLE notification_settings (
                        id integer PRIMARY KEY, version bigint DEFAULT 1,
                        enabled boolean DEFAULT false, channel text DEFAULT 'feishu_app',
                        updated_at timestamptz DEFAULT now(), updated_by text,
                        last_attempt_at timestamptz, last_success_at timestamptz, last_error text,
                        app_id text, secret_encrypted text, robot_code text,
                        receive_id_type text, receive_id text
                    )""")
                    conn.execute("""INSERT INTO notification_settings
                        (id, app_id, secret_encrypted, robot_code, receive_id_type, receive_id)
                        VALUES (1, 'fixture-app', 'fixture-ciphertext', '', 'chat_id', 'fixture-chat')""")
            with patch.object(balance, "connect", connect):
                balance.initialize()
                with connect() as conn:
                    conn.execute("UPDATE balance_settings SET budget=1234 WHERE id=1")
                balance.initialize()
            with connect() as conn:
                assert conn.execute(
                    "SELECT count(*) AS n FROM schema_migrations"
                ).fetchone()["n"] == len(
                    list(
                        Path(balance.__file__)
                        .with_name("migrations")
                        .glob("[0-9]*.sql")
                    )
                )
                assert (
                    conn.execute(
                        "SELECT budget FROM balance_settings WHERE id=1"
                    ).fetchone()["budget"]
                    == 1234
                )
                if legacy:
                    row = conn.execute(
                        "SELECT * FROM notification_feishu_settings WHERE id=1"
                    ).fetchone()
                    assert row["secret_encrypted"] == "fixture-ciphertext"
                    assert row["receive_id"] == "fixture-chat"
                    assert not conn.execute("""SELECT 1 FROM information_schema.columns
                        WHERE table_schema=current_schema() AND table_name='notification_settings'
                        AND column_name='secret_encrypted'""").fetchone()
                assert conn.execute(
                    "SELECT to_regclass('notification_dingtalk_webhook_settings') AS name"
                ).fetchone()["name"]
                assert conn.execute(
                    "SELECT to_regclass('balance_excluded_channels') AS name"
                ).fetchone()["name"]
                assert conn.execute(
                    "SELECT to_regclass('balance_channel_inventory') AS name"
                ).fetchone()["name"]
                assert conn.execute(
                    "SELECT to_regclass('balance_month_channels') AS name"
                ).fetchone()["name"]
                assert (
                    conn.execute(
                        "SELECT to_regclass('notification_dingtalk_settings') AS name"
                    ).fetchone()["name"]
                    is None
                )
        finally:
            with psycopg.connect(dsn) as conn:
                conn.execute(
                    sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema))
                )

    schema = "migration_v010_" + uuid.uuid4().hex
    with psycopg.connect(dsn) as conn:
        conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))

    def v010_connect():
        return psycopg.connect(
            dsn, row_factory=dict_row, options="-c search_path=" + schema
        )

    try:
        with v010_connect() as conn:
            conn.execute("""CREATE TABLE schema_migrations (
                version text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())""")
            conn.execute(
                "INSERT INTO schema_migrations(version) VALUES ('001_initial.sql')"
            )
            conn.execute("""CREATE TABLE balance_settings (
                id integer PRIMARY KEY, budget numeric(24,6) NOT NULL DEFAULT 0,
                threshold numeric(24,6) NOT NULL DEFAULT 0, start_month date NOT NULL,
                enabled boolean NOT NULL DEFAULT false, version bigint NOT NULL DEFAULT 1,
                updated_at timestamptz NOT NULL DEFAULT now(), updated_by text)""")
            conn.execute("""CREATE TABLE balance_months (
                month date PRIMARY KEY, amount numeric(24,6) NOT NULL,
                archived_at timestamptz NOT NULL DEFAULT now())""")
            conn.execute("""CREATE TABLE notification_settings (
                id integer PRIMARY KEY, version bigint NOT NULL DEFAULT 1,
                enabled boolean NOT NULL DEFAULT false, channel text NOT NULL,
                updated_at timestamptz NOT NULL DEFAULT now(), updated_by text,
                last_attempt_at timestamptz, last_success_at timestamptz, last_error text)""")
            conn.execute("""INSERT INTO notification_settings(id,enabled,channel)
                VALUES (1,true,'dingtalk_app')""")
            conn.execute("""CREATE TABLE notification_feishu_settings (
                id integer PRIMARY KEY, app_id text NOT NULL DEFAULT '',
                secret_encrypted text NOT NULL DEFAULT '',
                receive_id_type text NOT NULL DEFAULT 'chat_id',receive_id text NOT NULL DEFAULT '')""")
            conn.execute("""CREATE TABLE notification_dingtalk_settings (
                id integer PRIMARY KEY, client_id text NOT NULL DEFAULT '',
                secret_encrypted text NOT NULL DEFAULT '',robot_code text NOT NULL DEFAULT '',
                open_conversation_id text NOT NULL DEFAULT '')""")
        with v010_connect() as conn:
            conn.execute("""CREATE TABLE balance_state (
                id integer PRIMARY KEY, version bigint, current_month date,
                current_amount numeric(24,6),archived_amount numeric(24,6),remaining numeric(24,6),
                checked_at timestamptz,last_error text);
                CREATE TABLE balance_daily_runs(day date PRIMARY KEY,checked_at timestamptz DEFAULT now());
                CREATE TABLE balance_settings_audit(id bigserial PRIMARY KEY,username text,version bigint,
                budget numeric(24,6),threshold numeric(24,6),start_month date,enabled boolean,
                created_at timestamptz DEFAULT now());
                CREATE TABLE balance_alerts(id bigserial PRIMARY KEY,remaining numeric(24,6),threshold numeric(24,6),
                spent numeric(24,6),budget numeric(24,6),created_at timestamptz DEFAULT now(),
                updated_at timestamptz DEFAULT now(),resolved_at timestamptz);""")
        with patch.object(balance, "connect", v010_connect):
            balance.initialize()
            balance.initialize()
        with v010_connect() as conn:
            assert conn.execute(
                "SELECT count(*) AS n FROM schema_migrations"
            ).fetchone()["n"] == len(
                list(Path(balance.__file__).with_name("migrations").glob("[0-9]*.sql"))
            )
            settings = conn.execute(
                "SELECT * FROM notification_settings WHERE id=1"
            ).fetchone()
            assert settings["channel"] == "dingtalk_webhook"
            assert not settings["enabled"]
            assert "重新配置" in settings["last_error"]
            assert conn.execute(
                "SELECT to_regclass('notification_dingtalk_webhook_settings') AS name"
            ).fetchone()["name"]
            assert (
                conn.execute(
                    "SELECT to_regclass('notification_dingtalk_settings') AS name"
                ).fetchone()["name"]
                is None
            )
    finally:
        with psycopg.connect(dsn) as conn:
            conn.execute(
                sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema))
            )
    print("PASS: fresh install, legacy migration, idempotency and data preservation")


if __name__ == "__main__":
    main()
