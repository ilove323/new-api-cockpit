"""Independent isolation review: synthetic identities, temp disk, no network.

Stored records are deliberately adversarial. Run IDs are scoped by owner/cid,
so a valid record in one namespace must not control a live run in another.
"""

from dataclasses import replace
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

from chat_fixtures import access, body, create, make_service
from new_api_cockpit.features.chat.errors import ChatError
from new_api_cockpit.features.chat.fs import canonical_json


class ReviewChatIsolationTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.service, self.tokens, self.stream = make_service(self.temp.name)
        self.store = self.service.store
        self.a, self.b, self.admin = access(42), access(43), access(1, role=100)
        self.cid = create(self.service, self.a, title="A-private-title")

    def tearDown(self):
        self.stream.release.set()
        self.service.shutdown()
        self.store.close()
        self.temp.cleanup()

    def assert_code(self, expected, function, *args, **kwargs):
        with self.assertRaises(ChatError) as caught:
            function(*args, **kwargs)
        self.assertEqual(caught.exception.code, expected)

    def publish_completed_record(self, actor, cid, rid=None):
        """Model an injected but fully valid owner-local historical run.

        No RNG is patched: rid may intentionally equal a different user's live
        run. Persist via real validators so the fixture is not malformed data.
        """
        rid = rid or str(uuid4())
        scope = self.store.open_conversation(actor.principal, cid)
        snapshot = self.service.user_context.freeze_context(
            actor.principal, cid, rid, 0, []
        )
        with self.store.lease(scope) as lease:
            reference = self.store.write_context(scope, rid, snapshot, lease=lease)
            self.store.append(
                scope,
                "run.accepted",
                {
                    "client_request_id": str(uuid4()),
                    "message_id": str(uuid4()),
                    "text": "A-private-message",
                    "attachment_ids": [],
                    "token_id": 7,
                    "model": "test-chat",
                    "mode": "chat",
                    "capability_revision": "sha256:" + "a" * 64,
                    "context_snapshot": reference,
                    "context_from_run_ids": [],
                },
                run_id=rid,
                lease=lease,
            )
            self.store.append(
                scope,
                "run.terminal",
                {
                    "status": "completed",
                    "reason": "synthetic_history",
                    "error_code": None,
                    "uncertain": False,
                    "model_requests": 0,
                    "tool_calls": 0,
                    "usage": None,
                    "usage_complete": False,
                },
                run_id=rid,
                lease=lease,
            )
        return rid

    def test_cancel_owner_local_completed_run_must_not_abort_foreign_live_run(self):
        # Both namespaces have the same cid, so adding cid alone is not a fix.
        self.service.create_conversation(
            self.b.principal,
            {"conversation_id": self.cid, "title": "B-private-title"},
        )
        self.stream.mode = "blocked"
        victim = self.service.accept_run(
            self.b, self.cid, body(self.service, self.b, self.cid)
        )
        self.assertTrue(self.stream.entered.wait(3), "synthetic stream did not start")
        self.publish_completed_record(self.a, self.cid, victim.run_id)
        self.assertFalse(victim.stop_event.is_set())

        result, status = self.service.cancel_run(
            self.a.principal, self.cid, victim.run_id
        )

        self.assertEqual((status, result["status"]), (200, "completed"))
        self.assertFalse(
            victim.stop_event.is_set(),
            "cancel of A's completed record set B's live run stop_event",
        )

    def wait_for_cleanup(self, run):
        self.assertTrue(run.done.wait(3), "synthetic run did not finish")
        # done is signalled before removal from active; join the producer so
        # assertions below inspect completed cleanup, not a scheduling race.
        run.producer.join(timeout=3)
        self.assertFalse(run.producer.is_alive(), "run cleanup did not finish")

    def test_two_live_users_with_same_cid_and_rid_cancel_and_cleanup_independently(
        self,
    ):
        self.assertEqual(self.store.settings.max_concurrent_runs, 2)
        self.service.create_conversation(
            self.b.principal,
            {"conversation_id": self.cid, "title": "B-private-title"},
        )
        self.stream.mode = "blocked"
        request_a = body(self.service, self.a, self.cid)
        request_b = body(self.service, self.b, self.cid)
        fixed_rid = uuid4()
        # Only service's UUID binding is fixed; filesystem temporary names,
        # reservations and runner message IDs retain their real generators.
        with patch(
            "new_api_cockpit.features.chat.service.uuid4", return_value=fixed_rid
        ):
            run_a = self.service.accept_run(self.a, self.cid, request_a)
            self.assertTrue(self.stream.entered.wait(3), "A did not reach the stream")
            self.stream.entered.clear()
            run_b = self.service.accept_run(self.b, self.cid, request_b)
            self.assertTrue(self.stream.entered.wait(3), "B did not reach the stream")

        self.assertEqual(run_a.scope.cid, run_b.scope.cid)
        self.assertEqual(run_a.run_id, str(fixed_rid))
        self.assertEqual(run_b.run_id, str(fixed_rid))
        self.assertEqual(len(self.stream.calls), 2)
        self.assertFalse(run_a.done.is_set())
        self.assertFalse(run_b.done.is_set())
        key_a = (self.store.prefix, self.a.principal.user_id, self.cid, str(fixed_rid))
        key_b = (self.store.prefix, self.b.principal.user_id, self.cid, str(fixed_rid))
        with self.service.runner._lock:
            active = self.service.runner.active.copy()
        self.assertEqual(set(active), {key_a, key_b})
        self.assertIs(active[key_a], run_a)
        self.assertIs(active[key_b], run_b)

        result, status = self.service.cancel_run(
            self.a.principal, self.cid, run_a.run_id
        )
        self.assertEqual(status, 202)
        self.assertTrue(result["cancel_requested"])
        self.assertTrue(run_a.stop_event.is_set())
        self.assertFalse(run_b.stop_event.is_set())
        self.wait_for_cleanup(run_a)

        with self.service.runner._lock:
            remaining = self.service.runner.active.copy()
        self.assertEqual(set(remaining), {key_b}, "A cleanup removed B's live entry")
        self.assertIs(remaining[key_b], run_b)
        self.assertFalse(run_b.done.is_set())
        self.assertFalse(run_b.stop_event.is_set())
        run_b.abort.check()
        self.assertEqual(
            self.service.get_run(self.a.principal, self.cid, run_a.run_id)["status"],
            "cancelled",
        )
        self.assertEqual(
            self.service.get_run(self.b.principal, self.cid, run_b.run_id)["status"],
            "running",
        )
        self.stream.release.set()
        self.wait_for_cleanup(run_b)
        self.assertEqual(
            self.service.get_run(self.b.principal, self.cid, run_b.run_id)["status"],
            "completed",
        )
        with self.service.runner._lock:
            self.assertEqual(self.service.runner.active, {})

    def test_relogin_with_new_session_and_username_can_cancel_original_live_run(self):
        self.stream.mode = "blocked"
        run = self.service.accept_run(
            self.a, self.cid, body(self.service, self.a, self.cid)
        )
        self.assertTrue(self.stream.entered.wait(3))
        relogged = replace(
            self.a.principal,
            username="A-renamed-after-login",
            session_id="synthetic-new-session",
        )
        relogged_scope = self.store.open_conversation(relogged, self.cid)
        self.assertNotEqual(relogged, run.scope.principal)
        self.assertNotEqual(relogged_scope, run.scope)
        # Prove the local lookup key remains stable independently of the
        # durable cancellation monitor, which could otherwise mask a miss.
        expected_key = (
            self.store.prefix,
            self.a.principal.user_id,
            self.cid,
            run.run_id,
        )
        self.assertEqual(
            self.service.runner._run_key(relogged_scope, run.run_id), expected_key
        )
        with self.service.runner._lock:
            self.assertIs(self.service.runner.active[expected_key], run)

        result, status = self.service.cancel_run(relogged, self.cid, run.run_id)
        self.assertEqual(status, 202)
        self.assertTrue(result["cancel_requested"])
        self.assertTrue(run.stop_event.is_set())
        self.wait_for_cleanup(run)
        for principal in (self.a.principal, relogged):
            self.assertEqual(
                self.service.get_run(principal, self.cid, run.run_id)["status"],
                "cancelled",
            )
        with self.service.runner._lock:
            self.assertEqual(self.service.runner.active, {})

    def test_unknown_foreign_rid_does_not_abort_live_run(self):
        foreign_cid = create(self.service, self.b)
        self.stream.mode = "blocked"
        victim = self.service.accept_run(
            self.b, foreign_cid, body(self.service, self.b, foreign_cid)
        )
        self.assertTrue(self.stream.entered.wait(3))
        self.assert_code(
            "CHAT_NOT_FOUND",
            self.service.cancel_run,
            self.a.principal,
            self.cid,
            victim.run_id,
        )
        self.assertFalse(victim.stop_event.is_set())

    def test_service_read_and_mutation_scopes_do_not_grant_admin_bypass(self):
        fid = str(uuid4())
        self.service.upload(self.a.principal, self.cid, fid, "private.txt", b"A-only")
        rid = self.publish_completed_record(self.a, self.cid)
        for actor in (self.b, self.admin):
            for label, function, args in (
                ("detail", self.service.get_conversation, ()),
                ("events", self.service.events, ({},)),
                ("files", self.service.files, ()),
                ("download", self.service.download, (fid,)),
                ("run", self.service.get_run, (rid,)),
                ("cancel", self.service.cancel_run, (rid,)),
                ("context", self.service.user_context.get_run_context, (rid,)),
                (
                    "rename",
                    self.service.rename_conversation,
                    ({"title": "stolen", "expected_revision": 4},),
                ),
                (
                    "delete",
                    self.service.delete_conversation,
                    ({"expected_revision": 4},),
                ),
            ):
                with self.subTest(uid=actor.principal.user_id, operation=label):
                    self.assert_code(
                        "CHAT_NOT_FOUND", function, actor.principal, self.cid, *args
                    )
        self.assertEqual(
            self.service.download(self.a.principal, self.cid, fid)[0], b"A-only"
        )

    def test_copied_foreign_conversation_header_never_discloses_its_prefix(self):
        fid = str(uuid4())
        self.service.upload(self.a.principal, self.cid, fid, "private.txt", b"A-only")
        self.publish_completed_record(self.a, self.cid)
        # Prepare B's namespace, then inject an entire A-owned journal there.
        create(self.service, self.b)
        a_scope = self.store.open_conversation(self.a.principal, self.cid)
        copied = self.store._scope(self.b.principal, self.cid)
        shutil.copytree(
            Path(self.store.fs.root, a_scope.path),
            Path(self.store.fs.root, copied.path),
        )
        meta = self.service.get_conversation(self.b.principal, self.cid)
        self.assertEqual(meta["storage_state"], "corrupt")
        self.assertNotIn("A-private-title", canonical_json(meta).decode())
        self.assertEqual(
            self.service.events(self.b.principal, self.cid, {})["events"], []
        )
        self.assertEqual(self.service.files(self.b.principal, self.cid)["files"], [])
        self.assert_code(
            "CHAT_NOT_FOUND", self.service.download, self.b.principal, self.cid, fid
        )

    def test_injected_derived_file_map_cannot_import_another_conversation_file(self):
        foreign_cid = create(self.service, self.b)
        fid = str(uuid4())
        foreign, _ = self.service.upload(
            self.b.principal, foreign_cid, fid, "foreign.txt", b"B-only"
        )
        scope = self.store.open_conversation(self.a.principal, self.cid)
        self.store.fs.write_json(
            f"{scope.path}/files.json",
            {"v": 1, "last_seq": 1, "entries": {fid: foreign}},
        )
        self.assertEqual(self.service.files(self.a.principal, self.cid)["files"], [])
        self.assert_code(
            "CHAT_NOT_FOUND", self.service.download, self.a.principal, self.cid, fid
        )


if __name__ == "__main__":
    unittest.main()
