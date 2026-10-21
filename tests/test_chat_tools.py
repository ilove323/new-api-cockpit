"""Only a fake scoped workspace; no filesystem, model, network or SDK."""

import copy
import hashlib
import json
import threading
import time
import unittest
from unittest.mock import patch
from uuid import uuid4

from new_api_cockpit.features.chat import config, tools
from new_api_cockpit.features.chat.errors import ChatError
from new_api_cockpit.features.chat.transport import AbortControl

FID, CID, RID = (str(uuid4()) for _ in range(3))


class FakeWorkspace:
    def __init__(self):
        self.calls, self.writes = [], {}
        self.original = b"original content"

    def list_files(self, kind, offset):
        self.calls.append(("list", kind, offset))
        return {
            "files": [
                {
                    "file_id": FID,
                    "name": "input.txt",
                    "kind": "upload",
                    "size_bytes": 16,
                    "version": None,
                    "path": "/host/secret",
                    "owner_user_id": 888,
                }
            ],
            "next_offset": None,
            "total": 1,
            "secret": "must-not-pass",
        }

    def read_file(self, fid, start, lines):
        self.calls.append(("read", fid, start, lines))
        if fid != FID:
            raise ChatError("/host/other-user/secrets", 404, "TOOL_FILE_NOT_FOUND")
        return {
            "file_id": fid,
            "start_line": start,
            "end_line": start,
            "text": "original content",
            "truncated": False,
            "next_line": None,
        }

    def search_files(self, query, fids, sensitive):
        self.calls.append(("search", query, fids, sensitive))
        return {
            "matches": [
                {"file_id": FID, "line": 1, "text": query, "path": "/host/path"}
            ],
            "truncated": False,
            "scanned_files": 1,
            "scanned_bytes": 16,
        }

    def write_file(self, name, content, call_id):
        self.calls.append(("write", name, content, call_id))
        if call_id not in self.writes:
            fid = str(uuid4())
            self.writes[call_id] = {
                "file_id": fid,
                "name": name,
                "size_bytes": len(content.encode()),
                "version": len(self.writes) + 1,
                "sha256": hashlib.sha256(content.encode()).hexdigest(),
                "download_path": f"/cockpit/api/chat/conversations/{CID}/files/{fid}/download",
            }
        return self.writes[call_id]


