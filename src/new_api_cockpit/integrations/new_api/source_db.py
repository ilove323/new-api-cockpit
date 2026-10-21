"""Read-only source connections, with no quota or reporting semantics."""

import psycopg
from psycopg.rows import dict_row


class SourceDb:
    def read_connection(self):
        return psycopg.connect(
            connect_timeout=8,
            row_factory=dict_row,
            options="-c default_transaction_read_only=on -c statement_timeout=10000",
        )
