"""Storage hot paths use real journals and the same durability controls."""

import copy
import os
from unittest.mock import patch
from uuid import uuid4

from test_chat_storage import StorageFixture
from new_api_cockpit.features.chat import recovery
from new_api_cockpit.features.chat.errors import ChatError


class ChatStorageOptimizationTest(StorageFixture):
    def test_unchanged_snapshot_does_not_read_or_replay_the_journal(self):
        first = self.store.sync(self.scope)
        read = self.store.fs.read_bytes

        def bounded_read(path, *args, **kwargs):
            if path.endswith("/events.jsonl"):
                self.fail("unchanged poll reread the journal")
            return read(path, *args, **kwargs)

        with (
            patch.object(self.store.fs, "read_bytes", side_effect=bounded_read),
            patch(
                "new_api_cockpit.features.chat.storage.scan",
                side_effect=AssertionError("replay"),
            ),
        ):
            for _ in range(3):
                value = self.store.sync(
                    self.scope, 1, files_revision=first["files_revision"]
                )
                self.assertEqual(value["events"]["events"], [])
                self.assertIsNone(value["files"])
        # Authorization and tombstones remain outside the journal cache.
        self.assert_code(
            "CHAT_NOT_FOUND", self.store.open_conversation, self.b, self.cid
        )
        self.store.delete(self.scope, 1)
        self.assert_code("CHAT_NOT_FOUND", self.store.sync, self.scope)

    def test_same_length_replacement_and_permission_changes_invalidate_cache(self):
        self.store.rename(self.scope, "first", 1)
        self.assertEqual(self.store.sync(self.scope)["conversation"]["title"], "first")
        path = f"{self.scope.path}/events.jsonl"
        info = self.store.fs.stat(path)
        raw = self.store.fs.read_bytes(path).replace(b'"first"', b'"other"')
        self.store.fs.atomic_write(path, raw)
        absolute = os.path.join(self.root, path)
        os.utime(absolute, ns=(info.st_atime_ns, info.st_mtime_ns))
        self.assertEqual(self.store.sync(self.scope)["conversation"]["title"], "other")
        os.chmod(absolute, 0o644)
        try:
            self.assert_code("CHAT_STORAGE_UNAVAILABLE", self.store.sync, self.scope)
        finally:
            os.chmod(absolute, 0o600)

    def test_response_mutation_cannot_poison_a_cached_snapshot(self):
        with self.store.lease(self.scope) as lease:
            rid, _, _ = self.accept(lease)
            self.terminal(lease, rid)
        first = self.store.sync(self.scope)
        first["events"]["events"][0]["data"]["title"] = "poison"
        first["conversation"]["loaded_skill_ids"].append(str(uuid4()))
        first["run"]["context_snapshot"]["skill_ids"].append(str(uuid4()))
        value = self.store.sync(self.scope)
        self.assertEqual(value["events"]["events"][0]["data"]["title"], "新聊天")
        self.assertEqual(value["conversation"]["loaded_skill_ids"], [])
        self.assertEqual(value["run"]["context_snapshot"]["skill_ids"], [])

    def test_incremental_append_matches_replay_without_directory_walks(self):
        with self.store.lease(self.scope, run=True) as lease:
            self.store.reserve(self.scope, 16777216, lease=lease)
            rid, _, _ = self.accept(lease)
            message = str(uuid4())
            with patch.object(
                self.store.fs,
                "disk_usage",
                side_effect=AssertionError("full directory walk"),
            ):
                self.store.append(
                    self.scope,
                    "run.dispatching",
                    {"request_index": 1, "assistant_message_id": message},
                    rid,
                    lease=lease,
                )
                write_json = self.store.fs.write_json
                with patch.object(
                    self.store.fs, "write_json", wraps=write_json
                ) as writes:
                    for index in range(8):
                        self.store.append(
                            self.scope,
                            "assistant.delta",
                            {"message_id": message, "text": str(index)},
                            rid,
                            lease=lease,
                        )
                written = [call.args[0] for call in writes.call_args_list]
                self.assertFalse(
                    any(
                        path.endswith(("/files.json", "/runs.json", "/index.json"))
                        for path in written
                    )
                )
                state = lease._state
                expected = recovery.project(self.scope, state.events)
                self.assertEqual(state.projection.value(), expected)
                before = copy.deepcopy(state.projection.value())
                seq = state.seq
                with self.assertRaises(ChatError):
                    self.store.append(
                        self.scope,
                        "run.dispatching",
                        {"request_index": 9, "assistant_message_id": message},
                        rid,
                        lease=lease,
                    )
                self.assertEqual(state.projection.value(), before)
                self.assertEqual(state.seq, seq)
                self.terminal(lease, rid)
                self.assertEqual(
                    state.projection.value(), recovery.project(self.scope, state.events)
                )

    def test_warm_cache_still_observes_unpublished_tail(self):
        self.store.sync(self.scope)
        path = f"{self.scope.path}/events.jsonl"
        with self.store.lease(self.scope):
            fd = self.store.fs.open_file(path, os.O_WRONLY | os.O_APPEND)
            try:
                os.write(fd, b'{"torn":')
                os.fsync(fd)
            finally:
                os.close(fd)
            self.assertEqual(self.store.sync(self.scope)["events"]["until_seq"], 1)
        self.assertEqual(self.store.sync(self.scope)["events"]["until_seq"], 1)
        self.assertTrue(self.store.fs.listdir(f"{self.scope.path}/.quarantine"))
