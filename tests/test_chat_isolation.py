"""Owner identity, namespace and no-follow filesystem adversarial tests."""

from dataclasses import replace
import os
from pathlib import Path
from uuid import uuid4

from test_chat_storage import StorageFixture
from new_api_cockpit.features.chat.errors import ChatError
from new_api_cockpit.features.chat.fs import SafeFS
from new_api_cockpit.platform.identity import Principal


class ChatIsolationTest(StorageFixture):
    def test_A_B_and_admin_do_not_share_conversation_or_file_ids(self):
        fid = str(uuid4())
        self.store.upload(self.scope, "private.md", b"A-private", fid)
        for principal in (self.b, self.admin):
            self.assert_code(
                "CHAT_NOT_FOUND", self.store.open_conversation, principal, self.cid
            )
            self.assertEqual(self.store.list_conversations(principal)["total"], 0)
            self.store.create_conversation(principal, self.cid, "own-empty")
            own = self.store.open_conversation(principal, self.cid)
            self.assert_code("CHAT_NOT_FOUND", self.store.read_file, own, fid)
            self.assertEqual(self.store.get_conversation(own)["title"], "own-empty")
        self.assertEqual(self.store.read_file(self.scope, fid)[1], b"A-private")

    def test_files_are_conversation_local_and_scope_cannot_switch_store(self):
        fid = str(uuid4())
        self.store.upload(self.scope, "a.txt", b"local", fid)
        other_id = self.store.create_conversation(self.a)["id"]
        other = self.store.open_conversation(self.a, other_id)
        self.assert_code("CHAT_NOT_FOUND", self.store.read_file, other, fid)
        forged = replace(self.scope, instance_id="other")
        self.assert_code("CHAT_NOT_FOUND", self.store.read_events, forged)
        self.assert_code(
            "CHAT_SESSION_REQUIRED",
            self.store.list_conversations,
            Principal(42, auth_kind="pat"),
        )
        same_user = Principal(42, "renamed", role=100, session_id="new-session")
        self.assertEqual(
            self.store.read_file(
                self.store.open_conversation(same_user, self.cid), fid
            )[1],
            b"local",
        )

    def test_malformed_identifiers_and_encoded_traversal_are_rejected(self):
        for value in (
            "..",
            "../42",
            "x/y",
            "x\\y",
            "%2e%2e",
            "\x00",
            str(uuid4()).upper(),
        ):
            self.assert_code(
                "CHAT_INVALID_REQUEST", self.store.open_conversation, self.a, value
            )
            if value in {"..", "../42", "x\\y", "%2e%2e", "\x00"}:
                with self.assertRaises(ChatError):
                    self.store.fs.read_bytes(value)

    def test_symlink_on_blob_directory_root_or_index_never_follows(self):
        outside = Path(self.root, "outside.txt")
        outside.write_bytes(b"outside-secret")
        fid = str(uuid4())
        entry = self.store.upload(self.scope, "a.txt", b"inside", fid)
        blob = Path(self.root, self.scope.path, "uploads", entry["id"] + ".blob")
        blob.unlink()
        blob.symlink_to(outside)
        self.assert_code(
            "CHAT_STORAGE_UNAVAILABLE", self.store.read_file, self.scope, fid
        )
        self.assertEqual(outside.read_bytes(), b"outside-secret")
        with self.assertRaises(ChatError):
            self.store.fs.atomic_write(f"{self.scope.path}/uploads/{fid}.blob", b"bad")
        linkroot = Path(self.root, "linkroot")
        linkroot.symlink_to(Path(self.root, "site-a"), target_is_directory=True)
        with self.assertRaises(ChatError):
            SafeFS(str(linkroot))
        index = Path(self.root, "site-a", "users", str(self.a.user_id), "index.json")
        index.unlink(missing_ok=True)
        index.symlink_to(outside)
        # Derived index is ignored rather than read, and must not clobber outside.
        self.assertEqual(self.store.list_conversations(self.a)["total"], 1)
        self.assertEqual(outside.read_bytes(), b"outside-secret")

    def test_hardlink_and_fifo_refused_without_mutation_or_blocking(self):
        outside = Path(self.root, "outside.txt")
        outside.write_bytes(b"secret")
        linked = Path(self.root, self.scope.path, "uploads", "linked")
        os.link(outside, linked)
        for operation in (
            lambda: self.store.fs.read_bytes(f"{self.scope.path}/uploads/linked"),
            lambda: self.store.fs.atomic_write(
                f"{self.scope.path}/uploads/linked", b"bad"
            ),
            lambda: self.store.fs.open_file(
                f"{self.scope.path}/uploads/linked", os.O_WRONLY | os.O_TRUNC
            ),
        ):
            with self.assertRaises(ChatError):
                operation()
        self.assertEqual(outside.read_bytes(), b"secret")
        linked.unlink()
        fifo = Path(self.root, self.scope.path, "uploads", "fifo")
        os.mkfifo(fifo)
        with self.assertRaises(ChatError):
            self.store.fs.read_bytes(f"{self.scope.path}/uploads/fifo")
