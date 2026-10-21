"""Real cross-process locks, SIGKILL, JSONL publication and crash recovery."""

import json
import errno
from unittest.mock import patch
import multiprocessing
import os
import time
from uuid import uuid4

from test_chat_storage import StorageFixture
from new_api_cockpit.features.chat.config import Settings
from new_api_cockpit.features.chat.errors import ChatError
from new_api_cockpit.features.chat.fs import canonical_json, utcnow
from new_api_cockpit.features.chat.locks import profile_lock
from new_api_cockpit.features.chat.storage import ChatStore
from new_api_cockpit.platform.identity import Principal


def contend(root, uid, cid, run, ready):
    store = ChatStore(Settings(data_dir=root, instance_id="site-a", enabled=True))
    try:
        scope = store.open_conversation(Principal(uid), cid)
        with store.lease(scope, run=run):
            ready.put("acquired")
    except ChatError as exc:
        ready.put(exc.code)
    finally:
        store.close()


def crash_writer(root, cid, ready):
    store = ChatStore(Settings(data_dir=root, instance_id="site-a", enabled=True))
    scope = store.open_conversation(Principal(42), cid)
    with store.lease(scope, run=True) as lease:
        store.reserve(scope, 16777216, lease=lease)
        # Fixture helper uses only store/scope/cid, not a running TestCase.
        fixture = StorageFixture()
        fixture.store, fixture.scope, fixture.cid = store, scope, cid
        rid, _, _ = fixture.accept(lease)
        store.append(
            scope,
            "run.dispatching",
            {"request_index": 1, "assistant_message_id": str(uuid4())},
            run_id=rid,
            lease=lease,
        )
        ready.put(rid)
        while True:
            time.sleep(1)


