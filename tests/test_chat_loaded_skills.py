"""Persistent owner-scoped Skill loading with real Flask/file storage, no network."""

import copy
import importlib
import os
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

from chat_fixtures import access, body, create, make_service
from new_api_cockpit.auth import LoginRequired
from new_api_cockpit.features.chat import config
from new_api_cockpit.features.chat.errors import ChatError
from new_api_cockpit.features.chat.recovery import project

host = importlib.import_module("new_api_cockpit.app")
REAL_IDENTITY = host.request_identity


class LoadedSkillsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.service, self.tokens, self.stream = make_service(self.tmp.name)
        self.actor = access()
        self.cid = create(self.service, self.actor)
        self.client = host.app.test_client()
        self.enterContext(
            patch.dict(
                os.environ, {"COCKPIT_PROCESS_ROLE": "chat", "CHAT_ENABLED": "true"}
            )
        )
        self.service_mock = self.enterContext(
            patch(
                "new_api_cockpit.composition.get_chat_service",
                return_value=self.service,
            )
        )
        self.enterContext(
            patch.object(
                host,
                "request_identity",
                side_effect=lambda: self.actor.principal.as_user(),
            )
        )
        self.enterContext(
            patch.object(
                host, "new_api_credential", side_effect=lambda: self.actor.credential
            )
        )

    def tearDown(self):
        self.stream.release.set()
        self.service.shutdown()
        self.service.store.close()
        self.tmp.cleanup()

    def headers(self, **changes):
        return {
            "X-Cockpit-Session": self.actor.principal.session_id,
            "X-Cockpit-Chat": "1",
            **changes,
        }

    def api(self, suffix, method="GET", **kwargs):
        return self.client.open(
            "/cockpit/api/chat" + suffix,
            method=method,
            headers=self.headers(),
            **kwargs,
        )

    def meta(self, cid=None):
        response = self.api(f"/conversations/{cid or self.cid}")
        self.assertEqual(response.status_code, 200, response.json)
        return response.json["conversation"]

    def skill(self, name, content="CONTINUOUS-SKILL", actor=None):
        actor = actor or self.actor
        revision = self.service.user_context.read_context(actor.principal)[
            "context_revision"
        ]
        return self.service.user_context.create_skill(
            actor.principal,
            revision,
            f'---\nname: {name}\ndescription: "Offline fixture template"\n---\n{content}',
        )["skill"]

    def selection(self, ids, **changes):
        return {
            "expected_revision": self.meta()["revision"],
            "context_revision": self.service.user_context.read_context(
                self.actor.principal
            )["context_revision"],
            "loaded_skill_ids": ids,
            **changes,
        }

    def load(self, ids, **changes):
        return self.api(
            f"/conversations/{self.cid}",
            "PATCH",
            json=self.selection(ids, **changes),
        )

    def send(self, ids):
        request = body(self.service, self.actor, self.cid, skill_ids=ids)
        response = self.api(f"/conversations/{self.cid}/runs", "POST", json=request)
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        self.assertIn("run.terminal", response.get_data(as_text=True))
        history = self.service._history(
            self.service.store.open_conversation(self.actor.principal, self.cid)
        )
        rid = next(
            e["run_id"] for e in reversed(history) if e["type"] == "run.accepted"
        )
        return rid, request

    def snapshot(self, rid):
        response = self.api(f"/conversations/{self.cid}/runs/{rid}/context")
        self.assertEqual(response.status_code, 200, response.json)
        return response.json["context_snapshot"]

    def test_legacy_default_noop_and_loading_create_no_run_or_snapshot(self):
        original = self.meta()
        self.assertEqual(original["loaded_skill_ids"], [])
        response = self.load([])
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(
            response.json["conversation"]["revision"], original["revision"]
        )
        item = self.skill("alpha")
        response = self.load([item["id"]])
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(
            response.json["conversation"]["loaded_skill_ids"], [item["id"]]
        )
        scope = self.service.store.open_conversation(self.actor.principal, self.cid)
        events = self.service._history(scope)
        self.assertEqual(
            [e["type"] for e in events],
            ["conversation.created", "conversation.skills.updated"],
        )
        self.assertIsNone(events[-1]["run_id"])
        self.assertEqual(self.service.store.fs.listdir(f"{scope.path}/contexts"), [])
        self.assertFalse(self.tokens.key_entered.is_set())
        self.assertEqual(self.stream.calls, [])

    def test_order_survives_service_restart_projection_repair_and_other_chat_is_empty(
        self,
    ):
        first, second = self.skill("alpha"), self.skill("beta")
        ids = [second["id"], first["id"]]
        self.assertEqual(self.load(ids).status_code, 200)
        self.service.shutdown()
        self.service.store.close()
        self.service, self.tokens, self.stream = make_service(self.tmp.name)
        self.service_mock.return_value = self.service
        self.assertEqual(self.meta()["loaded_skill_ids"], ids)
        scope = self.service.store.open_conversation(self.actor.principal, self.cid)
        self.service.store.fs.unlink(f"{scope.path}/meta.json")
        self.assertEqual(self.meta()["loaded_skill_ids"], ids)
        self.assertEqual(
            self.api("/conversations").json["conversations"][0]["loaded_skill_ids"], ids
        )
        other = create(self.service, self.actor)
        self.assertEqual(self.meta(other)["loaded_skill_ids"], [])
        self.assertEqual(self.stream.calls, [])

    def test_two_sends_keep_loaded_skills_and_frozen_versions_survive_library_edit(
        self,
    ):
        item = self.skill("alpha", "ORIGINAL-SKILL-TEXT")
        ids = [item["id"]]
        self.assertEqual(self.load(ids).status_code, 200)
        rid, original_request = self.send(ids)
        original_snapshot = self.snapshot(rid)
        self.assertEqual(self.meta()["loaded_skill_ids"], ids)
        self.send(ids)
        self.assertEqual(self.meta()["loaded_skill_ids"], ids)
        self.assertEqual(len(self.stream.calls), 2)
        self.assertIn("ORIGINAL-SKILL-TEXT", str(self.stream.calls))
        self.service.user_context.update_skill(
            self.actor.principal,
            item["id"],
            1,
            1,
            item["content"].replace("ORIGINAL-SKILL-TEXT", "UPDATED-SKILL-TEXT"),
        )
        self.assertEqual(self.snapshot(rid), original_snapshot)
        new_rid, _ = self.send(ids)
        self.assertIn(
            "UPDATED-SKILL-TEXT", self.snapshot(new_rid)["skills"][0]["content"]
        )
        self.assertEqual(self.meta()["loaded_skill_ids"], ids)
        self.assertEqual(self.load([]).status_code, 200)
        replay = self.api(
            f"/conversations/{self.cid}/runs", "POST", json=original_request
        )
        self.assertEqual(replay.status_code, 200, replay.json)
        self.assertTrue(replay.json["replayed"])
        self.assertEqual(len(self.stream.calls), 3)
        self.assertEqual(self.meta()["loaded_skill_ids"], [])

    def test_manual_remove_does_not_delete_library_or_change_personal_context_revision(
        self,
    ):
        item = self.skill("alpha")
        self.assertEqual(self.load([item["id"]]).status_code, 200)
        context = copy.deepcopy(self.api("/context").json)
        self.assertEqual(self.load([]).status_code, 200)
        self.assertEqual(self.api("/context").json, context)
        self.assertEqual(self.meta()["loaded_skill_ids"], [])
        self.assertEqual(self.stream.calls, [])

    def test_stale_chat_and_context_revisions_cannot_overwrite_loaded_skills(self):
        first, second = self.skill("alpha"), self.skill("beta")
        stale = self.selection([second["id"]])
        self.assertEqual(self.load([first["id"]]).status_code, 200)
        response = self.api(f"/conversations/{self.cid}", "PATCH", json=stale)
        self.assertEqual(
            (response.status_code, response.json["code"]),
            (409, "CHAT_REVISION_CONFLICT"),
        )
        stale_context = self.selection([second["id"]])
        self.skill("gamma")
        response = self.api(f"/conversations/{self.cid}", "PATCH", json=stale_context)
        self.assertEqual(
            (response.status_code, response.json["code"]), (409, "CHAT_CONTEXT_CHANGED")
        )
        self.assertEqual(self.meta()["loaded_skill_ids"], [first["id"]])

    def test_foreign_chat_or_skill_is_not_visible_even_to_admin(self):
        owner = self.actor
        owned = self.skill("alpha")
        for stranger in [access(13), access(99, role=100)]:
            self.actor = stranger
            response = self.api(
                f"/conversations/{self.cid}",
                "PATCH",
                json={
                    "expected_revision": 1,
                    "context_revision": 0,
                    "loaded_skill_ids": [owned["id"]],
                },
            )
            self.assertEqual(
                (response.status_code, response.json["code"]), (404, "CHAT_NOT_FOUND")
            )
        self.actor = owner
        foreign = self.skill("foreign", actor=access(13))
        response = self.load([foreign["id"]])
        self.assertEqual(
            (response.status_code, response.json["code"]), (404, "CHAT_NOT_FOUND")
        )
        self.assertEqual(self.meta()["loaded_skill_ids"], [])

    def test_invalid_or_mixed_patch_bodies_leave_metadata_unchanged(self):
        item = self.skill("alpha")
        original = self.meta()
        for selected, extra in [
            ([item["id"], item["id"]], {}),
            ([str(uuid4()) for _ in range(4)], {}),
            (["../foreign"], {}),
            (None, {}),
            ([], {"title": "must-not-rename"}),
            ([], {"user_id": 99}),
            ([], {"expected_revision": True}),
            ([], {"context_revision": True}),
        ]:
            with self.subTest(selected=selected, extra=extra):
                response = self.load(selected, **extra)
                self.assertEqual(response.status_code, 400, response.json)
                self.assertEqual(self.meta(), original)

    def test_deleted_library_entry_is_not_silently_removed_but_can_be_unloaded(self):
        item = self.skill("alpha")
        ids = [item["id"]]
        self.assertEqual(self.load(ids).status_code, 200)
        self.service.user_context.delete_skill(self.actor.principal, item["id"], 1, 1)
        response = self.load(ids)
        self.assertEqual(response.status_code, 404, response.json)
        self.assertEqual(self.meta()["loaded_skill_ids"], ids)
        self.assertEqual(self.load([]).status_code, 200)
        self.assertEqual(self.meta()["loaded_skill_ids"], [])

    def test_load_rejects_known_credential_content_and_budget_without_any_paid_step(
        self,
    ):
        item = self.skill("sensitive", "Offline fixture: " + self.actor.credential)
        response = self.load([item["id"]])
        self.assertEqual(
            (response.status_code, response.json["code"]),
            (422, "CHAT_CONTEXT_CREDENTIAL_FORBIDDEN"),
        )
        safe = self.skill("safe")
        with patch.object(config, "PERSONAL_CONTEXT_BYTES", 1):
            response = self.load([safe["id"]])
        self.assertEqual(
            (response.status_code, response.json["code"]),
            (413, "CHAT_CONTEXT_TOO_LARGE"),
        )
        self.assertEqual(self.meta()["loaded_skill_ids"], [])
        self.assertFalse(self.tokens.key_entered.is_set())
        self.assertEqual(self.stream.calls, [])

    def test_running_conversation_and_invalid_event_transitions_are_rejected(self):
        item = self.skill("alpha")
        scope = self.service.store.open_conversation(self.actor.principal, self.cid)
        with self.service.store.lease(scope) as lease:
            with self.assertRaises(ChatError):
                self.service.store.append(
                    scope,
                    "conversation.skills.updated",
                    {"skill_ids": [item["id"]]},
                    run_id=str(uuid4()),
                    lease=lease,
                )
        self.stream.mode = "blocked"
        result = self.service.accept_run(
            self.actor, self.cid, body(self.service, self.actor, self.cid)
        )
        self.assertTrue(self.stream.entered.wait(3))
        response = self.load([item["id"]])
        self.assertEqual(
            (response.status_code, response.json["code"]),
            (409, "CHAT_CONVERSATION_BUSY"),
        )
        events = self.service._history(scope)
        invalid = {
            "v": 1,
            "seq": events[-1]["seq"] + 1,
            "type": "conversation.skills.updated",
            "conversation_id": self.cid,
            "run_id": None,
            "at": events[-1]["at"],
            "data": {"skill_ids": []},
        }
        with self.assertRaises(ChatError):
            project(scope, [*events, invalid])
        self.service.cancel_run(self.actor.principal, self.cid, result.run_id)

    def test_session_stamp_origin_and_pat_guards_also_cover_loaded_skills_variant(self):
        payload = self.selection([])
        path = f"/cockpit/api/chat/conversations/{self.cid}"
        self.assertEqual(self.client.patch(path, json=payload).status_code, 403)
        self.assertEqual(
            self.client.patch(
                path, json=payload, headers=self.headers(**{"X-Cockpit-Session": "old"})
            ).status_code,
            409,
        )
        self.assertEqual(
            self.client.patch(
                path,
                json=payload,
                headers=self.headers(Origin="https://different.test"),
            ).status_code,
            403,
        )
        with (
            patch.object(host, "request_identity", REAL_IDENTITY),
            patch.object(host, "verify_session", side_effect=LoginRequired("invalid")),
            patch.object(host, "verify_pat") as pat,
        ):
            response = self.client.patch(
                path,
                json=payload,
                headers={
                    "Authorization": "Bearer opaque-fixture-pat",
                    "X-Cockpit-Session": "fixture",
                    "X-Cockpit-Chat": "1",
                },
            )
        self.assertEqual(response.status_code, 401)
        pat.assert_not_called()


if __name__ == "__main__":
    unittest.main()