class ToolsTest(unittest.TestCase):
    def setUp(self):
        self.ws = FakeWorkspace()
        self.rc = {
            "run_id": RID,
            "call_id": "call_1",
            "deadline": time.monotonic() + 60,
        }

    def call(self, name, args):
        return tools.execute(name, args, self.ws, self.rc)

    def test_exact_four_fresh_schema_objects_no_extra_properties(self):
        schemas = tools.schemas()
        self.assertEqual(
            [t["function"]["name"] for t in schemas],
            ["list_files", "read_file", "search_files", "write_file"],
        )
        for tool in schemas:
            self.assertEqual(tool["type"], "function")
            self.assertFalse(tool["function"]["parameters"]["additionalProperties"])
        schemas[0]["function"]["name"] = "shell"
        self.assertEqual(tools.schemas()[0]["function"]["name"], "list_files")

    def test_all_four_signatures_and_safe_result_projection(self):
        results = [
            self.call("list_files", {"kind": "all", "offset": 0}),
            self.call("read_file", {"file_id": FID, "start_line": 1, "max_lines": 200}),
            self.call(
                "search_files",
                {"query": "a.*[x]", "file_ids": [], "case_sensitive": False},
            ),
            self.call("write_file", {"name": "out.md", "content": "中文"}),
        ]
        self.assertTrue(all(r["ok"] for r in results), results)
        self.assertEqual(self.ws.calls[2], ("search", "a.*[x]", [], False))
        self.assertNotIn("/host", json.dumps(results))
        self.assertNotIn("must-not-pass", json.dumps(results))

    def test_schema_rejections_never_touch_workspace(self):
        invalid = [
            ("shell", {}),
            ("__class__", {}),
            ("load_skill", {}),
            ("list_files", {"kind": "all", "offset": False}),
            ("list_files", {"kind": "all", "offset": 201}),
            ("list_files", {"kind": "all", "offset": 0, "path": "/etc"}),
            ("list_files", {"kind": [], "offset": 0}),
            (
                "read_file",
                {"file_id": "../../MEMORY.md", "start_line": 1, "max_lines": 1},
            ),
            ("read_file", {"file_id": FID, "start_line": True, "max_lines": 1}),
            ("read_file", {"file_id": FID, "start_line": 1, "max_lines": 201}),
            (
                "search_files",
                {"query": "x", "file_ids": [FID, FID], "case_sensitive": False},
            ),
            ("search_files", {"query": "x", "file_ids": [], "case_sensitive": 0}),
            (
                "search_files",
                {"query": "x" * 201, "file_ids": [], "case_sensitive": False},
            ),
            ("write_file", {"name": "x.txt", "content": "x", "overwrite": True}),
            ("write_file", {"name": "x.txt", "content": "\ud800"}),
        ]
        for name, args in invalid:
            with self.subTest(name=name, args=args):
                self.assertFalse(self.call(name, args)["ok"])
        self.assertEqual(self.ws.calls, [])

    def test_no_paths_hidden_files_binary_extensions_or_control_characters(self):
        for name in (
            "../a.txt",
            "/a.txt",
            "a\\b.txt",
            ".env",
            "a.pdf",
            "a.zip",
            "a.txt\x00",
            "x\n.txt",
            "a:b.txt",
            "x.txt.",
        ):
            with self.subTest(name=name):
                self.assertEqual(
                    self.call("write_file", {"name": name, "content": "x"})["code"],
                    "TOOL_INVALID_ARGUMENTS",
                )
        self.assertEqual(self.ws.calls, [])
        for name in ("MEMORY.md", "SKILL.md", "code.sh", "PAGE.HTML"):
            self.assertTrue(
                self.call("write_file", {"name": name, "content": "inert text"})["ok"]
            )

    def test_utf8_bytes_not_characters_and_escaped_result_budget(self):
        self.assertFalse(
            self.call("write_file", {"name": "out.txt", "content": "界" * 21846})["ok"]
        )
        self.assertTrue(
            self.call("write_file", {"name": "out.txt", "content": "x" * 65536})["ok"]
        )
        with patch.object(config, "TOOL_RESULT_BYTES", 50):
            result = self.call("list_files", {"kind": "all", "offset": 0})
            self.assertEqual(result["code"], "TOOL_RESULT_LIMIT")

    def test_write_idempotence_is_forwarded_to_transaction_owner(self):
        first = self.call("write_file", {"name": "input.txt", "content": "new"})
        second = self.call("write_file", {"name": "input.txt", "content": "new"})
        self.assertEqual(first, second)
        self.assertEqual(len(self.ws.writes), 1)
        self.assertEqual(self.ws.original, b"original content")
        self.rc["call_id"] = "call_2"
        self.assertNotEqual(
            self.call("write_file", {"name": "input.txt", "content": "new"})["file_id"],
            first["file_id"],
        )

    def test_other_scope_error_is_indistinguishable_and_no_exception_text_leaks(self):
        result = self.call(
            "read_file", {"file_id": str(uuid4()), "start_line": 1, "max_lines": 1}
        )
        self.assertEqual(result["code"], "TOOL_FILE_NOT_FOUND")
        self.assertNotIn("/host", str(result))
        self.ws.read_file = lambda *a: (_ for _ in ()).throw(
            OSError("credential=secret")
        )
        with self.assertRaises(ChatError) as error:
            self.call("read_file", {"file_id": FID, "start_line": 1, "max_lines": 1})
        self.assertNotIn("secret", str(error.exception))
        self.assertEqual(error.exception.code, "CHAT_STORAGE_UNAVAILABLE")

    def test_persistence_errors_escape_but_explicit_quota_error_is_safe(self):
        for status, code in (
            (503, "CHAT_STORAGE_UNAVAILABLE"),
            (503, "CHAT_STORAGE_CORRUPT"),
            (507, "CHAT_STORAGE_FULL"),
            (500, "OTHER_BACKEND_FAILURE"),
            (409, "CHAT_STORAGE_CORRUPT"),
        ):
            original = ChatError("bounded storage error", status, code)

            def fail(*args):
                raise original

            self.ws.list_files = fail
            with self.subTest(code=code), self.assertRaises(ChatError) as error:
                self.call("list_files", {"kind": "all", "offset": 0})
            self.assertIs(error.exception, original)
        self.ws.list_files = lambda *a: (_ for _ in ()).throw(
            ChatError("quota", 507, "TOOL_STORAGE_LIMIT")
        )
        self.assertEqual(
            self.call("list_files", {"kind": "all", "offset": 0})["code"],
            "TOOL_STORAGE_LIMIT",
        )

    def test_workspace_error_objects_are_normalized_without_raw_messages(self):
        for source, expected in (
            ("CHAT_NOT_FOUND", "TOOL_FILE_NOT_FOUND"),
            ("TOOL_RESULT_TOO_LARGE", "TOOL_RESULT_LIMIT"),
            ("CHAT_INVALID_REQUEST", "TOOL_INVALID_ARGUMENTS"),
        ):
            self.ws.list_files = lambda *a: {
                "ok": False,
                "code": source,
                "message": "/host/secret",
            }
            result = self.call("list_files", {"kind": "all", "offset": 0})
            self.assertEqual(result["code"], expected)
            self.assertNotIn("secret", str(result))

    def test_list_preserves_valid_stored_upload_names_and_null_versions(self):
        base = self.ws.list_files("all", 0)
        base["files"][0]["name"] = ".notes.md"
        self.ws.list_files = lambda *args: base
        result = self.call("list_files", {"kind": "all", "offset": 0})
        self.assertTrue(result["ok"])
        self.assertEqual(result["files"][0]["name"], ".notes.md")
        self.assertIsNone(result["files"][0]["version"])

    def test_cancel_arriving_inside_workspace_is_not_hidden_as_tool_failure(self):
        stop = threading.Event()
        self.rc["stop_event"] = stop
        original = self.ws.list_files

        def stop_during_call(*args):
            stop.set()
            return original(*args)

        self.ws.list_files = stop_during_call
        with self.assertRaises(ChatError) as error:
            self.call("list_files", {"kind": "all", "offset": 0})
        self.assertEqual(error.exception.code, "CHAT_CANCELLED")

    def test_cancel_deadline_and_five_second_cooperative_boundary(self):
        for key, value, code in (
            ("deadline", time.monotonic() - 1, "CHAT_DEADLINE"),
            ("stop_event", threading.Event(), "CHAT_CANCELLED"),
            ("abort", AbortControl(), "CHAT_CANCELLED"),
        ):
            rc = copy.copy(self.rc)
            if key == "stop_event":
                value.set()
            elif key == "abort":
                value.abort()
            rc[key] = value
            with self.subTest(key=key), self.assertRaises(ChatError) as error:
                tools.execute("list_files", {"kind": "all", "offset": 0}, self.ws, rc)
            self.assertEqual(error.exception.code, code)
        self.assertEqual(self.ws.calls, [])
        with patch.object(config, "TOOL_DEADLINE_SECONDS", -1):
            self.assertEqual(
                self.call("list_files", {"kind": "all", "offset": 0})["code"],
                "TOOL_TIMEOUT",
            )


if __name__ == "__main__":
    unittest.main()
