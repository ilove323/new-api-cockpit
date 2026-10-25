"""Loopback-only real Cockpit handler; synthetic identity, no upstream or model."""

from unittest.mock import patch
from flask import Flask
from werkzeug.serving import make_server
from new_api_cockpit.features.account_mcp import http, upstream


def authenticate(credential):
    if credential != "nap_vscode_fixture_user_pat_only":
        raise upstream.AccountError("Invalid fixture credential", 401)
    return {
        "id": 12,
        "username": "fixture-user",
        "quota": 1250001,
        "used_quota": 500000,
        "request_count": 7,
    }


if __name__ == "__main__":
    app = Flask(__name__)
    app.register_blueprint(http.create_blueprint())
    with patch.object(upstream, "authenticate", side_effect=authenticate):
        server = make_server("127.0.0.1", 0, app)
        print(server.server_port, flush=True)
        server.serve_forever()
