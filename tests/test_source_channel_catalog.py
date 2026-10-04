"""Real catalog SQL regression. MONITOR_DATABASE_URL must point to disposable PG."""

import os
import unittest
from unittest.mock import patch

import psycopg
from psycopg.rows import dict_row

from new_api_cockpit import balance


@unittest.skipUnless(
    os.environ.get("MONITOR_DATABASE_URL"), "disposable PostgreSQL not configured"
)
class SourceChannelCatalogTest(unittest.TestCase):
    def test_real_union_bigint_status_and_deleted_log_channels(self):
        # Session-local tables shadow any business tables: no persistent schema changes.
        conn = psycopg.connect(os.environ["MONITOR_DATABASE_URL"], row_factory=dict_row)
        self.addCleanup(conn.close)
        conn.execute("""CREATE TEMP TABLE channels (
            id bigint PRIMARY KEY, name text, status bigint, tag text
        ); CREATE TEMP TABLE logs (channel_id bigint)""")
        conn.execute("""INSERT INTO channels(id,name,status,tag) VALUES
            (1,'primary',1,'  upstream-A  '),
            (2,'   ',2,NULL),
            (3,'disabled',3,'  '),
            (0,'zero',1,NULL);
            INSERT INTO logs(channel_id) VALUES
            (1),(1),(2),(0),(99),(99),(100),(NULL),(NULL)""")
        conn.commit()
        conn.execute("SET default_transaction_read_only=on")
        conn.commit()

        def source_connection(**kwargs):
            # Preserve and verify source's read-only contract while reusing the temp tables.
            self.assertIn("default_transaction_read_only=on", kwargs["options"])
            self.assertIs(kwargs["row_factory"], dict_row)
            self.assertEqual(
                conn.execute("SHOW transaction_read_only").fetchone()[
                    "transaction_read_only"
                ],
                "on",
            )
            return conn

        # Only redirect the connection; the production SQL and its result are not mocked.
        with patch.object(balance.psycopg, "connect", side_effect=source_connection):
            rows = balance.source_channels(include_deleted=True)

        self.assertEqual([row["channel_id"] for row in rows], [0, 1, 2, 3, 99, 100])
        by_id = {row["channel_id"]: row for row in rows}
        self.assertEqual(by_id[1]["tag_value"], "upstream-A")
        self.assertEqual(by_id[1]["channel_status"], 1)
        self.assertFalse(by_id[1]["is_deleted"])
        self.assertEqual(by_id[2]["channel_name"], "渠道 #2")
        self.assertEqual(by_id[2]["channel_status"], 2)
        self.assertEqual(by_id[2]["tag_value"], "")
        self.assertEqual(by_id[3]["tag_value"], "")
        for channel_id in (99, 100):
            self.assertEqual(
                by_id[channel_id],
                dict(
                    channel_id=channel_id,
                    channel_name=f"渠道 #{channel_id}",
                    channel_status=None,
                    tag_value="",
                    is_deleted=True,
                ),
            )

    def test_routine_catalog_query_never_requires_a_logs_table(self):
        conn = psycopg.Connection.connect(
            os.environ["MONITOR_DATABASE_URL"], row_factory=dict_row
        )
        self.addCleanup(conn.close)
        conn.execute(
            "CREATE TEMP TABLE channels(id bigint,name text,status bigint,tag text)"
        )
        conn.execute("INSERT INTO channels VALUES (7,'fixture',1,'A')")
        conn.commit()
        conn.execute("SET default_transaction_read_only=on")
        conn.commit()
        with patch.object(balance.psycopg, "connect", return_value=conn):
            rows = balance.source_channels()
        self.assertEqual([r["channel_id"] for r in rows], [7])
