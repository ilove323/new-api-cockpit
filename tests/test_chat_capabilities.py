"""Capabilities never guess tools from names or add models to a KEY."""

import json
from pathlib import Path
import tempfile
import unittest

from new_api_cockpit.features.chat.capabilities import Capabilities
from new_api_cockpit.features.chat.errors import ChatError


class CapabilitiesTest(unittest.TestCase):
    def test_unknown_protocol_is_disabled_but_known_chat_gets_tools_by_default(self):
        rows = Capabilities().catalog(
            [
                {"id": "gpt-secret-name", "protocols": []},
                {"id": "generic", "protocols": ["chat"]},
            ]
        )
        self.assertFalse(rows[0]["chat_enabled"])
        self.assertTrue(rows[1]["chat_enabled"])
        self.assertTrue(rows[1]["tools_enabled"])
        self.assertEqual(len(rows[1]["capability_revision"]), 71)

    def test_explicit_config_and_revision_changes(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "caps.json"
            value = {
                "schema_version": 1,
                "models": {"test": {"chat": True, "tools": True}},
            }
            path.write_text(json.dumps(value))
            caps = Capabilities(str(path))
            rows = [{"id": "test", "protocols": []}]
            first = caps.catalog(rows)[0]
            self.assertTrue(
                caps.select(rows, "test", first["capability_revision"], "files")[
                    "tools_enabled"
                ]
            )
            value["models"]["test"]["tools"] = False
            path.write_text(json.dumps(value))
            with self.assertRaises(ChatError) as caught:
                caps.select(rows, "test", first["capability_revision"], "chat")
            self.assertEqual(caught.exception.code, "CHAT_CAPABILITY_CHANGED")
            self.assertEqual(caps.catalog([]), [])

    def test_bad_operator_config_fails_closed(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "caps.json"
            for value in (
                '{"schema_version":1,"schema_version":1,"models":{}}',
                '{"schema_version":1,"models":{"x":{"tools":"true"}}}',
                '{"schema_version":true,"models":{}}',
            ):
                path.write_text(value)
                with self.assertRaises(ChatError):
                    Capabilities(str(path)).load()


if __name__ == "__main__":
    unittest.main()
