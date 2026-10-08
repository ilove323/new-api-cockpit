"""Business-route actor fixtures; actual authentication is covered by test_auth.

No network or password validation in business tests. Each test explicitly mocks
browser_identity; these opaque fixture credentials are invalid in production.
"""

from flask import request
from werkzeug.datastructures import Authorization


def session_auth(username):
    return Authorization("bearer", token="fixture-session-" + username)


def fixture_identity():
    auth = request.authorization
    if (
        not auth
        or auth.type != "bearer"
        or not auth.token
        or not auth.token.startswith("fixture-session-")
    ):
        return None
    return {
        "id": 1,
        "username": auth.token.removeprefix("fixture-session-"),
        "role": 100,
        "session_id": "fixture-session",
        "expires_at": 4102444800,
    }
