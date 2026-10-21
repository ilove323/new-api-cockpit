"""T04 offline storage tests: real disk, no network or model credentials."""

import errno
import hashlib
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

from new_api_cockpit.features.chat import config
from new_api_cockpit.features.chat.config import Settings
from new_api_cockpit.features.chat.errors import ChatError
from new_api_cockpit.features.chat.fs import canonical_json, utcnow
from new_api_cockpit.features.chat.storage import ChatStore
from new_api_cockpit.platform.identity import Principal


class StorageFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = str(Path(self.temp.name).resolve())
        self.settings = Settings(data_dir=self.root, instance_id="site-a", enabled=True)
        self.store = ChatStore(self.settings)
        self.a, self.b, self.admin = (
            Principal(42, "A"),
            Principal(43, "B"),
            Principal(1, "admin", role=100),
        )
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(self.store.close)
        self.cid = self.store.create_conversation(self.a)["id"]
        self.scope = self.store.open_conversation(self.a, self.cid)

    def assert_code(self, code, function, *args, **kwargs):
        with self.assertRaises(ChatError) as caught:
            function(*args, **kwargs)
        self.assertEqual(code, caught.exception.code)
        return caught.exception

    def snapshot(self, rid):
        return {
            "v": 1,
            "conversation_id": self.cid,
            "run_id": rid,
            "captured_at": utcnow(),
            "context_revision": 0,
            "policy_version": "personal-context-v1",
            "profiles": [
                {
                    "name": name,
                    "revision": 0,
                    "content": "",
                    "sha256": hashlib.sha256(b"").hexdigest(),
                    "size_bytes": 0,
                    "updated_at": None,
                }
                for name in ("MEMORY.md", "SOUL.md", "IDENTITY.md")
            ],
            "skills": [],
            "skill_ids": [],
            "budget": {
                "personal_block_bytes": 0,
                "limit_bytes": config.PERSONAL_CONTEXT_BYTES,
            },
        }

    def accept(self, lease, rid=None, request_id=None):
        rid = rid or str(uuid4())
        snapshot = self.snapshot(rid)
        ref = self.store.write_context(self.scope, rid, snapshot, lease=lease)
        data = {
            "client_request_id": request_id or str(uuid4()),
            "message_id": str(uuid4()),
            "text": "hello",
            "attachment_ids": [],
            "token_id": 3,
            "model": "fake-chat",
            "mode": "chat",
            "capability_revision": "sha256:" + "a" * 64,
            "context_snapshot": ref,
            "context_from_run_ids": [],
        }
        event = self.store.append(
            self.scope, "run.accepted", data, run_id=rid, lease=lease
        )
        return rid, data, event

    def terminal(self, lease, rid, status="completed"):
        run = self.store.get_run(self.scope, rid, lease=lease)
        return self.store.append(
            self.scope,
            "run.terminal",
            {
                "status": status,
                "reason": "test",
                "error_code": None,
                "uncertain": run["model_requests"] > 0,
                "model_requests": run["model_requests"],
                "tool_calls": run["tool_calls"],
                "usage": run["usage"],
                "usage_complete": run["usage_complete"],
            },
            run_id=rid,
            lease=lease,
        )


