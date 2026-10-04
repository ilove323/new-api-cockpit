"""Disposable monitoring-schema fixture. Never point its DSN at production."""

from contextlib import contextmanager
import os
import unittest
import uuid
from unittest.mock import patch

import psycopg
from psycopg import sql
from psycopg.rows import dict_row

from new_api_statistics import balance

CONNECT = psycopg.connect
DSN = os.environ.get("MONITOR_DATABASE_URL")


@contextmanager
def isolated_schema():
    name = "cockpit_test_" + uuid.uuid4().hex
    with CONNECT(DSN) as conn:
        conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(name)))

    def connect(*_args, **_kwargs):
        return CONNECT(DSN, row_factory=dict_row, options="-c search_path=" + name)

    try:
        yield connect
    finally:
        with CONNECT(DSN) as conn:
            conn.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(name)))


@unittest.skipUnless(DSN, "Requires a disposable PostgreSQL test database")
class MonitorTestCase(unittest.TestCase):
    def setUp(self):
        self.connect = self.enterContext(isolated_schema())
        self.enterContext(patch.object(balance, "connect", self.connect))
        self.enterContext(patch.object(balance, "configured", return_value=True))
        self.catalog = [dict(channel_id=1, channel_name="fixture", channel_status=1)]
        self.enterContext(
            patch.object(balance, "source_channels", return_value=self.catalog)
        )
        self.notify = self.enterContext(
            patch("new_api_statistics.notifications.notify_safely")
        )
        balance.initialize()
