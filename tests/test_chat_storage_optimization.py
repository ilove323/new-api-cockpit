"""Storage hot paths use real journals and the same durability controls."""

import copy
import os
from unittest.mock import patch
from uuid import uuid4

from test_chat_storage import StorageFixture
from new_api_cockpit.features.chat import recovery
from new_api_cockpit.features.chat.errors import ChatError
from new_api_cockpit.features.chat import config
from new_api_cockpit.features.chat.event_index import EventIndex
from new_api_cockpit.features.chat.fs import canonical_json


class ChatStorageOptimizationTest(StorageFixture):
    def test_context_lookup_still_reads_valid_prefix_when_recovery_finds_corruption(
        self,
    ):
        with self.store.lease(self.scope) as lease:
            rid, _, _ = self.accept(lease)
            terminal = self.terminal(lease, rid)
        invalid = {
            **terminal,
            "seq": terminal["seq"] + 1,
            "type": "assistant.delta",
            "data": {"message_id": str(uuid4()), "text": "after terminal"},
        }
        path = f"{self.scope.path}/events.jsonl"
        self.store.fs.atomic_write(
            path, self.store.fs.read_bytes(path) + canonical_json(invalid) + b"\n"
        )
        self.store.fs.unlink(f"{self.scope.path}/commit.json")
        self.assertEqual(self.store.read_context(self.scope, rid)["run_id"], rid)

    def large_journal(self):
        messages = {}
        for _ in range(4):
            with self.store.lease(self.scope) as lease:
                rid, _, _ = self.accept(lease)
                messages[rid] = str(uuid4())
                self.store.append(
                    self.scope,
                    "run.dispatching",
                    {"request_index": 1, "assistant_message_id": messages[rid]},
                    rid,
                    lease=lease,
                )
                self.terminal(lease, rid)
        base = self.store.read_events(self.scope)["events"]
        events = []
        for event in base:
            if event["type"] == "run.terminal":
                for _ in range(13):
                    events.append(
                        {
                            **event,
                            "type": "assistant.delta",
                            "seq": len(events) + 1,
                            "data": {
                                "message_id": messages[event["run_id"]],
                                "text": "x" * 200000,
                            },
                        }
                    )
            events.append({**event, "seq": len(events) + 1})
        raw = b"".join(canonical_json(event) + b"\n" for event in events)
        with self.store.lease(self.scope):
            self.store.fs.atomic_write(f"{self.scope.path}/events.jsonl", raw)
            self.store.fs.write_json(
                f"{self.scope.path}/commit.json",
                {"v": 1, "seq": len(events), "byte_offset": len(raw)},
            )
        self.assertGreater(len(raw), config.READ_CACHE_BYTES)
        return events, raw

    def test_large_history_keeps_only_offsets_and_reads_only_requested_blocks(self):
        events, raw = self.large_journal()
        first = self.store.sync(self.scope, len(events))
        cached = self.store._read_cache[(self.a.user_id, self.cid)]
        self.assertIsInstance(cached[1].events, EventIndex)
        self.assertLess(cached[2], config.READ_CACHE_BYTES)
        self.assertLess(cached[2], len(raw) // 10)
        with patch(
            "new_api_cockpit.features.chat.storage.scan",
            side_effect=AssertionError("unchanged large journal replayed"),
        ):
            with patch(
                "new_api_cockpit.features.chat.event_index.os.pread", wraps=os.pread
            ) as read:
                page = self.store.read_events(self.scope, after_seq=5, limit=2)
                self.assertEqual(page["events"], events[5:7])
                self.assertLess(
                    sum(call.args[1] for call in read.call_args_list), len(raw) // 4
                )
            with patch(
                "new_api_cockpit.features.chat.event_index.os.pread",
                side_effect=AssertionError("empty poll read a journal block"),
            ):
                value = self.store.sync(
                    self.scope, len(events), files_revision=first["files_revision"]
                )
                self.assertEqual(value["events"]["events"], [])
                self.assertIsNone(value["files"])
                self.store.read_context(self.scope, events[1]["run_id"])

    def test_index_pins_its_prefix_and_rejects_changed_block_bytes(self):
        self.store.sync(self.scope)
        indexed = self.store._read_cache[(self.a.user_id, self.cid)][1].events
        self.assertIsInstance(indexed, EventIndex)
        path = f"{self.scope.path}/events.jsonl"
        with self.store.lease(self.scope):
            fd = self.store.fs.open_file(path, os.O_RDWR)
            try:
                os.lseek(fd, 0, os.SEEK_END)
                os.write(fd, b"unpublished tail")
                self.assertEqual(indexed[0]["seq"], 1)
                # In-place overwrite after snapshot lookup must not return
                # unchecked bytes, even when offsets/length remain valid.
                os.pwrite(fd, b"X", 0)
                self.assertEqual(os.pread(fd, 1, 0), b"X")
                self.assert_code("CHAT_STORAGE_UNAVAILABLE", lambda: indexed[0])
            finally:
                os.close(fd)

    def test_index_eviction_and_store_close_release_source_descriptors(self):
        self.store.sync(self.scope)
        fd = self.store._read_cache[(self.a.user_id, self.cid)][1].events.fd
        with patch.object(config, "READ_CACHE_ENTRIES", 1):
            cid = self.store.create_conversation(self.a)["id"]
            self.store.sync(self.store.open_conversation(self.a, cid))
        with self.assertRaises(OSError):
            os.fstat(fd)
        active = next(iter(self.store._read_cache.values()))[1].events.fd
        self.store._read_cache.clear()
        with self.assertRaises(OSError):
            os.fstat(active)

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
