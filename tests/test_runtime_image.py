"""Actual installed image and bundled VSIX, without account/model services.

Run with COCKPIT_TEST_RUNTIME_IMAGE=1 only in the built runtime container.
The normal source suite skips these checks instead of mistaking a local
development installation for an image verification.
"""

import hashlib
from importlib.metadata import version
import json
import os
from pathlib import Path
import shutil
import unittest
from unittest.mock import patch
import zipfile

from new_api_cockpit import client_downloads
from new_api_cockpit.app import app


@unittest.skipUnless(
    os.environ.get("COCKPIT_TEST_RUNTIME_IMAGE") == "1",
    "Requires the actual built runtime container",
)
class RuntimeImageTest(unittest.TestCase):
    def setUp(self):
        self.assertEqual(os.getuid(), 10001)
        self.assertEqual(Path.cwd(), Path("/app"))
        self.enterContext(
            patch("socket.socket.connect", side_effect=AssertionError("network access"))
        )
        self.enterContext(
            patch("psycopg.connect", side_effect=AssertionError("database access"))
        )
        client_downloads.vscode_release.cache_clear()
        self.addCleanup(client_downloads.vscode_release.cache_clear)
        self.release = client_downloads.vscode_release()
        self.assertIsNotNone(self.release)

    def test_installed_bundle_matches_application_and_has_no_build_or_private_files(
        self,
    ):
        release = self.release
        self.assertRegex(version("new-api-cockpit"), r"^\d+\.\d+\.\d+$")
        payload = release.path.read_bytes()
        self.assertEqual(len(payload), release.size)
        self.assertEqual(hashlib.sha256(payload).hexdigest(), release.sha256)
        with zipfile.ZipFile(release.path) as archive:
            self.assertIsNone(archive.testzip())
            package = json.loads(archive.read("extension/package.json"))
            self.assertEqual(package["version"], release.version)
            self.assertEqual(package["publisher"], "ilove323")
            self.assertEqual(package["name"], "new-api-cockpit-balance")
            self.assertIn("extension/dist/extension.js", archive.namelist())
            self.assertFalse(
                any("/node_modules/" in name for name in archive.namelist())
            )
        self.assertIsNone(shutil.which("node"))
        for name in (".git", ".env", "DEPLOYMENT.private.md", "SITES.local.md", "vsix"):
            self.assertFalse((Path("/app") / name).exists(), name)

    def test_installed_download_is_private_and_returns_the_exact_image_bundle(self):
        client = app.test_client()
        self.assertEqual(client.get("/cockpit/downloads/vscode").status_code, 401)
        with patch(
            "new_api_cockpit.app.request_identity",
            return_value={"id": 12, "username": "synthetic-image-user", "role": 1},
        ):
            response = client.get("/cockpit/downloads/vscode")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.headers["Cache-Control"], "no-store")
            self.assertIn(
                self.release.filename, response.headers["Content-Disposition"]
            )
            self.assertEqual(response.data, self.release.path.read_bytes())
            response.close()
            response = client.head("/cockpit/downloads/vscode")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.content_length, self.release.size)
            response.close()
        self.assertEqual(client.get("/cockpit/downloads/vscode").status_code, 401)


if __name__ == "__main__":
    unittest.main()
