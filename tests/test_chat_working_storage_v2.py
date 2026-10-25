"""Working contexts are committed immutable owner-scoped blobs, never UI history."""

from new_api_cockpit.features.chat.errors import ChatError
from new_api_cockpit.features.chat.storage import ChatStore
from test_chat_storage import StorageFixture


class WorkingStorageV2Test(StorageFixture):
    def publish(self, lease, rid, reference):
        self.store.append(
            self.scope,
            "context.compacted",
            {
                "mode": "summary",
                "covered_run_ids": [],
                "source_sha256": "a" * 64,
                "checkpoint": reference,
            },
            run_id=rid,
            lease=lease,
        )

    def test_whole_window_survives_reopen_and_canonical_user_text_unchanged(self):
        checkpoint = {
            "protocol": "responses",
            "window": [
                {"role": "user", "content": "旧问题"},
                {"type": "compaction", "encrypted_content": "opaque"},
            ],
        }
        with self.store.lease(self.scope) as lease:
            rid, accepted, _ = self.accept(lease)
            reference = self.store.write_working_context(
                self.scope, rid, checkpoint, lease=lease
            )
            with self.assertRaises(ChatError):
                self.store.read_working_context(self.scope, reference, lease=lease)
            self.publish(lease, rid, reference)
            self.terminal(lease, rid)
        self.assertEqual(
            self.store.read_working_context(self.scope, reference), checkpoint
        )
        other = ChatStore(self.settings)
        try:
            scope = other.open_conversation(self.a, self.cid)
            self.assertEqual(other.read_working_context(scope, reference), checkpoint)
            self.assertEqual(
                next(
                    e["data"]["text"]
                    for e in other.read_events(scope)["events"]
                    if e["type"] == "run.accepted"
                ),
                accepted["text"],
            )
            self.assertTrue(other.fs.exists(f"{scope.path}/{reference['file']}"))
        finally:
            other.close()

    def test_orphan_never_published_or_replayed_recovery_quarantines(self):
        with self.store.lease(self.scope) as lease:
            rid, _, _ = self.accept(lease)
            reference = self.store.write_working_context(
                self.scope, rid, {"summary": "uncommitted"}, lease=lease
            )
            self.terminal(lease, rid)
        with self.assertRaises(ChatError):
            self.store.read_working_context(self.scope, reference)
        self.store.recover()
        self.assertFalse(self.store.fs.exists(f"{self.scope.path}/{reference['file']}"))
        self.assertTrue(self.store.fs.listdir(f"{self.scope.path}/.quarantine"))

    def test_cross_owner_cross_conversation_and_path_forgery_rejected(self):
        with self.store.lease(self.scope) as lease:
            rid, _, _ = self.accept(lease)
            reference = self.store.write_working_context(
                self.scope, rid, {"summary": "owner-private"}, lease=lease
            )
            self.publish(lease, rid, reference)
            self.terminal(lease, rid)
        for principal in (self.b, self.admin):
            other = self.store.create_conversation(principal)["id"]
            with self.assertRaises(ChatError):
                self.store.read_working_context(
                    self.store.open_conversation(principal, other), reference
                )
        same = self.store.create_conversation(self.a)["id"]
        with self.assertRaises(ChatError):
            self.store.read_working_context(
                self.store.open_conversation(self.a, same), reference
            )
        with self.assertRaises(ChatError):
            self.store.read_working_context(
                self.scope, {**reference, "file": "../elsewhere"}
            )

    def test_tamper_missing_and_terminal_cannot_write_new_working_file(self):
        with self.store.lease(self.scope) as lease:
            rid, _, _ = self.accept(lease)
            reference = self.store.write_working_context(
                self.scope, rid, {"summary": "valid"}, lease=lease
            )
            self.publish(lease, rid, reference)
            self.terminal(lease, rid)
            with self.assertRaises(ChatError):
                self.store.write_working_context(
                    self.scope, rid, {"summary": "late"}, lease=lease
                )
        self.store.fs.atomic_write(f"{self.scope.path}/{reference['file']}", b"{}")
        with self.assertRaises(ChatError) as changed:
            self.store.read_working_context(self.scope, reference)
        self.assertEqual(changed.exception.code, "CHAT_STORAGE_CORRUPT")
        self.store.fs.unlink(f"{self.scope.path}/{reference['file']}")
        with self.assertRaises(ChatError) as missing:
            self.store.read_working_context(self.scope, reference)
        self.assertEqual(missing.exception.code, "CHAT_STORAGE_CORRUPT")
