"""Whole run lifecycle against real local storage and synthetic model streams."""

from pathlib import Path
import tempfile
import threading
import unittest
from uuid import uuid4
from unittest.mock import MagicMock, patch

from new_api_cockpit.features.chat.errors import ChatError
from new_api_cockpit.features.chat.runner import ActiveRun, Runner
from new_api_cockpit.features.chat.locks import ConversationLease, FileLock
from chat_fixtures import access, body, create, make_service


class ChatRunnerTest(unittest.TestCase):
    def test_cleanup_fault_still_signals_done_drops_credentials_and_fails_closed(self):
        store = MagicMock()
        store.get_run.return_value = {"status": "completed"}
        runner = Runner(store, MagicMock(), MagicMock())
        lease = MagicMock(poisoned=False, closed=False)
        lease.close.side_effect = OSError("synthetic cleanup failure")
        run = ActiveRun(
            MagicMock(instance_id="fixture", user_id=12, cid=str(uuid4())),
            lease,
            str(uuid4()),
            {},
            access(),
            [{"content": "private test"}],
            {},
            [],
        )
        runner.active[runner._run_key(run.scope, run.run_id)] = run
        runner._finish(run, "completed", None, "completed")
        self.assertTrue(run.done.is_set())
        self.assertIsNone(run.access)
        self.assertEqual(run.messages, [])
        self.assertEqual(runner.active, {})
        self.assertTrue(runner.stopping)

    def test_all_lock_descriptors_are_closed_even_when_release_fails(self):
        lock = FileLock.__new__(FileLock)
        lock.fd = 123
        with (
            patch(
                "new_api_cockpit.features.chat.locks.fcntl.flock",
                side_effect=OSError("unlock"),
            ),
            patch("new_api_cockpit.features.chat.locks.os.close") as close,
            self.assertRaises(OSError),
        ):
            lock.close()
        close.assert_called_once_with(123)
        self.assertEqual(lock.fd, -1)
        lease = ConversationLease.__new__(ConversationLease)
        lease.closed = False
        lease.reservation = MagicMock()
        lease.reservation.close.side_effect = OSError("quota cleanup")
        first, second = MagicMock(), MagicMock()
        second.close.side_effect = OSError("lock cleanup")
        lease._locks = [first, second]
        with self.assertRaises(OSError):
            lease.close()
        first.close.assert_called_once()
        second.close.assert_called_once()
        self.assertTrue(lease.closed)
        self.assertEqual(lease._locks, [])

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.service, self.tokens, self.stream = make_service(self.tmp.name)
        self.actor = access()
        self.cid = create(self.service, self.actor)

    def tearDown(self):
        self.stream.release.set()
        if self.tokens.key_gate is not None:
            self.tokens.key_gate.set()
        self.service.shutdown()
        self.service.store.close()
        self.tmp.cleanup()

    def run_turn(self, **changes):
        request = body(self.service, self.actor, self.cid, **changes)
        active = self.service.accept_run(self.actor, self.cid, request)
        text = "".join(self.service.stream(active))
        return request, active, text

    def test_success_is_persisted_and_replay_survives_key_and_settings_change(self):
        request, active, text = self.run_turn()
        state = self.service.get_run(self.actor.principal, self.cid, active.run_id)
        self.assertEqual(state["status"], "completed")
        self.assertEqual(state["usage"]["total_tokens"], 15)
        self.assertTrue(state["usage_complete"])
        self.assertIn("run.terminal", text)
        self.service.user_context.update_profile(
            self.actor.principal, "MEMORY.md", 0, 0, "新偏好"
        )
        self.tokens.unavailable = True
        repeated = self.service.accept_run(self.actor, self.cid, request)
        self.assertTrue(repeated["replayed"])
        self.assertEqual(repeated["run_id"], active.run_id)
        self.assertEqual(len(self.stream.calls), 1)
        with self.assertRaises(ChatError) as caught:
            self.service.accept_run(
                self.actor, self.cid, {**request, "text": "变更内容"}
            )
        self.assertEqual(caught.exception.code, "CHAT_IDEMPOTENCY_CONFLICT")

    def test_cancel_while_key_query_blocked_prevents_model_dispatch(self):
        self.tokens.key_gate = threading.Event()
        request = body(self.service, self.actor, self.cid)
        active = self.service.accept_run(self.actor, self.cid, request)
        self.assertTrue(self.tokens.key_entered.wait(2))
        result, status = self.service.cancel_run(
            self.actor.principal, self.cid, active.run_id
        )
        self.assertEqual(status, 202)
        self.assertTrue(result["cancel_requested"])
        self.tokens.key_gate.set()
        "".join(self.service.stream(active))
        state = self.service.get_run(self.actor.principal, self.cid, active.run_id)
        self.assertEqual(state["status"], "cancelled")
        self.assertEqual(state["model_requests"], 0)
        self.assertFalse(state["uncertain"])
        self.assertEqual(self.stream.calls, [])

    def test_disconnect_does_not_retry_and_keeps_partial_history(self):
        self.stream.mode = "blocked"
        active = self.service.accept_run(
            self.actor, self.cid, body(self.service, self.actor, self.cid)
        )
        self.assertTrue(self.stream.entered.wait(2))
        iterator = self.service.stream(active)
        self.assertIn("run.accepted", next(iterator))
        iterator.close()
        self.assertTrue(active.done.wait(3))
        state = self.service.get_run(self.actor.principal, self.cid, active.run_id)
        self.assertEqual(state["status"], "cancelled")
        self.assertTrue(state["uncertain"])
        self.assertEqual(len(self.stream.calls), 1)
        events = self.service.events(self.actor.principal, self.cid, {})["events"]
        self.assertTrue(any(e["type"] == "run.accepted" for e in events))

    def test_model_requests_stop_at_six_without_seventh_summary(self):
        self.stream.mode = "loop"
        _, active, _ = self.run_turn(mode="files")
        state = self.service.get_run(self.actor.principal, self.cid, active.run_id)
        self.assertEqual(len(self.stream.calls), 6)
        self.assertEqual(state["model_requests"], 6)
        self.assertEqual(state["tool_calls"], 5)
        self.assertEqual(state["status"], "failed")
        self.assertEqual(state["error_code"], "CHAT_RUN_LIMIT")

    def test_write_files_have_versions_and_contiguous_sse_events(self):
        self.stream.mode = "write"
        for expected in (1, 2):
            _, active, text = self.run_turn(mode="files")
            state = self.service.get_run(self.actor.principal, self.cid, active.run_id)
            self.assertEqual(state["status"], "completed")
            self.assertIn("file.added", text)
            ids = [
                int(line[4:]) for line in text.splitlines() if line.startswith("id: ")
            ]
            self.assertEqual(ids, list(range(ids[0], ids[-1] + 1)))
            files = self.service.files(self.actor.principal, self.cid)["files"]
            self.assertEqual(
                [f["version"] for f in files], list(range(1, expected + 1))
            )

    def test_other_users_admins_and_same_user_other_chat_cannot_access_files(self):
        fid = str(uuid4())
        self.service.upload(
            self.actor.principal, self.cid, fid, "private.md", b"private fixture"
        )
        for actor in (access(13), access(99, role=100)):
            for function, args in (
                (self.service.get_conversation, (actor.principal, self.cid)),
                (self.service.events, (actor.principal, self.cid, {})),
                (self.service.download, (actor.principal, self.cid, fid)),
            ):
                with (
                    self.subTest(actor=actor.principal.user_id),
                    self.assertRaises(ChatError) as caught,
                ):
                    function(*args)
                self.assertEqual(caught.exception.status, 404)
        other = create(self.service, self.actor)
        with self.assertRaises(ChatError) as caught:
            self.service.download(self.actor.principal, other, fid)
        self.assertEqual(caught.exception.status, 404)

    def test_all_history_visible_after_reopen_and_no_key_needed_to_view(self):
        ids = {self.cid}
        for n in range(7):
            ids.add(create(self.service, self.actor, title=f"聊天{n}"))
        self.run_turn()
        self.tokens.unavailable = True
        found, cursor = set(), None
        while True:
            query = {"limit": 3}
            if cursor:
                query["cursor"] = cursor
            page = self.service.list_conversations(self.actor.principal, query)
            found.update(c["id"] for c in page["conversations"])
            if not page["has_more"]:
                break
            cursor = page["next_cursor"]
        self.assertEqual(found, ids)
        # A fresh store/process view does not depend on a browser session or KEY.
        from new_api_cockpit.features.chat.storage import ChatStore

        reopened = ChatStore(self.service.settings)
        try:
            self.assertEqual(
                reopened.list_conversations(self.actor.principal)["total"], 8
            )
        finally:
            reopened.close()
        for path in Path(self.tmp.name).rglob("*"):
            if path.is_file():
                raw = path.read_bytes()
                self.assertNotIn(b"synthetic-session-only", raw)
                self.assertNotIn(b"testfixtureonly" * 4, raw)


if __name__ == "__main__":
    unittest.main()
