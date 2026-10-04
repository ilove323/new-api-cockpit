"""Verify New API administrator passwords and API keys against PostgreSQL."""

import bcrypt
import psycopg
from psycopg.rows import dict_row

# Use the same password-check cost for missing accounts without accepting them.
DUMMY_HASH = bcrypt.hashpw(b"not-a-user-password", bcrypt.gensalt(rounds=10))


def find_user(username):
    with psycopg.connect(
        connect_timeout=8,
        row_factory=dict_row,
        options="-c default_transaction_read_only=on -c statement_timeout=5000",
    ) as conn:
        return conn.execute(
            "SELECT id, username, password, role, status, deleted_at "
            "FROM users WHERE username = %s LIMIT 1",
            (username,),
        ).fetchone()


def verify_admin(username, password):
    if not username or not password or len(username) > 128:
        return False
    encoded = password.encode("utf-8")
    if len(encoded) > 72:
        return False
    user = find_user(username)
    hashed = (user.get("password") or "").encode("utf-8") if user else DUMMY_HASH
    try:
        matches = bcrypt.checkpw(encoded, hashed)
    except ValueError:
        return False
    return bool(
        matches
        and user
        and user["role"] >= 10
        and user["status"] == 1
        and user["deleted_at"] is None
    )


def verify_api_key(value):
    """Validate an existing New API administrator PAT, not an inference token."""
    if not value or len(value) > 256:
        return False
    with psycopg.connect(
        connect_timeout=8,
        row_factory=dict_row,
        options="-c default_transaction_read_only=on -c statement_timeout=5000",
    ) as conn:
        row = conn.execute(
            """SELECT id FROM users WHERE access_token=%s
               AND role>=10 AND status=1 AND deleted_at IS NULL LIMIT 1""",
            (value,),
        ).fetchone()
    return row is not None
