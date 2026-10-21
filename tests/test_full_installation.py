"""Offline checks for the documented full-installation examples; no Docker/DB I/O."""

import ast
import importlib.util
from io import BytesIO
import json
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "deploy/full-stack"


def load_script(name):
    spec = importlib.util.spec_from_file_location(
        "full_install_" + name.replace("-", "_"), EXAMPLES / (name + ".py")
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def read_env(path):
    return dict(line.split("=", 1) for line in path.read_text().splitlines())


class FullInstallationConfigTest(unittest.TestCase):
    def setUp(self):
        self.module = load_script("init-config")
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve() / "instance"
        self.chown = self.enterContext(patch.object(self.module.os, "chown"))

    def initialize(self, scheme="http", subnet="172.28.241.0/24"):
        return self.module.initialize(self.root, scheme, "fixture-site", subnet)

    def test_fresh_configuration_is_private_complete_and_role_separated(self):
        result = self.initialize()
        config = self.root / "config"
        postgres = read_env(config / "postgres.env")
        new_api = read_env(config / "new-api.env")
        web, image, chat = (
            read_env(config / (name + ".env")) for name in ("web", "image", "chat")
        )
        self.assertEqual(result["instance_id"], "fixture-site")
        self.assertEqual(new_api["SESSION_COOKIE_SECURE"], "false")
        self.assertEqual(web["COCKPIT_COOKIE_SECURE"], "false")
        self.assertEqual(web["PGPASSWORD"], postgres["READER_DB_PASSWORD"])
        self.assertIn(postgres["SOURCE_DB_PASSWORD"], new_api["SQL_DSN"])
        self.assertIn("new_api_cockpit", image["MONITOR_DATABASE_URL"])
        self.assertNotEqual(
            postgres["SOURCE_DB_PASSWORD"], postgres["READER_DB_PASSWORD"]
        )
        self.assertIn("NOTIFICATION_ENCRYPTION_KEY", web)
        self.assertNotIn("NOTIFICATION_ENCRYPTION_KEY", image)
        for key in chat:
            self.assertFalse(key.startswith("PG"))
            self.assertNotIn(
                key, {"MONITOR_DATABASE_URL", "SESSION_SECRET", "CRYPTO_SECRET"}
            )
        for path in config.glob("*.env"):
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        for name in ("", "config", "data", "logs", "data/chat", "backups", "releases"):
            self.assertEqual(stat.S_IMODE((self.root / name).stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE((self.root / ".env").stat().st_mode), 0o600)
        for name in ("redis", "image-studio-receipts"):
            self.assertEqual(
                stat.S_IMODE((config / (name + ".conf")).stat().st_mode), 0o400
            )
        self.chown.assert_any_call(str(self.root / "data/chat"), 10001, 10001)
        self.chown.assert_any_call(str(config / "redis.conf"), 999, 1000)
        self.assertEqual(
            json.loads((config / "chat-capabilities.json").read_text())["models"], {}
        )
        self.assertEqual(
            json.loads((config / "image-studio-capabilities.json").read_text())[
                "channels"
            ],
            {},
        )

    def test_https_sets_both_cookies_secure_without_changing_origin(self):
        self.initialize("https")
        config = self.root / "config"
        self.assertEqual(
            read_env(config / "new-api.env")["SESSION_COOKIE_SECURE"], "true"
        )
        self.assertEqual(read_env(config / "web.env")["COCKPIT_COOKIE_SECURE"], "true")
        self.assertNotIn("SESSION_COOKIE_TRUSTED_URL", read_env(config / "new-api.env"))

    def test_rerun_does_not_replace_secrets_or_existing_data(self):
        self.initialize()
        before = {str(p): p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        with self.assertRaises(ValueError):
            self.initialize()
        self.assertEqual(
            before,
            {str(p): p.read_bytes() for p in self.root.rglob("*") if p.is_file()},
        )

    def test_existing_data_and_symlinked_managed_directories_are_rejected(self):
        (self.root / "data").mkdir(parents=True)
        record = self.root / "data/preserve.txt"
        record.write_text("fixture user data")
        with self.assertRaises(ValueError):
            self.initialize()
        self.assertEqual(record.read_text(), "fixture user data")
        other = Path(self.temp.name).resolve() / "other"
        other.mkdir()
        (self.root / "config").symlink_to(other, target_is_directory=True)
        with self.assertRaises(ValueError):
            self.initialize()
        self.assertEqual(list(other.iterdir()), [])

    def test_only_rfc1918_private_24_subnets_are_accepted(self):
        for subnet in (
            "127.0.0.0/24",
            "169.254.169.0/24",
            "8.8.8.0/24",
            "172.28.0.0/16",
            "::/24",
        ):
            with self.subTest(subnet=subnet), self.assertRaises(ValueError):
                self.initialize(subnet=subnet)
        self.assertFalse(self.root.exists())

    def test_scripts_parse_as_platform_python_without_project_dependencies(self):
        for name in ("init-config", "bootstrap-admin"):
            ast.parse((EXAMPLES / (name + ".py")).read_text(), feature_version=(3, 6))


class FullInstallationAdminTest(unittest.TestCase):
    def setUp(self):
        self.module = load_script("bootstrap-admin")

    def opener(self, *responses):
        opener = MagicMock()
        opener.open.side_effect = [
            BytesIO(json.dumps(value).encode()) for value in responses
        ]
        return opener

    def test_only_uninitialized_official_setup_can_create_an_account(self):
        opener = self.opener(
            {"success": True, "data": {"status": False, "root_init": False}},
            {"success": True},
        )
        result = self.module.initialize(opener, "fixture-admin", "synthetic-password")
        self.assertTrue(result["official_administrator_initialized"])
        self.assertNotIn("password", json.dumps(result))
        req = opener.open.call_args_list[1].args[0]
        self.assertEqual(req.full_url, "http://127.0.0.1:3000/api/setup")
        self.assertEqual(json.loads(req.data)["DemoSiteEnabled"], False)

    def test_initialized_root_is_never_replaced(self):
        for data in ({"status": True}, {"status": False, "root_init": True}):
            opener = self.opener({"success": True, "data": data})
            with self.subTest(data=data), self.assertRaises(ValueError):
                self.module.initialize(opener, "fixture-admin", "synthetic-password")
            self.assertEqual(opener.open.call_count, 1)

    def test_noninteractive_setup_never_reads_credentials(self):
        with (
            patch.object(self.module.sys.stdin, "isatty", return_value=False),
            patch.object(self.module.getpass, "getpass") as password,
        ):
            with self.assertRaises(ValueError):
                self.module.main()
            password.assert_not_called()


if __name__ == "__main__":
    unittest.main()
