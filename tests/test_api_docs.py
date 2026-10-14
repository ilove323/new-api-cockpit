"""Documentation contract and access checks; no databases, network or writes."""

import json
import re
import unittest
from unittest.mock import patch

from session_fixture import fixture_identity, session_auth
from new_api_cockpit.app import app, safe_next
from new_api_cockpit.openapi import document, serialized_document


class APIDocsTest(unittest.TestCase):
    def test_contract_json_is_reused_but_each_request_still_authenticates(self):
        serialized_document.cache_clear()
        self.addCleanup(serialized_document.cache_clear)
        with patch("new_api_cockpit.openapi.document", wraps=document) as build:
            first = serialized_document()
            self.assertIs(serialized_document(), first)
            self.assertEqual(json.loads(first), document())
            build.assert_called_once()
        client = app.test_client()
        identity = {"id": 1, "username": "fixture", "role": 100}
        with patch("new_api_cockpit.app.verify_pat", return_value=identity) as verify:
            for _ in range(2):
                response = client.get(
                    "/cockpit/api/openapi.json",
                    headers={"Authorization": "Bearer fixture-docs-pat"},
                )
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.text, first)
            self.assertEqual(verify.call_count, 2)
            self.assertEqual(client.get("/cockpit/api/openapi.json").status_code, 401)

    def test_contract_covers_routes_and_marks_side_effecting_reads(self):
        spec = document()

        def normalize(path):
            return re.sub(r"<[^>]+>|\{[^}]+\}", "{}", path)

        described = {
            (method.upper(), normalize(path))
            for path, methods in spec["paths"].items()
            for method in methods
        }
        actual = {
            (method, normalize(rule.rule))
            for rule in app.url_map.iter_rules()
            if rule.rule.startswith("/cockpit/api/")
            for method in rule.methods - {"HEAD", "OPTIONS"}
        }
        self.assertEqual(described, actual)
        self.assertEqual(
            {
                path
                for path in spec["paths"]
                if path.startswith("/cockpit/api/quality/")
            },
            {"/cockpit/api/quality/summary", "/cockpit/api/quality/trends"},
        )
        self.assertEqual(
            spec["servers"],
            [{"url": "/", "description": "当前站点（保留协议、域名和端口）"}],
        )
        identifiers = []
        for path, methods in spec["paths"].items():
            for method, operation in methods.items():
                identifiers.append(operation["operationId"])
                self.assertIn(
                    operation["x-cockpit-effect"],
                    {"read", "preview", "write", "notify", "pat", "session"},
                )
                if method != "get":
                    self.assertIn("requestBody", operation)
                    self.assertTrue(operation["x-cockpit-confirm-headers"])
        self.assertEqual(len(identifiers), len(set(identifiers)))
        self.assertEqual(
            spec["paths"]["/cockpit/api/statistics/alert"]["get"]["x-cockpit-effect"],
            "notify",
        )
        self.assertEqual(
            spec["paths"]["/cockpit/api/keys/{key_id}"]["get"]["x-cockpit-effect"],
            "pat",
        )
        self.assertFalse(
            spec["paths"]["/cockpit/api/auth/session"]["post"]["x-cockpit-testable"]
        )

        def check_refs(value):
            if isinstance(value, dict):
                if "$ref" in value:
                    self.assertIn(
                        value["$ref"].split("/")[-1], spec["components"]["schemas"]
                    )
                for child in value.values():
                    check_refs(child)
            elif isinstance(value, list):
                for child in value:
                    check_refs(child)

        check_refs(spec)

    def test_page_requires_login_and_has_local_assets_and_scoped_csp(self):
        client = app.test_client()
        with (
            patch("new_api_cockpit.app.request_identity", side_effect=fixture_identity),
            patch("new_api_cockpit.app.load_site_name", return_value="Fixture"),
        ):
            response = client.get("/cockpit/docs/")
            self.assertEqual(response.status_code, 302)
            self.assertIn("next=%2Fcockpit%2Fdocs%2F", response.location)
            response = client.get("/cockpit/docs/", auth=session_auth("admin"))
            self.assertEqual(response.status_code, 200)
            self.assertIn('href="/cockpit/docs/" aria-current="page"', response.text)
            self.assertNotRegex(response.text, r'(src|href)="https?://')
            self.assertIn(
                "style-src 'self' 'unsafe-inline'",
                response.headers["Content-Security-Policy"],
            )
            for path in re.findall(
                r'(?:src|href)="(/cockpit/static/[^"?#]+)"', response.text
            ):
                with client.get(path) as asset:
                    self.assertEqual(asset.status_code, 200, path)
            response = client.get(
                "/cockpit/api/openapi.json", auth=session_auth("admin")
            )
            self.assertNotIn(
                "unsafe-inline", response.headers["Content-Security-Policy"]
            )
        self.assertEqual(safe_next("/cockpit/docs/"), "/cockpit/docs/")
        self.assertEqual(
            safe_next("https://other.test/cockpit/docs/"), "/cockpit/statistics/"
        )

    def test_schema_accepts_admin_pat_without_queries_or_token_disclosure(self):
        client = app.test_client()
        identity = {"id": 1, "username": "fixture", "role": 100}
        with (
            patch("new_api_cockpit.app.verify_pat", return_value=identity) as verify,
            patch(
                "new_api_cockpit.app.load_site_name",
                side_effect=AssertionError("schema must not query site options"),
            ),
        ):
            self.assertEqual(client.get("/cockpit/api/openapi.json").status_code, 401)
            response = client.get(
                "/cockpit/api/openapi.json",
                headers={"Authorization": "Bearer fixture-docs-pat"},
            )
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json["openapi"], "3.1.0")
            verify.assert_called_once_with("fixture-docs-pat")
            self.assertNotIn("fixture-docs-pat", response.text)


if __name__ == "__main__":
    unittest.main()
