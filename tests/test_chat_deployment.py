"""Static deployment contracts without reading real .env or starting services."""

from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]


class ChatDeploymentTest(unittest.TestCase):
    def test_receipt_redis_templates_skip_root_entrypoint_traversal(self):
        for filename in (
            "docker-compose.yml",
            "compose.release.yml",
            "deploy/full-stack/compose.yml",
        ):
            text = (ROOT / filename).read_text()
            service = text.split("\n  image-studio-receipts:\n", 1)[1]
            service = re.split(r"\n(?:  [a-z][a-z0-9-]*:|[a-z][a-z0-9-]*:)", service)[0]
            self.assertRegex(service, r'user: ["\']?999:1000')
            self.assertIn("cap_drop:", service)
            self.assertNotIn("cap_add:", service)

    def test_chat_pool_uses_only_explicit_environment_not_shared_credentials(self):
        expected = {
            "COCKPIT_PROCESS_ROLE",
            "NEW_API_INTERNAL_URL",
            "CHAT_ENABLED",
            "CHAT_INSTANCE_ID",
            "CHAT_DATA_DIR",
            "CHAT_CAPABILITIES_FILE",
            "CHAT_WEB_SEARCH_BACKEND",
            "CHAT_WEB_SEARXNG_URL",
            "CHAT_MAX_CONCURRENT_RUNS",
            "CHAT_TOTAL_STORAGE_BYTES",
            "TZ",
        }
        for filename in ("docker-compose.yml", "compose.release.yml"):
            with self.subTest(filename=filename):
                text = (ROOT / filename).read_text()
                chat = text.split("\n  chat:\n", 1)[1].split("\n  image-studio:\n", 1)[
                    0
                ]
                self.assertNotIn("env_file:", chat)
                environment = chat.split("    environment:\n", 1)[1].split(
                    "    command:", 1
                )[0]
                keys = set(re.findall(r"^      ([A-Z_]+):", environment, re.M))
                self.assertEqual(keys, expected)
                self.assertIn("profiles: [chat]", chat)
                self.assertIn("read_only: true", chat)
                self.assertIn("create_host_path: false", chat)
                self.assertIn("/tmp:size=64m,mode=1777", chat)
                self.assertIn("127.0.0.1", chat)

    def test_proxy_keeps_chat_upload_headroom_and_no_buffering(self):
        text = (ROOT / "nginx.conf.example").read_text()
        chat = text.split("location ^~ /cockpit/api/chat/ {", 1)[1].split("}", 1)[0]
        self.assertIn("client_max_body_size 51264k;", chat)
        self.assertIn("proxy_set_header Host $http_host;", chat)
        self.assertIn("proxy_buffering off;", chat)
        self.assertIn("proxy_cache off;", chat)


if __name__ == "__main__":
    unittest.main()
