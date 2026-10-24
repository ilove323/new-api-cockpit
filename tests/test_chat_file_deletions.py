"""Real blob deletion and immutable history, including live-run races. No network."""

import errno
import json
import tempfile
import time
import unittest
from unittest.mock import patch
from uuid import uuid4

from chat_fixtures import access, body, create, make_service
from test_chat_storage import StorageFixture
from new_api_cockpit.features.chat import attachments, file_deletions, tools
from new_api_cockpit.features.chat.storage import ChatStore
from new_api_cockpit.features.chat.workspace import Workspace
from new_api_cockpit.features.chat.errors import ChatError


class FileDeletionStorageTest(StorageFixture):
    def upload(self):
        fid = str(uuid4())
        return self.store.upload(
            self.scope, "private.md", b"PRIVATE_ATTACHMENT_" * 1000, fid
        )

    def test_workers_wait_for_short_startup_recovery_lock_instead_of_failing_boot(self):
        import threading
        from new_api_cockpit.features.chat.locks import FileLock

        result = []

        def initialize():
            try:
                result.append(ChatStore(self.settings))
            except Exception as error:
                result.append(error)

        with FileLock(self.store.fs, self.store.prefix + "/.locks/startup.lock"):
            thread = threading.Thread(target=initialize)
            thread.start()
            time.sleep(0.05)
            self.assertTrue(thread.is_alive())
            self.assertEqual(result, [])
        thread.join(3)
        self.assertFalse(thread.is_alive())
        self.assertEqual(len(result), 1)
        self.assertIsInstance(result[0], ChatStore)
        result[0].close()

    def test_unlink_is_real_idempotent_and_event_history_unchanged(self):
        entry = self.upload()
        before = self.store.fs.read_bytes(self.scope.path + "/events.jsonl", 100000)
        used = self.store.fs.disk_usage(self.scope.path)
        deleted = self.store.delete_upload(self.scope, entry["id"])
        self.assertTrue(deleted["deleted_at"])
        self.assertFalse(
            self.store.fs.exists(f"{self.scope.path}/uploads/{entry['id']}.blob")
        )
        self.assertLess(self.store.fs.disk_usage(self.scope.path), used)
        self.assertEqual(self.store.delete_upload(self.scope, entry["id"]), deleted)
        self.assertEqual(
            self.store.fs.read_bytes(self.scope.path + "/events.jsonl", 100000), before
        )
        self.assertNotIn(
            "deleted_at", self.store._published(self.scope).events[-1]["data"]["file"]
        )
        self.assert_code(
            "CHAT_FILE_DELETED", self.store.read_file, self.scope, entry["id"]
        )
        self.assert_code(
            "CHAT_FILE_DELETED",
            self.store.upload,
            self.scope,
            entry["name"],
            b"PRIVATE_ATTACHMENT_" * 1000,
            entry["id"],
        )
        ws = Workspace(self.store, self.scope)
        self.assertEqual(ws.list_files()["files"], [])
        self.assertEqual(ws.search_files("PRIVATE")["matches"], [])
        result = tools.execute(
            "read_file",
            {"file_id": entry["id"], "start_line": 1, "max_lines": 10},
            ws,
            {"run_id": str(uuid4()), "call_id": "read-deleted"},
        )
        self.assertEqual(result["code"], "TOOL_FILE_DELETED")
        self.assertEqual(self.store.get_conversation(self.scope)["storage_state"], "ok")

    def test_cross_owner_admin_cross_chat_and_output_deletion_denied(self):
        entry = self.upload()
        for principal in (self.b, self.admin):
            self.assert_code(
                "CHAT_NOT_FOUND",
                self.store.delete_upload,
                self.store._scope(principal, self.cid),
                entry["id"],
            )
        other = self.store.open_conversation(
            self.a, self.store.create_conversation(self.a)["id"]
        )
        self.assert_code("CHAT_NOT_FOUND", self.store.delete_upload, other, entry["id"])
        with self.store.lease(self.scope, run=True) as lease:
            rid, _, _ = self.accept(lease)
            output = Workspace(self.store, self.scope, lease, rid).write_file(
                "output.txt", "keep", "write"
            )
            self.assert_code(
                "CHAT_INVALID_REQUEST",
                self.store.delete_upload,
                self.scope,
                output["file_id"],
            )
            self.terminal(lease, rid)
        self.assertEqual(
            self.store.read_file(self.scope, entry["id"])[1],
            b"PRIVATE_ATTACHMENT_" * 1000,
        )
        self.assertEqual(
            self.store.read_file(self.scope, output["file_id"])[1], b"keep"
        )

    def test_committed_marker_survives_failed_unlink_restart_finishes_purge(self):
        entry = self.upload()
        with patch.object(
            self.store.fs, "unlink", side_effect=OSError(errno.EIO, "synthetic failure")
        ):
            with self.assertRaises(ChatError):
                self.store.delete_upload(self.scope, entry["id"])
        self.assertIn(entry["id"], file_deletions.records(self.store, self.scope))
        self.assertTrue(
            self.store.fs.exists(f"{self.scope.path}/uploads/{entry['id']}.blob")
        )
        self.store.close()
        self.store = ChatStore(self.settings)
        self.addCleanup(self.store.close)
        self.scope = self.store.open_conversation(self.a, self.cid)
        self.assertFalse(
            self.store.fs.exists(f"{self.scope.path}/uploads/{entry['id']}.blob")
        )
        self.assertEqual(self.store.get_conversation(self.scope)["storage_state"], "ok")
        self.assertTrue(self.store.files(self.scope)[0]["deleted_at"])

    def test_next_read_finishes_an_interrupted_delete_before_exposing_any_original(
        self,
    ):
        entry = self.upload()
        with patch.object(
            self.store.fs, "unlink", side_effect=OSError(errno.EIO, "failure")
        ):
            with self.assertRaises(ChatError):
                self.store.delete_upload(self.scope, entry["id"])
        self.assert_code(
            "CHAT_FILE_DELETED", self.store.read_file, self.scope, entry["id"]
        )
        self.assertFalse(
            self.store.fs.exists(f"{self.scope.path}/uploads/{entry['id']}.blob")
        )

    def test_tampered_marker_fails_closed_and_never_unlinks_other_blob(self):
        entry = self.upload()
        self.store.delete_upload(self.scope, entry["id"])
        path = f"{self.scope.path}/control/{file_deletions.PREFIX}{entry['id']}.json"
        marker = self.store.fs.read_json(path)
        marker["sha256"] = "0" * 64
        self.store.fs.write_json(path, marker)
        self.assert_code("CHAT_STORAGE_CORRUPT", self.store.files, self.scope)

    def test_old_published_prefix_ignores_marker_for_a_later_file_without_false_corruption(
        self,
    ):
        old = self.store._published(self.scope)
        entry = self.upload()
        self.store.delete_upload(self.scope, entry["id"])
        self.assertEqual(self.store._projection(self.scope, old)[1], {})


class FileDeletionRunTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.service, self.tokens, self.stream = make_service(self.tmp.name)
        self.actor = access()
        self.cid = create(self.service, self.actor)
        self.fid = str(uuid4())
        self.secret = "UNIQUE_DELETED_ORIGINAL_BODY_12345"
        self.service.upload(
            self.actor.principal, self.cid, self.fid, "report.md", self.secret.encode()
        )

    def tearDown(self):
        self.stream.release.set()
        self.service.shutdown()
        self.service.store.close()
        self.tmp.cleanup()

    def turn(self, **changes):
        active = self.service.accept_run(
            self.actor, self.cid, body(self.service, self.actor, self.cid, **changes)
        )
        list(self.service.stream(active))
        self.assertEqual(
            self.service.get_run(self.actor.principal, self.cid, active.run_id)[
                "status"
            ],
            "completed",
        )
        return active

    def test_delete_previously_referenced_attachment_keeps_chat_usable_and_does_not_rehydrate(
        self,
    ):
        self.turn(attachment_ids=[self.fid])
        before = len(self.stream.calls)
        self.service.delete_upload(self.actor.principal, self.cid, self.fid)
        self.assertEqual(len(self.stream.calls), before)
        with patch.object(
            self.service.store,
            "read_file",
            side_effect=AssertionError("deleted source must not be reread"),
        ):
            self.turn(text="继续正常聊天", mode="files", context_strategy="adaptive")
        payload = json.dumps(self.stream.calls[-1], ensure_ascii=False)
        self.assertNotIn(self.secret, payload)
        self.assertIn("附件已删除", payload)
        with self.assertRaises(ChatError) as error:
            self.service.accept_run(
                self.actor,
                self.cid,
                body(self.service, self.actor, self.cid, attachment_ids=[self.fid]),
            )
        self.assertEqual(error.exception.code, "CHAT_FILE_DELETED")
        self.assertEqual(len(self.stream.calls), before + 1)

    def test_deletion_during_live_model_request_does_not_wait_for_long_run_lease(self):
        self.stream.mode = "blocked"
        active = self.service.accept_run(
            self.actor,
            self.cid,
            body(self.service, self.actor, self.cid, attachment_ids=[self.fid]),
        )
        self.assertTrue(self.stream.entered.wait(2))
        start = time.monotonic()
        deleted = self.service.delete_upload(self.actor.principal, self.cid, self.fid)
        self.assertLess(time.monotonic() - start, 2)
        self.assertTrue(deleted["deleted_at"])
        self.assertFalse(active.done.is_set())
        with self.assertRaises(ChatError) as error:
            self.service.download(self.actor.principal, self.cid, self.fid)
        self.assertEqual(error.exception.status, 410)
        self.stream.release.set()
        list(self.service.stream(active))
        self.assertEqual(
            self.service.get_run(self.actor.principal, self.cid, active.run_id)[
                "status"
            ],
            "completed",
        )
        self.stream.mode = "text"
        self.turn()
        self.assertNotIn(
            self.secret, json.dumps(self.stream.calls[-1], ensure_ascii=False)
        )

    def test_delete_during_preparation_rejects_acceptance_before_dispatch(self):
        original = attachments.read_texts

        def race(*args, **kwargs):
            value = original(*args, **kwargs)
            self.service.delete_upload(self.actor.principal, self.cid, self.fid)
            return value

        with (
            patch.object(attachments, "read_texts", side_effect=race),
            self.assertRaises(ChatError) as error,
        ):
            self.service.accept_run(
                self.actor,
                self.cid,
                body(self.service, self.actor, self.cid, attachment_ids=[self.fid]),
            )
        self.assertEqual(error.exception.code, "CHAT_ATTACHMENTS_CHANGED")
        self.assertEqual(self.stream.calls, [])
        self.assertIsNone(
            self.service.get_conversation(self.actor.principal, self.cid)[
                "active_run_id"
            ]
        )
