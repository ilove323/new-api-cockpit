"""Optional official-SDK round trip against a loopback-only fixture server."""

import os
import subprocess
import threading
import unittest
from unittest.mock import patch

from werkzeug.serving import make_server

from new_api_cockpit.app import app
from new_api_cockpit.features.account_mcp import service, upstream

CLIENT = r"""
import asyncio, json, sys
import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

async def main():
    async with httpx2.AsyncClient(headers={"Authorization": "Bearer nap_fixture_account_pat_only"}, trust_env=False) as http:
        async with Client(streamable_http_client(sys.argv[1], http_client=http), read_timeout_seconds=10) as client:
            tools = await client.list_tools()
            assert {tool.name for tool in tools.tools} == {"get_balance", "get_usage", "list_keys", "list_key_groups", "create_key", "delete_key", "change_key_group", "reveal_key"}
            balance = await client.call_tool("get_balance", {})
            assert not balance.is_error and balance.structured_content["remaining_amount"] == "2.5"
            usage = await client.call_tool("get_usage", {})
            assert not usage.is_error and usage.structured_content["user"]["id"] == 12
            print(json.dumps({"sdk_round_trip": True}))

asyncio.run(main())
"""


@unittest.skipUnless(
    os.environ.get("MCP_SDK_PYTHON"), "requires an isolated mcp==2.3.0 SDK environment"
)
class AccountMcpSdkTest(unittest.TestCase):
    def test_official_client_auto_discovery_fallback_and_both_tools(self):
        user = {
            "id": 12,
            "username": "fixture-member",
            "quota": 1250000,
            "used_quota": 500000,
            "request_count": 2,
        }
        with (
            patch.dict(
                os.environ,
                {"COCKPIT_PROCESS_ROLE": "web", "COCKPIT_MCP_ENABLED": "true"},
            ),
            patch.object(upstream, "authenticate", return_value=user),
            patch.object(service, "usage", return_value={"user": {"id": 12}}),
        ):
            server = make_server("127.0.0.1", 0, app, threaded=True)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                result = subprocess.run(
                    [
                        os.environ["MCP_SDK_PYTHON"],
                        "-c",
                        CLIENT,
                        f"http://127.0.0.1:{server.server_port}/cockpit/mcp",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=25,
                    check=False,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn('"sdk_round_trip": true', result.stdout)
            finally:
                server.shutdown()
                thread.join(timeout=5)
                server.server_close()


if __name__ == "__main__":
    unittest.main()
