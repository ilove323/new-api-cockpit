"""T07 real Workspace tools: originals, immutable versions and bounded results."""

from uuid import uuid4
from unittest.mock import patch

from test_chat_storage import StorageFixture
from new_api_cockpit.features.chat import config
from new_api_cockpit.features.chat.fs import canonical_json
from new_api_cockpit.features.chat.workspace import Workspace, _line_spans


class ChatWorkspaceTest(StorageFixture):
    def test_incremental_line_spans_match_utf8_splitlines_without_a_full_line_list(
        self,
    ):
        for text in (
            "",
            "x",
            "a\r\nb\rc\nd\v\f\x1c\x1d\x1e\x85\u2028\u2029尾",
            "\n\n",
            "中文😀\r\n尾\n",
        ):
            raw = text.encode("utf-8")
            self.assertEqual(
                [raw[start:end].decode("utf-8") for start, end in _line_spans(raw)],
                text.splitlines(keepends=True),
            )

    def test_incremental_read_long_line_after_partial_text_and_deadline(self):
        entry = self.upload(b"first\n" + b"x" * config.TOOL_RESULT_BYTES)
        workspace = Workspace(self.store, self.scope)
        result = workspace.read_file(entry["id"], 1, 10)
        self.assertEqual(result["text"], "first\n")
        self.assertTrue(result["truncated"])
        self.assertEqual(result["next_line"], 2)
        with patch(
            "new_api_cockpit.features.chat.workspace.time.monotonic",
            side_effect=[0, 10],
        ):
            self.assertEqual(workspace.read_file(entry["id"])["code"], "TOOL_TIMEOUT")

    def upload(self, content=b"first\nSECOND\nthird\n", name="a.md"):
        return self.store.upload(self.scope, name, content, str(uuid4()))

    def test_listing_read_pagination_eof_long_line_and_no_cross_chat(self):
        entry = self.upload()
        workspace = Workspace(self.store, self.scope)
        self.assertEqual(workspace.list_files("all", 0)["total"], 1)
        read = workspace.read_file(entry["id"], 2, 1)
        self.assertEqual(read["text"], "SECOND\n")
        self.assertEqual(read["next_line"], 3)
        eof = workspace.read_file(entry["id"], 99, 20)
        self.assertEqual(eof["text"], "")
        self.assertEqual(eof["end_line"], 98)
        self.assertIsNone(eof["next_line"])
        long = self.upload(b"x" * config.TOOL_RESULT_BYTES)
        self.assertEqual(
            workspace.read_file(long["id"], 1, 1)["code"], "TOOL_LINE_TOO_LONG"
        )
        othercid = self.store.create_conversation(self.a)["id"]
        other = Workspace(self.store, self.store.open_conversation(self.a, othercid))
        self.assertEqual(other.read_file(entry["id"], 1, 1)["code"], "CHAT_NOT_FOUND")
        self.assertFalse(workspace.read_file("../MEMORY.md", 1, 1)["ok"])

    def test_search_is_literal_case_aware_ordered_and_bounded(self):
        entry = self.upload(b"literal .* [a]\nSECOND\nsecond\n")
        workspace = Workspace(self.store, self.scope)
        literal = workspace.search_files(".* [a]", [], True)
        self.assertEqual([m["line"] for m in literal["matches"]], [1])
        self.assertEqual(
            len(workspace.search_files("second", [entry["id"]], False)["matches"]), 2
        )
        self.assertEqual(
            len(workspace.search_files("second", [entry["id"]], True)["matches"]), 1
        )
        self.assertFalse(workspace.search_files("x", [str(uuid4())], True)["ok"])
        with patch.object(config, "TOOL_SEARCH_SCAN_BYTES", 2):
            result = workspace.search_files("a", [], True)
        self.assertTrue(result["truncated"])
        self.assertEqual(result["scanned_bytes"], 0)
        self.upload(b"yes\n" * 120)
        result = workspace.search_files("yes", [], True)
        self.assertEqual(len(result["matches"]), 100)
        self.assertTrue(result["truncated"])
        self.assertLessEqual(len(canonical_json(result)), config.TOOL_RESULT_BYTES)

    def test_outputs_new_versions_call_id_dedup_and_original_hash_unchanged(self):
        original = self.upload(b"original", "report.md")
        with self.store.lease(self.scope, run=True) as lease:
            rid, _, _ = self.accept(lease)
            workspace = Workspace(self.store, self.scope, lease, rid)
            first = workspace.write_file("report.md", "v1", "call-a")
            repeat = workspace.write_file("report.md", "v1", "call-a")
            self.assertEqual(first, repeat)
            self.assertEqual(
                workspace.write_file("report.md", "changed", "call-a")["code"],
                "CHAT_IDEMPOTENCY_CONFLICT",
            )
            second = workspace.write_file("report.md", "v2", "call-b")
            self.assertEqual((first["version"], second["version"]), (1, 2))
            self.assertNotEqual(first["file_id"], second["file_id"])
            self.assertEqual(
                self.store.read_file(self.scope, original["id"])[1], b"original"
            )
            self.assertFalse(
                workspace.write_file("../MEMORY.md", "evil", "call-c")["ok"]
            )
            self.assertEqual(len(self.store.files(self.scope)), 3)
            self.store.cancel(self.scope, rid)
            self.terminal(lease, rid, "cancelled")
        self.assertEqual(self.store.read_file(self.scope, first["file_id"])[1], b"v1")
        self.assertEqual(self.store.read_file(self.scope, second["file_id"])[1], b"v2")

    def test_write_without_lease_and_run_is_denied(self):
        workspace = Workspace(self.store, self.scope)
        self.assertFalse(workspace.write_file("a.md", "hi", "a")["ok"])
        self.assertEqual(self.store.files(self.scope), [])
