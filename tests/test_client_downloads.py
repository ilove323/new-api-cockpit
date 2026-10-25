"""Bundled VSIX integrity, logged-in downloads and documentation boundaries."""

import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from new_api_cockpit import client_downloads
from new_api_cockpit.app import app, safe_next
from test_ui import Page


class ClientDownloadsTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.patch = patch.object(client_downloads, "BUNDLE_DIRECTORY", self.directory)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        client_downloads.vscode_release.cache_clear()
        self.addCleanup(client_downloads.vscode_release.cache_clear)
        self.client = app.test_client()

    def package(self, **changes):
        memory = io.BytesIO()
        with zipfile.ZipFile(memory, "w") as archive:
            archive.writestr(
                "extension/package.json",
                json.dumps(
                    {
                        "name": "new-api-cockpit-balance",
                        "publisher": "ilove323",
                        "version": "1.2.3",
                        **changes,
                    }
                ),
            )
        payload = memory.getvalue()
        metadata = {
            "version": "1.2.3",
            "filename": "new-api-cockpit-balance-1.2.3.vsix",
            "size": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest(),
        }
        (self.directory / metadata["filename"]).write_bytes(payload)
        self.write_metadata(metadata)
        return payload, metadata

    def write_metadata(self, metadata):
        (self.directory / "vscode-extension.json").write_text(json.dumps(metadata))
        client_downloads.vscode_release.cache_clear()

    def identity(self, role=1):
        return patch(
            "new_api_cockpit.app.request_identity",
            return_value={"id": 12, "username": "member", "role": role},
        )

    def test_only_verified_package_is_exposed_and_corruption_fails_closed(self):
        self.assertIsNone(client_downloads.vscode_release())
        _, metadata = self.package()
        self.assertEqual(client_downloads.vscode_release().version, "1.2.3")
        for changes in (
            {"filename": "../other.vsix"},
            {"version": "../../other"},
            {"size": 1},
            {"sha256": "0" * 64},
        ):
            with self.subTest(changes=changes):
                self.write_metadata({**metadata, **changes})
                self.assertIsNone(client_downloads.vscode_release())
        self.package(publisher="other")
        self.assertIsNone(client_downloads.vscode_release())

    def test_download_requires_login_and_returns_the_exact_binary_and_filename(self):
        payload, metadata = self.package()
        response = self.client.get("/cockpit/downloads/vscode")
        self.assertEqual(response.status_code, 401)
        self.assertNotIn("Content-Disposition", response.headers)
        with self.identity():
            response = self.client.get("/cockpit/downloads/vscode")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.data, payload)
            self.assertIn(metadata["filename"], response.headers["Content-Disposition"])
            self.assertEqual(response.mimetype, "application/octet-stream")
            self.assertEqual(response.headers["Cache-Control"], "no-store")
            response.close()
            response = self.client.head("/cockpit/downloads/vscode")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.content_length, len(payload))
            response.close()
        self.assertEqual(self.client.get("/cockpit/downloads/vscode").status_code, 401)

    def test_docs_have_three_peer_sections_and_preserve_admin_api_access(self):
        self.package()
        with (
            self.identity(10),
            patch("new_api_cockpit.app.load_site_name", return_value="Fixture"),
        ):
            for route in ("/cockpit/manual", "/cockpit/tools", "/cockpit/docs/"):
                response = self.client.get(route)
                self.assertEqual(response.status_code, 200)
                page = Page(response.text)
                self.assertEqual(page.errors, [])
                groups = [
                    n for n in page.nodes if n["attrs"].get("aria-label") == "文档分类"
                ]
                links = [
                    n["attrs"]["href"]
                    for n in page.nodes
                    if n["tag"] == "a" and groups[0] in n["parents"]
                ]
                self.assertEqual(
                    links, ["/cockpit/manual", "/cockpit/tools", "/cockpit/docs/"]
                )
        with (
            self.identity(),
            patch("new_api_cockpit.app.load_site_name", return_value="Fixture"),
            patch("new_api_cockpit.manual.upstream") as upstream,
        ):
            response = self.client.get("/cockpit/tools")
            self.assertEqual(response.status_code, 200)
            for value in (
                "1.2.3",
                'href="/cockpit/downloads/vscode"',
                'id="manual-mcp-title"',
                "Claude Code",
                "auth.json",
            ):
                self.assertIn(value, response.text)
            self.assertNotIn('src="/cockpit/static/manual.js"', response.text)
            self.assertNotIn('id="manual-token"', response.text)
            self.assertNotIn('href="/cockpit/docs/"', response.text)
            self.assertEqual(self.client.get("/cockpit/docs/").status_code, 403)
            self.assertEqual(
                self.client.get("/cockpit/api/openapi.json").status_code, 403
            )
            upstream.assert_not_called()
        self.assertEqual(safe_next("/cockpit/tools"), "/cockpit/tools")
        self.assertIn(
            "next=%2Fcockpit%2Ftools", self.client.get("/cockpit/tools").location
        )

    def test_missing_bundle_has_no_dead_download_link_or_external_fallback(self):
        with (
            self.identity(),
            patch("new_api_cockpit.app.load_site_name", return_value="Fixture"),
        ):
            response = self.client.get("/cockpit/tools")
            self.assertIn('id="tools-vsix-unavailable"', response.text)
            self.assertNotIn('id="tools-vsix-download"', response.text)
            self.assertEqual(
                self.client.get("/cockpit/downloads/vscode").status_code, 404
            )


if __name__ == "__main__":
    unittest.main()
