"""Shared UI and asset contracts; no browser or production database."""

from html.parser import HTMLParser
from pathlib import Path
import re
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

from new_api_cockpit.app import app


ROOT = Path(__file__).resolve().parents[1] / "src/new_api_cockpit"


class Page(HTMLParser):
    VOID = {"meta", "link", "input", "img", "br", "hr", "source", "wbr"}

    def __init__(self, html):
        super().__init__()
        self.nodes = []
        self.stack = []
        self.errors = []
        self.feed(html)
        self.close()

    def handle_starttag(self, tag, attrs):
        node = {"tag": tag, "attrs": dict(attrs), "parents": tuple(self.stack)}
        self.nodes.append(node)
        if tag not in self.VOID:
            self.stack.append(node)

    def handle_endtag(self, tag):
        if not self.stack or self.stack[-1]["tag"] != tag:
            self.errors.append(tag)
        else:
            self.stack.pop()

    def one(self, id):
        matches = [node for node in self.nodes if node["attrs"].get("id") == id]
        assert len(matches) == 1, (id, len(matches))
        return matches[0]

    @staticmethod
    def under(node, class_name):
        return any(
            class_name in parent["attrs"].get("class", "").split()
            for parent in node["parents"]
        )


class UITest(unittest.TestCase):
    def setUp(self):
        self.client = app.test_client()
        self.auth = ("admin", "fixture-password")

    def page(self, route):
        with (
            patch("new_api_cockpit.app.verify_admin", return_value=True),
            patch("new_api_cockpit.app.load_site_name", return_value="Fixture <site>"),
        ):
            response = self.client.get("/cockpit/" + route + "/", auth=self.auth)
            self.assertEqual(response.status_code, 200)
            body = response.get_data(as_text=True)
            response.close()
            self.assertIn("Fixture &lt;site&gt;", body)
            return Page(body)

    def test_four_pages_share_shell_theme_and_unique_ids(self):
        for route in ("statistics", "users", "keys", "operations"):
            with self.subTest(route=route):
                page = self.page(route)
                self.assertEqual(page.errors, [])
                self.assertEqual(page.stack, [])
                links = [node for node in page.nodes if node["tag"] == "link"]
                self.assertEqual(links[0]["attrs"]["href"], "/cockpit/static/ui.css")
                self.assertEqual(
                    sum(
                        node["tag"] == "script"
                        and node["attrs"].get("src") == "/cockpit/static/dropdowns.js"
                        for node in page.nodes
                    ),
                    1,
                )
                ids = [
                    node["attrs"]["id"] for node in page.nodes if "id" in node["attrs"]
                ]
                self.assertEqual(len(ids), len(set(ids)))
                self.assertTrue(Page.under(page.one("sidebar-toggle"), "app-header"))
                self.assertEqual(page.one("app-sidebar")["tag"], "aside")
                self.assertIn("hidden", page.one("sidebar-backdrop")["attrs"])
                headings = [node for node in page.nodes if node["tag"] == "h1"]
                self.assertEqual(len(headings), 1)
                self.assertTrue(Page.under(headings[0], "page-heading"))

    def test_statistics_optional_columns_are_unchecked_and_headers_hidden(self):
        page = self.page("statistics")
        for column in ("cache_hit_rate", "amount_per_million", "failure_requests"):
            toggles = [
                node
                for node in page.nodes
                if node["attrs"].get("data-column-toggle") == column
            ]
            headers = [
                node
                for node in page.nodes
                if node["tag"] == "th" and node["attrs"].get("data-column") == column
            ]
            self.assertEqual(len(toggles), 1)
            self.assertNotIn("checked", toggles[0]["attrs"])
            self.assertEqual(len(headers), 1)
            self.assertIn("hidden", headers[0]["attrs"])

    def test_lucide_sprite_references_and_local_fonts_are_valid_and_protected(self):
        sprite = ET.parse(ROOT / "static/icons.svg").getroot()
        symbols = sprite.findall(".//{http://www.w3.org/2000/svg}symbol")
        names = {node.get("id") for node in symbols}
        self.assertEqual(len(names), len(symbols))
        for node in symbols:
            self.assertEqual(node.get("viewBox"), "0 0 24 24")
            self.assertEqual(node.get("stroke-width"), "2")
        for route in ("statistics", "users", "keys", "operations"):
            page = self.page(route)
            for node in page.nodes:
                if node["tag"] == "use":
                    prefix, name = node["attrs"]["href"].split("#")
                    self.assertEqual(prefix, "/cockpit/static/icons.svg")
                    self.assertIn(name, names)
        assets = {"icons.svg", "fonts/public-sans-latin-wght-normal.woff2"}
        for css in (ROOT / "static").glob("*.css"):
            text = css.read_text()
            for file in re.findall(r'url\("([^"\)]+)"\)', text):
                self.assertTrue((ROOT / "static" / file).is_file(), (css, file))
                assets.add(file)
        with patch("new_api_cockpit.app.verify_admin", return_value=True):
            for file in assets:
                path = "/cockpit/static/" + file
                denied = self.client.get(path)
                self.assertEqual(denied.status_code, 401)
                denied.close()
                response = self.client.get(path, auth=self.auth)
                self.assertEqual(response.status_code, 200, path)
                response.close()
        font = ROOT / "static/fonts/public-sans-latin-wght-normal.woff2"
        self.assertEqual(font.read_bytes()[:4], b"wOF2")
        self.assertIn(
            "SIL OPEN FONT LICENSE", (font.parent / "OFL-LICENSE.txt").read_text()
        )
        self.assertIn("Cole Bemis", (ROOT / "static/LUCIDE-LICENSE").read_text())


if __name__ == "__main__":
    unittest.main()