class ChatStorageTest(StorageFixture):
    def test_create_dedup_revision_and_paginated_complete_history(self):
        self.assertEqual(
            self.store.create_conversation(self.a, self.cid, "changed")["title"],
            "新聊天",
        )
        self.store.rename(self.scope, "kept", 1)
        self.assert_code(
            "CHAT_REVISION_CONFLICT", self.store.rename, self.scope, "lost", 1
        )
        ids = {self.cid}
        for _ in range(5):
            ids.add(self.store.create_conversation(self.a)["id"])
        page = self.store.list_conversations(self.a, limit=2)
        seen, total = set(), page["total"]
        newest = self.store.create_conversation(self.a)["id"]
        while True:
            seen.update(c["id"] for c in page["conversations"])
            if not page["has_more"]:
                break
            page = self.store.list_conversations(
                self.a, limit=2, cursor=page["next_cursor"]
            )
        self.assertEqual(seen, ids)
        self.assertNotIn(newest, seen)
        self.assertEqual(total, 6)
        self.assertEqual(self.store.list_conversations(self.a)["total"], 7)
        self.assert_code(
            "CHAT_INVALID_CURSOR", self.store.list_conversations, self.a, cursor="../xx"
        )

    def test_index_absent_broken_or_valid_but_missing_never_hides_chats(self):
        other = self.store.create_conversation(self.a)["id"]
        path = f"site-a/users/{self.a.user_id}/index.json"
        for value in (b"not-json", canonical_json({"v": 1, "entries": []})):
            self.store.fs.atomic_write(path, value)
            listing = self.store.list_conversations(self.a)
            self.assertEqual(
                {c["id"] for c in listing["conversations"]}, {self.cid, other}
            )
        self.store.fs.unlink(path)
        self.assertEqual(self.store.list_conversations(self.a)["total"], 2)
        self.assertEqual(len(self.store.fs.read_json(path)["entries"]), 2)

    def test_healthy_reads_do_not_take_writer_or_storage_locks(self):
        with (
            patch.object(
                self.store,
                "lease",
                side_effect=AssertionError("GET took a writer lock"),
            ),
            patch.object(
                self.store,
                "storage_lock",
                side_effect=AssertionError("GET rewrote a current cache"),
            ),
        ):
            self.assertEqual(self.store.get_conversation(self.scope)["id"], self.cid)
            self.assertEqual(self.store.read_events(self.scope)["until_seq"], 1)
            self.assertEqual(self.store.files(self.scope), [])
            self.assertEqual(self.store.list_conversations(self.a)["total"], 1)

    def test_events_paging_and_projection_reconstruction(self):
        for index in range(4):
            self.store.rename(self.scope, str(index), index + 1)
        self.store.fs.atomic_write(f"{self.scope.path}/meta.json", b"broken")
        self.store.fs.unlink(f"{self.scope.path}/files.json")
        self.store.fs.unlink(f"{self.scope.path}/runs.json")
        first = self.store.read_events(self.scope, limit=2)
        all_events = list(first["events"])
        while first["has_more"]:
            first = self.store.read_events(
                self.scope,
                after_seq=first["next_after_seq"],
                limit=2,
                until_seq=first["until_seq"],
            )
            all_events += first["events"]
        self.assertEqual([e["seq"] for e in all_events], [1, 2, 3, 4, 5])
        self.assertEqual(self.store.get_conversation(self.scope)["title"], "3")
        self.assert_code(
            "CHAT_INVALID_REQUEST", self.store.read_events, self.scope, until_seq=99
        )

    def test_upload_immutable_hash_bom_and_same_id_dedup(self):
        fid = str(uuid4())
        file = self.store.upload(self.scope, "abc.MD", b"\xef\xbb\xbfhello\n", fid)
        self.assertEqual(self.store.read_file(self.scope, fid)[1], b"hello\n")
        self.assertEqual(self.store.upload(self.scope, "abc.MD", b"hello\n", fid), file)
        self.assert_code(
            "CHAT_FILE_CONFLICT",
            self.store.upload,
            self.scope,
            "abc.MD",
            b"different",
            fid,
        )
        self.assert_code(
            "CHAT_FILE_TYPE_UNSUPPORTED",
            self.store.upload,
            self.scope,
            "a.zip",
            b"x",
            str(uuid4()),
        )
        self.assert_code(
            "CHAT_FILE_TYPE_UNSUPPORTED",
            self.store.upload,
            self.scope,
            "a.txt",
            b"\x00",
            str(uuid4()),
        )
        self.assert_code(
            "CHAT_INVALID_REQUEST",
            self.store.upload,
            self.scope,
            "../a.txt",
            b"x",
            str(uuid4()),
        )
        physical = Path(self.root, self.scope.path, "uploads", fid + ".blob")
        self.assertEqual(physical.stat().st_mode & 0o777, 0o600)
        physical.write_bytes(b"tampered")
        self.assert_code("CHAT_STORAGE_CORRUPT", self.store.read_file, self.scope, fid)

    def test_snapshot_commits_before_accepted_is_immutable_and_dedup_digest(self):
        with self.store.lease(self.scope, run=True) as lease:
            rid, data, _ = self.accept(lease)
            raw = self.store.fs.read_bytes(f"{self.scope.path}/contexts/{rid}.json")
            self.assertTrue(raw.endswith(b"\n"))
            self.assertEqual(
                hashlib.sha256(raw).hexdigest(), data["context_snapshot"]["sha256"]
            )
            payload = {
                **data,
                "context_revision": 0,
                "skill_ids": [],
                "expected_revision": 999,
            }
            self.assertEqual(
                self.store.find_request(
                    self.scope, data["client_request_id"], payload, lease=lease
                )["id"],
                rid,
            )
            self.assert_code(
                "CHAT_IDEMPOTENCY_CONFLICT",
                self.store.find_request,
                self.scope,
                data["client_request_id"],
                {**payload, "text": "changed"},
                lease=lease,
            )
            snapshot = self.snapshot(rid)
            snapshot["context_revision"] = 1
            self.assert_code(
                "CHAT_IDEMPOTENCY_CONFLICT",
                self.store.write_context,
                self.scope,
                rid,
                snapshot,
                lease=lease,
            )
            self.terminal(lease, rid)
        self.assertEqual(self.store.read_context(self.scope, rid)["run_id"], rid)
        self.assertEqual(self.store.get_run(self.scope, rid)["status"], "completed")

    def test_quota_includes_orphans_profiles_reservations_and_disk_floor(self):
        with patch.object(config, "USER_STORAGE_BYTES", 600000):
            self.store.fs.mkdir(f"site-a/users/{self.a.user_id}/profiles")
            self.store.fs.atomic_write(
                f"site-a/users/{self.a.user_id}/profiles/large", b"x" * 100000
            )
            self.assert_code(
                "CHAT_STORAGE_LIMIT",
                self.store.upload,
                self.scope,
                "a.txt",
                b"x" * 20000,
                str(uuid4()),
            )
        with patch.object(self.store.fs, "free_bytes", return_value=0):
            self.assert_code(
                "CHAT_STORAGE_FULL", self.store.rename, self.scope, "no", 1
            )
            self.assertEqual(self.store.list_conversations(self.a)["total"], 1)
        with self.store.lease(self.scope, run=True) as lease:
            reservation = self.store.reserve(
                self.scope, config.RUN_STORAGE_RESERVE_BYTES, lease=lease
            )
            self.assertGreater(reservation.remaining, 0)
            with patch.object(
                config, "USER_STORAGE_BYTES", config.RUN_STORAGE_RESERVE_BYTES + 100000
            ):
                self.assert_code(
                    "CHAT_STORAGE_LIMIT", self.store.reserve_user, self.a, 200000
                )
        self.assertEqual(self.store.fs.listdir("site-a/reservations"), [])

    def test_commit_failure_poisoned_cannot_append_even_terminal(self):
        with self.store.lease(self.scope) as lease:
            self.store._state(self.scope, lease=lease)
            original = self.store.fs.write_json

            def fail_commit(path, *args, **kwargs):
                if path.endswith("/commit.json"):
                    raise OSError(errno.ENOSPC, "synthetic")
                return original(path, *args, **kwargs)

            with patch.object(self.store.fs, "write_json", side_effect=fail_commit):
                self.assert_code(
                    "CHAT_STORAGE_FULL",
                    self.store.append,
                    self.scope,
                    "conversation.renamed",
                    {"title": "durable but unpublished"},
                    lease=lease,
                )
            self.assertTrue(lease.poisoned)
            self.assert_code(
                "CHAT_STORAGE_CORRUPT",
                self.store.append,
                self.scope,
                "conversation.renamed",
                {"title": "must not append"},
                lease=lease,
            )
            self.assertEqual(self.store.read_events(self.scope)["until_seq"], 1)
        self.assertEqual(
            self.store.get_conversation(self.scope)["title"], "durable but unpublished"
        )

    def test_projection_failure_keeps_one_committed_action(self):
        with self.store.lease(self.scope) as lease:
            self.store._state(self.scope, lease=lease)
            with patch.object(
                self.store,
                "_write_projections",
                side_effect=ChatError("synthetic", 507, "CHAT_STORAGE_FULL"),
            ):
                self.assert_code(
                    "CHAT_STORAGE_FULL",
                    self.store.append,
                    self.scope,
                    "conversation.renamed",
                    {"title": "kept"},
                    lease=lease,
                )
            self.assertFalse(lease.poisoned)
            events = self.store.read_events(self.scope)["events"]
            self.assertEqual(
                [e["type"] for e in events],
                ["conversation.created", "conversation.renamed"],
            )
        self.assertEqual(self.store.get_conversation(self.scope)["title"], "kept")

    def test_delete_tombstone_idempotent_cannot_reanimate_or_delete_settings(self):
        self.store.fs.mkdir(f"site-a/users/{self.a.user_id}/profiles")
        self.store.fs.atomic_write(
            f"site-a/users/{self.a.user_id}/profiles/kept", b"keep"
        )
        self.store.delete(self.scope, 1)
        self.store.delete(self.scope, 1)
        self.assertEqual(self.store.list_conversations(self.a)["total"], 0)
        self.assert_code(
            "CHAT_DELETED", self.store.create_conversation, self.a, self.cid
        )
        self.assert_code(
            "CHAT_NOT_FOUND", self.store.open_conversation, self.a, self.cid
        )
        self.assertEqual(
            self.store.fs.read_bytes(f"site-a/users/{self.a.user_id}/profiles/kept"),
            b"keep",
        )

    def test_terminal_budget_and_cancel_after_user_quota_is_full(self):
        with self.store.lease(self.scope, run=True) as lease:
            rid, _, _ = self.accept(lease)
            used = sum(
                len(canonical_json(e)) + 1
                for e in lease._state.events
                if e["run_id"] == rid
            )
            with patch.object(
                config,
                "RUN_EVENT_BYTES",
                used + config.EVENT_TERMINAL_RESERVE_BYTES + 20,
            ):
                self.assert_code(
                    "CHAT_STORAGE_LIMIT",
                    self.store.append,
                    self.scope,
                    "assistant.delta",
                    {"message_id": str(uuid4()), "text": "x" * 200},
                    run_id=rid,
                    lease=lease,
                )
                with patch.object(config, "USER_STORAGE_BYTES", 1):
                    self.assertTrue(
                        self.store.cancel(self.scope, rid)["cancel_requested"]
                    )
                    self.assertTrue(self.store.is_cancelled(self.scope, rid))
                self.terminal(lease, rid, "cancelled")
            # A new run must not observe a previous rid's sticky cancel flag.
            rid2, _, _ = self.accept(lease)
            self.assertFalse(self.store.is_cancelled(self.scope, rid2))
            self.terminal(lease, rid2)

    def test_upload_partial_disk_failure_never_reports_success_or_leaks_temp(self):
        with self.store.lease(self.scope) as lease:
            self.store._state(self.scope, lease=lease)
            fid = str(uuid4())
            with patch(
                "new_api_cockpit.features.chat.fs.SafeFS.write_all",
                side_effect=OSError(errno.ENOSPC, "synthetic"),
            ):
                self.assert_code(
                    "CHAT_STORAGE_FULL",
                    self.store.upload,
                    self.scope,
                    "failure.md",
                    b"not saved",
                    fid,
                    lease=lease,
                )
            self.assertEqual(self.store.files(self.scope), [])
            self.assertEqual(self.store.fs.listdir("site-a/reservations"), [])

    def test_live_file_and_snapshot_read_ignore_missing_stale_projections(self):
        with self.store.lease(self.scope, run=True) as lease:
            rid, _, _ = self.accept(lease)
            original = self.store._write_projections
            self.store._write_projections = lambda *a: None
            try:
                entry = self.store._add_file(
                    self.scope,
                    "out.md",
                    "body",
                    str(uuid4()),
                    "output",
                    lease=lease,
                    run_id=rid,
                )
                self.store.fs.unlink(f"{self.scope.path}/files.json")
                self.store.fs.unlink(f"{self.scope.path}/runs.json")
                self.assertEqual(
                    self.store.read_file(self.scope, entry["id"])[1], b"body"
                )
                self.assertEqual(
                    self.store.read_context(self.scope, rid)["run_id"], rid
                )
            finally:
                self.store._write_projections = original
            self.terminal(lease, rid)

    def test_short_writes_are_completed_and_synced(self):
        real_write = os.write

        def short(fd, data):
            return real_write(fd, data[: max(1, len(data) // 3)])

        with patch("new_api_cockpit.features.chat.fs.os.write", side_effect=short):
            result = self.store.upload(
                self.scope, "short.txt", b"abcdef" * 100, str(uuid4())
            )
        self.assertEqual(
            self.store.read_file(self.scope, result["id"])[1], b"abcdef" * 100
        )


if __name__ == "__main__":
    unittest.main()
