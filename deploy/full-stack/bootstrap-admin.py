"""Initialize the official New API administrator using a terminal-only password."""

import getpass
import json
import sys
from urllib import request


def initialize(opener, username, password):
    base = "http://127.0.0.1:3000"
    with opener.open(base + "/api/setup", timeout=10) as response:
        status = json.load(response)
    if status.get("success") is not True:
        raise ValueError("Cannot read official setup status")
    if status["data"].get("status") or status["data"].get("root_init"):
        raise ValueError("An initialized account exists; refusing to replace it")
    body = {
        "username": username,
        "password": password,
        "confirmPassword": password,
        "SelfUseModeEnabled": False,
        "DemoSiteEnabled": False,
    }
    req = request.Request(
        base + "/api/setup",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
    )
    with opener.open(req, timeout=15) as response:
        result = json.load(response)
    if result.get("success") is not True:
        raise ValueError(
            "Official setup rejected the account; check its password policy"
        )
    return {"official_administrator_initialized": True, "username": username}


def main():
    if not sys.stdin.isatty():
        raise ValueError("Use an interactive terminal; do not pipe credentials")
    username = input("Administrator username (1-12 characters): ").strip()
    if not 1 <= len(username.encode("utf-8")) <= 12:
        raise ValueError("Invalid username")
    password = getpass.getpass("Administrator password (not saved): ")
    if password != getpass.getpass("Confirm password: "):
        raise ValueError("Passwords do not match")
    opener = request.build_opener(request.ProxyHandler({}))
    print(json.dumps(initialize(opener, username, password)))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        # Do not print exception payloads, requests, credentials or API responses.
        print(
            "Administrator setup stopped (" + type(exc).__name__ + ").", file=sys.stderr
        )
        sys.exit(1)