class ChatRecoveryTest(StorageFixture):
    def compete(self, uid, cid, run=False):
        ctx = multiprocessing.get_context("spawn")
        output = ctx.Queue()
        process = ctx.Process(target=contend, args=(self.root, uid, cid, run, output))
        process.start()
        process.join(10)
        if process.is_alive():
            process.kill()
            process.join()
            self.fail("lock acquisition blocked")
        self.assertEqual(process.exitcode, 0)
        result = output.get(timeout=2)
        output.close()
        output.join_thread()
        return result

    def test_conversation_and_user_leases_are_real_process_locks(self):
        cid2 = self.store.create_conversation(self.a)["id"]
        with self.store.lease(self.scope):
            self.assertEqual(
                self.compete(self.a.user_id, self.cid), "CHAT_CONVERSATION_BUSY"
            )
            self.assertEqual(self.compete(self.a.user_id, cid2), "acquired")
            # Read history immediately without waiting for the long lease.
            self.assertEqual(self.store.read_events(self.scope)["until_seq"], 1)
        with self.store.lease(self.scope, run=True):
            self.assertEqual(self.compete(self.a.user_id, cid2, True), "CHAT_USER_BUSY")
        self.assertEqual(self.compete(self.a.user_id, cid2, True), "acquired")

    def test_global_capacity_profile_lock_and_stable_lock_inodes(self):
        bcid = self.store.create_conversation(self.b)["id"]
        ccid = self.store.create_conversation(self.admin)["id"]
        bscope = self.store.open_conversation(self.b, bcid)
        with self.store.lease(self.scope, run=True), self.store.lease(bscope, run=True):
            self.assertEqual(
                self.compete(self.admin.user_id, ccid, True), "CHAT_CAPACITY_BUSY"
            )
        with profile_lock(self.store.fs, self.store.prefix, self.a.user_id):
            self.assert_code(
                "CHAT_PROFILE_BUSY",
                profile_lock,
                self.store.fs,
                self.store.prefix,
                self.a.user_id,
            )
        lockpath = f"site-a/.locks/conversations/{self.a.user_id}.{self.cid}.lock"
        inode = self.store.fs.stat(lockpath).st_ino
        self.store.delete(self.scope, 1)
        self.assertEqual(self.store.fs.stat(lockpath).st_ino, inode)

    def test_live_commit_high_water_does_not_expose_unpublished_complete_line(self):
        with self.store.lease(self.scope) as lease:
            self.store._state(self.scope, lease=lease)
            event = {
                "v": 1,
                "seq": 2,
                "type": "conversation.renamed",
                "conversation_id": self.cid,
                "run_id": None,
                "at": utcnow(),
                "data": {"title": "not-published"},
            }
            fd = self.store.fs.open_file(
                f"{self.scope.path}/events.jsonl", os.O_WRONLY | os.O_APPEND
            )
            os.write(fd, canonical_json(event) + b"\n")
            os.fsync(fd)
            os.close(fd)
            self.assertEqual(self.store.read_events(self.scope)["until_seq"], 1)
            self.assertEqual(self.store.get_conversation(self.scope)["title"], "新聊天")
        self.assertEqual(
            self.store.get_conversation(self.scope)["title"], "not-published"
        )

    def test_missing_commit_active_writer_returns_503_not_unpublished_events(self):
        with self.store.lease(self.scope):
            self.store.fs.unlink(f"{self.scope.path}/commit.json")
            self.assert_code(
                "CHAT_STORAGE_UNAVAILABLE", self.store.read_events, self.scope
            )
        self.assertEqual(self.store.read_events(self.scope)["until_seq"], 1)

    def test_bad_tail_quarantined_and_mid_log_corruption_never_truncated(self):
        path = f"{self.scope.path}/events.jsonl"
        with self.store.lease(self.scope):
            fd = self.store.fs.open_file(path, os.O_WRONLY | os.O_APPEND)
            os.write(fd, b'{"v":1,"seq":2')
            os.close(fd)
        self.assertEqual(self.store.read_events(self.scope)["until_seq"], 1)
        self.assertTrue(self.store.fs.listdir(f"{self.scope.path}/.quarantine"))
        self.store.rename(self.scope, "valid", 1)
        raw = self.store.fs.read_bytes(path)
        # A complete malformed line is corruption, not a tear we may silently drop.
        self.store.fs.atomic_write(path, raw + b'{"bad":true}\n')
        read = self.store.read_events(self.scope)
        self.assertEqual(read["storage_state"], "corrupt")
        self.assertEqual(read["until_seq"], 2)
        self.assertEqual(self.store.fs.read_bytes(path), raw + b'{"bad":true}\n')
        self.assert_code("CHAT_STORAGE_CORRUPT", self.store.rename, self.scope, "no", 2)
        self.assertEqual(self.store.list_conversations(self.a)["total"], 1)

    def test_bad_owner_seq_version_schema_and_first_line_stay_listed(self):
        path = f"{self.scope.path}/events.jsonl"
        original = self.store.fs.read_bytes(path)
        for mutate in (
            lambda e: e.update(v=2),
            lambda e: e.update(seq=9),
            lambda e: e["data"].update(owner_user_id=43),
            lambda e: e["data"].update(secret="nope"),
        ):
            event = json.loads(original)
            mutate(event)
            self.store.fs.atomic_write(path, canonical_json(event) + b"\n")
            result = self.store.list_conversations(self.a)
            self.assertEqual(result["total"], 1)
            self.assertEqual(result["conversations"][0]["storage_state"], "corrupt")
            self.assertIn(self.cid[:8], result["conversations"][0]["title"])
        self.store.fs.atomic_write(path, original)

    def test_orphan_blob_and_context_are_quarantined_not_listed_or_run(self):
        fid, rid = str(uuid4()), str(uuid4())
        self.store.fs.atomic_write(f"{self.scope.path}/uploads/{fid}.blob", b"orphan")
        self.store.fs.atomic_write(f"{self.scope.path}/contexts/{rid}.json", b"{}\n")
        self.assertEqual(self.store.files(self.scope), [])
        # Healthy GETs do not take writer leases. Startup (or the next writer)
        # performs orphan maintenance without ever exposing/activating them.
        self.store.recover()
        self.assert_code("CHAT_NOT_FOUND", self.store.read_context, self.scope, rid)
        self.assertGreaterEqual(
            len(self.store.fs.listdir(f"{self.scope.path}/.quarantine")), 2
        )
        self.assertEqual(self.store.get_conversation(self.scope)["message_count"], 0)

    def test_kill_writer_releases_locks_reclaims_reservation_and_interrupts_once(self):
        ctx = multiprocessing.get_context("spawn")
        output = ctx.Queue()
        process = ctx.Process(target=crash_writer, args=(self.root, self.cid, output))
        process.start()
        try:
            rid = output.get(timeout=10)
            self.assertEqual(self.store.get_run(self.scope, rid)["status"], "running")
            self.assertEqual(self.store.cancel(self.scope, rid)["status"], "running")
            self.assertTrue(self.store.is_cancelled(self.scope, rid))
            process.kill()
            process.join(5)
        finally:
            if process.is_alive():
                process.kill()
                process.join()
            output.close()
            output.join_thread()
        new = ChatStore(self.settings)
        try:
            scope = new.open_conversation(self.a, self.cid)
            run = new.get_run(scope, rid)
            self.assertEqual(run["status"], "interrupted")
            self.assertTrue(run["uncertain"])
            self.assertEqual(new.list_conversations(self.a)["total"], 1)
            events = new.read_events(scope)["events"]
            self.assertEqual(sum(e["type"] == "run.terminal" for e in events), 1)
            self.assertEqual(new.fs.listdir("site-a/reservations"), [])
            self.assertEqual(new.get_run(scope, rid)["status"], "interrupted")
        finally:
            new.close()

    def test_accepted_without_dispatch_recovered_uncertain_false(self):
        with self.store.lease(self.scope, run=True) as lease:
            rid, _, _ = self.accept(lease)
        run = self.store.get_run(self.scope, rid)
        self.assertEqual(run["status"], "interrupted")
        self.assertFalse(run["uncertain"])
        self.assertEqual(self.store.cancel(self.scope, rid)["status"], "interrupted")

    def test_partial_append_poison_never_concatenates_terminal_to_broken_tail(self):
        with self.store.lease(self.scope, run=True) as lease:
            rid, _, _ = self.accept(lease)
            self.store.append(
                self.scope,
                "run.dispatching",
                {"request_index": 1, "assistant_message_id": str(uuid4())},
                run_id=rid,
                lease=lease,
            )
            ino = self.store.fs.stat(f"{self.scope.path}/events.jsonl").st_ino
            write = os.write

            def tear(fd, data):
                if os.fstat(fd).st_ino == ino:
                    write(fd, data[:10])
                    raise OSError(errno.ENOSPC, "synthetic")
                return write(fd, data)

            with patch("new_api_cockpit.features.chat.fs.os.write", side_effect=tear):
                self.assert_code(
                    "CHAT_STORAGE_FULL",
                    self.store.append,
                    self.scope,
                    "assistant.delta",
                    {"message_id": str(uuid4()), "text": "partial"},
                    run_id=rid,
                    lease=lease,
                )
            self.assertTrue(lease.poisoned)
            self.assert_code("CHAT_STORAGE_CORRUPT", self.terminal, lease, rid)
            self.assertEqual(self.store.get_run(self.scope, rid)["status"], "running")
        result = self.store.get_run(self.scope, rid)
        self.assertEqual(result["status"], "interrupted")
        events = self.store.read_events(self.scope)["events"]
        self.assertEqual([e["seq"] for e in events], list(range(1, len(events) + 1)))

    def test_failed_commit_recovery_without_space_never_publishes_new_bytes(self):
        with self.store.lease(self.scope):
            path = f"{self.scope.path}/events.jsonl"
            raw = self.store.fs.read_bytes(path)
            event = {
                "v": 1,
                "seq": 2,
                "type": "conversation.renamed",
                "conversation_id": self.cid,
                "run_id": None,
                "at": utcnow(),
                "data": {"title": "uncommitted"},
            }
            self.store.fs.atomic_write(path, raw + canonical_json(event) + b"\n")
        with patch.object(self.store.fs, "free_bytes", return_value=0):
            page = self.store.read_events(self.scope)
            self.assertEqual(page["until_seq"], 1)
            self.assertEqual(page["events"][0]["type"], "conversation.created")
        self.assertEqual(self.store.read_events(self.scope)["until_seq"], 2)

    def test_missing_blob_directory_does_not_hide_the_conversation(self):
        self.store.fs.remove_tree(f"{self.scope.path}/uploads")
        self.assertEqual(self.store.list_conversations(self.a)["total"], 1)

    def test_delete_crash_after_rename_creates_tombstone_on_restart(self):
        root = f"site-a/users/{self.a.user_id}"
        self.store.fs.rename(self.scope.path, f"{root}/.trash/{self.cid}")
        new = ChatStore(self.settings)
        try:
            self.assertEqual(new.list_conversations(self.a)["total"], 0)
            self.assertTrue(new.fs.exists(f"{root}/deleted/{self.cid}.json"))
            self.assertFalse(new.fs.exists(f"{root}/.trash/{self.cid}"))
            self.assert_code("CHAT_DELETED", new.create_conversation, self.a, self.cid)
        finally:
            new.close()
