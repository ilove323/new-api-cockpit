"""Actual Flask routing with verified synthetic users and real file storage."""

import importlib
from io import BytesIO
import os
import tempfile
from unittest.mock import patch
import unittest
from uuid import uuid4

from new_api_cockpit.auth import LoginRequired
from new_api_cockpit.features.chat import config
from new_api_cockpit.features.chat.workspace import Workspace
from new_api_cockpit.integrations.new_api.personal_tokens import TokenError
from new_api_cockpit.integrations.new_api.token_provider import TokenProvider
from chat_fixtures import access, body, create, make_service

host = importlib.import_module("new_api_cockpit.app")
REAL_IDENTITY = host.request_identity


class ChatApiTest(unittest.TestCase):
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
        self.enterContext(
            patch(
                "new_api_cockpit.composition.get_chat_service",
                return_value=self.service,
            )
        )
        self.identity_patch = patch.object(
            host, "request_identity", side_effect=lambda: self.actor.principal.as_user()
        )
        self.identity = self.enterContext(self.identity_patch)
        self.enterContext(
            patch.object(host, "new_api_credential", return_value=self.actor.credential)
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
        response = self.client.open(
            "/cockpit/api/chat" + suffix,
            method=method,
            headers=self.headers(),
            **kwargs,
        )
        # Werkzeug's test builder spools large multipart request bodies to disk.
        self.addCleanup(response.request.environ["wsgi.input"].close)
        return response

    def test_cookie_stamp_origin_role_and_disabled_feature_are_enforced(self):
        self.assertEqual(
            self.client.get("/cockpit/api/chat/conversations").status_code, 403
        )
        self.assertEqual(
            self.client.get(
                "/cockpit/api/chat/conversations",
                headers=self.headers(**{"X-Cockpit-Session": "old-session"}),
            ).status_code,
            409,
        )
        response = self.client.post(
            "/cockpit/api/chat/conversations",
            json={"conversation_id": str(uuid4())},
            headers=self.headers(Origin="https://different.test"),
        )
        self.assertEqual(response.status_code, 403)
        with patch.dict(os.environ, {"COCKPIT_PROCESS_ROLE": "web"}):
            self.assertEqual(self.api("/conversations").status_code, 503)
        with patch.dict(os.environ, {"COCKPIT_PROCESS_ROLE": "image"}):
            self.assertEqual(self.api("/conversations").status_code, 404)
        with patch.dict(os.environ, {"CHAT_ENABLED": "false"}):
            self.assertEqual(self.api("/conversations").status_code, 404)

    def test_recovery_snapshot_is_one_scoped_read_and_omits_unchanged_files(self):
        scope = self.service.store.open_conversation(self.actor.principal, self.cid)
        self.service.store.upload(
            scope, "fixture.txt", b"read-only fixture", str(uuid4())
        )
        self.identity.reset_mock()
        with patch.object(
            self.service.store, "_published", wraps=self.service.store._published
        ) as read:
            first = self.api(f"/conversations/{self.cid}/sync?after_seq=0&limit=1")
        self.assertEqual(first.status_code, 200)
        self.assertEqual(read.call_count, 1)
        self.assertEqual(self.identity.call_count, 1)
        value = first.json
        self.assertTrue(value["events"]["has_more"])
        self.assertEqual(
            value["events"]["until_seq"], value["conversation"]["last_seq"]
        )
        self.assertEqual(len(value["files"]), 1)
        self.assertIsNone(value["run"])
        self.assertIsNone(value["context"])
        query = f"after_seq={value['conversation']['last_seq']}&files_revision={value['files_revision']}"
        second = self.api(f"/conversations/{self.cid}/sync?{query}")
        self.assertEqual(second.status_code, 200)
        self.assertEqual(second.json["events"]["events"], [])
        self.assertIsNone(second.json["files"])
        self.assertEqual(self.stream.calls, [])
        self.service.delete_upload(
            self.actor.principal, self.cid, value["files"][0]["id"]
        )
        third = self.api(f"/conversations/{self.cid}/sync?{query}")
        self.assertEqual(third.status_code, 200)
        self.assertNotEqual(third.json["files_revision"], value["files_revision"])
        self.assertTrue(third.json["files"][0]["deleted_at"])

    def test_recovery_snapshot_rejects_bad_cursor_or_other_owners_without_dispatch(
        self,
    ):
        for query in ("after_seq=999", "files_revision=bad", "limit=0", "unknown=1"):
            self.assertEqual(
                self.api(f"/conversations/{self.cid}/sync?{query}").status_code, 400
            )
        self.actor = access(99)
        self.assertEqual(self.api(f"/conversations/{self.cid}/sync").status_code, 404)
        self.assertEqual(self.stream.calls, [])

    def test_catalogue_rate_limit_is_not_reported_as_a_permission_error(self):
        with patch.object(
            self.tokens,
            "list_models",
            side_effect=TokenError("private upstream details", 429, retry_after="1800"),
        ) as call:
            response = self.api("/models", "POST", json={"token_id": 7})
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.headers["Retry-After"], "1800")
        self.assertEqual(response.json["code"], "CHAT_UPSTREAM_RATE_LIMIT")
        self.assertNotIn("权限", response.json["error"])
        self.assertNotIn("private", str(response.json))
        self.assertEqual(call.call_count, 1)

    def test_repeated_http_catalogue_reads_do_not_exhaust_the_secret_read_limit(self):
        calls = []

        def upstream(path, credential, uid, **kwargs):
            calls.append(path)
            if path == "/api/token/7":
                return {
                    "data": dict(
                        id=7,
                        user_id=12,
                        status=1,
                        expired_time=-1,
                        remain_quota=100,
                        unlimited_quota=False,
                        group="auto",
                        key="xxxx********xxxx",
                        model_limits_enabled=False,
                    )
                }
            if path == "/api/token/7/key":
                if calls.count(path) > 1:
                    raise TokenError("rate limited", 429)
                return {"data": {"key": "x" * 48}}
            if path == "/v1/models":
                return {
                    "data": [
                        {"id": "test-chat", "supported_endpoint_types": ["openai"]}
                    ]
                }
            self.fail("Unexpected upstream path")

        self.service.token_provider = TokenProvider(upstream)
        for _ in range(24):
            response = self.api("/models", "POST", json={"token_id": 7})
            self.assertEqual(response.status_code, 200, response.json)
            self.assertTrue(response.json["models"][0]["chat_enabled"])
        self.assertEqual(calls.count("/api/token/7/key"), 1)
        self.assertFalse(self.stream.calls)

    def test_every_user_and_admin_can_only_see_their_own_chat_and_files(self):
        fid = str(uuid4())
        result = self.api(
            f"/conversations/{self.cid}/files",
            "POST",
            data={"client_file_id": fid, "file": (BytesIO(b"private"), "private.md")},
        )
        self.assertEqual(result.status_code, 201, result.json)
        for outsider in (access(13), access(99, role=100)):
            self.actor = outsider
            self.assertEqual(self.api("/conversations").json["total"], 0)
            for suffix, method, payload in (
                (f"/conversations/{self.cid}", "GET", None),
                (f"/conversations/{self.cid}/events", "GET", None),
                (f"/conversations/{self.cid}/files", "GET", None),
                (f"/conversations/{self.cid}/files/{fid}/download", "GET", None),
                (
                    f"/conversations/{self.cid}",
                    "PATCH",
                    {"title": "forged", "expected_revision": 2},
                ),
                (f"/conversations/{self.cid}/runs/{uuid4()}/cancel", "POST", {}),
            ):
                with self.subTest(user=outsider.principal.user_id, suffix=suffix):
                    response = (
                        self.api(suffix, method, json=payload)
                        if payload is not None
                        else self.api(suffix, method)
                    )
                    self.assertEqual(response.status_code, 404)
                    self.assertEqual(response.json["code"], "CHAT_NOT_FOUND")

    def test_run_sse_replay_and_snapshot_endpoints(self):
        request = body(self.service, self.actor, self.cid)
        first = self.api(f"/conversations/{self.cid}/runs", "POST", json=request)
        self.assertEqual(first.status_code, 200, first.get_data(as_text=True))
        self.assertTrue(first.content_type.startswith("text/event-stream"))
        output = first.get_data(as_text=True)
        self.assertIn("run.terminal", output)
        self.assertNotIn("synthetic-session-only", output)
        replay = self.api(f"/conversations/{self.cid}/runs", "POST", json=request)
        self.assertTrue(replay.json["replayed"])
        rid = replay.json["run_id"]
        snapshot = self.api(f"/conversations/{self.cid}/runs/{rid}/context")
        self.assertEqual(snapshot.status_code, 200, snapshot.json)
        self.assertEqual(snapshot.json["context_snapshot"]["run_id"], rid)
        self.assertEqual(len(self.stream.calls), 1)
        self.actor = access(99, role=100)
        self.assertEqual(
            self.api(f"/conversations/{self.cid}/runs/{rid}/context").status_code, 404
        )

    def test_personal_documents_and_skills_ui_contract(self):
        current = self.api("/context")
        self.assertEqual(current.json["context_revision"], 0)
        response = self.api(
            "/profiles/MEMORY.md",
            "PATCH",
            json={"context_revision": 0, "expected_revision": 0, "content": "偏好中文"},
        )
        self.assertEqual(response.status_code, 200, response.json)
        skill = self.api(
            "/skills",
            "POST",
            json={
                "context_revision": 1,
                "content": '---\nname: summarize\ndescription: "整理材料"\n---\n只使用用户给出的材料。',
            },
        )
        self.assertEqual(skill.status_code, 201, skill.json)
        sid = skill.json["skill"]["id"]
        detail = self.api(f"/skills/{sid}?context_revision=2")
        self.assertEqual(detail.status_code, 200)
        self.assertIn("整理材料", detail.json["skill"]["content"])
        self.actor = access(13)
        self.assertEqual(self.api(f"/skills/{sid}").status_code, 404)
        self.assertEqual(self.api("/context").json["context_revision"], 0)

    def test_real_50_mib_upload_download_bounded_read_and_next_run_reservation(self):
        self.assertEqual(config.UPLOAD_FILE_BYTES, 50 * 1024 * 1024)
        published = self.api("/config").json["limits"]
        self.assertEqual(published["upload_file_bytes"], config.UPLOAD_FILE_BYTES)
        content = b"x\n" * (config.UPLOAD_FILE_BYTES // 2)
        fid = str(uuid4())
        response = self.api(
            f"/conversations/{self.cid}/files",
            "POST",
            data={"client_file_id": fid, "file": (BytesIO(content), "large.txt")},
        )
        self.assertEqual(response.status_code, 201, response.json)
        self.assertEqual(response.json["file"]["size_bytes"], len(content))
        scope = self.service.store.open_conversation(self.actor.principal, self.cid)
        result = Workspace(self.service.store, scope).read_file(fid, 1, 2)
        self.assertEqual(result["text"], "x\nx\n")
        self.assertEqual(result["next_line"], 3)
        downloaded = self.api(f"/conversations/{self.cid}/files/{fid}/download")
        self.assertEqual(downloaded.status_code, 200)
        self.assertEqual(downloaded.data, content)
        with self.service.store.lease(scope, run=True) as lease:
            self.service.store.reserve(
                scope, config.RUN_STORAGE_RESERVE_BYTES, lease=lease
            )
        self.assertEqual(self.stream.calls, [])

    def test_over_50_mib_file_is_rejected_without_publishing_or_model_calls(self):
        response = self.api(
            f"/conversations/{self.cid}/files",
            "POST",
            data={
                "client_file_id": str(uuid4()),
                "file": (BytesIO(b"x" * (config.UPLOAD_FILE_BYTES + 1)), "large.txt"),
            },
        )
        self.assertEqual(response.status_code, 413, response.json)
        self.assertEqual(response.json["code"], "CHAT_FILE_TOO_LARGE")
        self.assertEqual(self.api(f"/conversations/{self.cid}/files").json["files"], [])
        self.assertEqual(self.stream.calls, [])

    def test_multipart_above_parser_chunk_and_scalar_fields_remain_bounded(self):
        content = b"text\n" * 40000
        response = self.api(
            f"/conversations/{self.cid}/files",
            "POST",
            data={
                "client_file_id": str(uuid4()),
                "file": (BytesIO(content), "chunks.txt"),
            },
        )
        self.assertEqual(response.status_code, 201, response.json)
        for fields in (
            {"client_file_id": "x" * (config.UPLOAD_FORM_MEMORY_BYTES + 1)},
            {"client_file_id": str(uuid4()), "extra": "forbidden"},
        ):
            response = self.api(
                f"/conversations/{self.cid}/files",
                "POST",
                data={**fields, "file": (BytesIO(b"x"), "small.txt")},
            )
            self.assertIn(response.status_code, (400, 413), response.json)
        self.assertEqual(
            len(self.api(f"/conversations/{self.cid}/files").json["files"]), 1
        )
        response = self.api(
            f"/conversations/{self.cid}/files",
            "POST",
            data={
                "client_file_id": str(uuid4()),
                "file": (BytesIO(b"ok"), "after.txt"),
            },
        )
        self.assertEqual(response.status_code, 201, response.json)

    def test_concurrent_upload_is_rejected_before_spooling_and_owner_is_checked_first(
        self,
    ):
        with self.service.upload_request(self.actor.principal, self.cid):
            with patch(
                "flask.wrappers.Request._load_form_data",
                side_effect=AssertionError("busy upload read body"),
            ):
                response = self.api(
                    f"/conversations/{self.cid}/files",
                    "POST",
                    data={
                        "client_file_id": str(uuid4()),
                        "file": (BytesIO(b"ok"), "busy.txt"),
                    },
                )
                self.assertEqual(response.status_code, 429, response.json)
                self.assertEqual(response.json["code"], "CHAT_UPLOAD_BUSY")
                self.actor = access(13)
                response = self.api(
                    f"/conversations/{self.cid}/files",
                    "POST",
                    data={
                        "client_file_id": str(uuid4()),
                        "file": (BytesIO(b"ok"), "other.txt"),
                    },
                )
                self.assertEqual(response.status_code, 404, response.json)
        self.actor = access()
        response = self.api(
            f"/conversations/{self.cid}/files",
            "POST",
            data={
                "client_file_id": str(uuid4()),
                "file": (BytesIO(b"ok"), "after.txt"),
            },
        )
        self.assertEqual(response.status_code, 201, response.json)

    def test_duplicate_json_fields_and_owner_override_are_rejected(self):
        response = self.api(
            "/models",
            "POST",
            data='{"token_id":7,"token_id":8}',
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        response = self.api(
            "/conversations",
            "POST",
            json={"conversation_id": str(uuid4()), "user_id": 13},
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.api("/conversations?user_id=13").status_code, 400)

    def test_chat_pat_never_reaches_pat_database_lookup(self):
        with (
            patch.object(host, "request_identity", REAL_IDENTITY),
            patch.object(host, "verify_session", side_effect=LoginRequired("invalid")),
            patch.object(host, "verify_pat") as pat,
        ):
            response = self.client.get(
                "/cockpit/api/chat/conversations",
                headers={
                    "Authorization": "Bearer opaque-test-pat",
                    "X-Cockpit-Session": "test",
                },
            )
        self.assertEqual(response.status_code, 401)
        pat.assert_not_called()

    def test_every_chat_api_method_rejects_pat_before_dispatch(self):
        with (
            patch.object(host, "request_identity", REAL_IDENTITY),
            patch.object(host, "verify_session", side_effect=LoginRequired("invalid")),
            patch.object(host, "verify_pat") as pat,
        ):
            count = 0
            for rule in host.app.url_map.iter_rules():
                if not rule.rule.startswith("/cockpit/api/chat/"):
                    continue
                path = rule.rule
                for name in rule.arguments:
                    path = path.replace("<" + name + ">", str(uuid4()))
                for method in rule.methods - {"HEAD", "OPTIONS"}:
                    response = self.client.open(
                        path,
                        method=method,
                        json={},
                        headers={
                            "Authorization": "Bearer opaque-test-pat",
                            "X-Cockpit-Session": "test",
                            "X-Cockpit-Chat": "1",
                        },
                    )
                    self.assertEqual(response.status_code, 401, (path, method))
                    count += 1
            self.assertEqual(count, 27)
            pat.assert_not_called()

    def test_attachment_delete_is_confirmed_owner_only_idempotent_and_tombstones_history(
        self,
    ):
        fid = str(uuid4())
        self.service.upload(
            self.actor.principal, self.cid, fid, "report.md", b"original"
        )
        path = f"/conversations/{self.cid}/files/{fid}"
        self.assertEqual(
            self.api(path, "DELETE", json={"confirm": False}).status_code, 400
        )
        self.assertEqual(
            self.api(path, "DELETE", json={"confirm": True, "user_id": 99}).status_code,
            400,
        )
        self.assertEqual(
            self.api(
                path + "?scope_id=other", "DELETE", json={"confirm": True}
            ).status_code,
            400,
        )
        self.assertEqual(
            self.client.delete(
                "/cockpit/api/chat" + path,
                json={"confirm": True},
                headers=self.headers(Origin="https://other.test"),
            ).status_code,
            403,
        )
        other_cid = create(self.service, self.actor)
        self.assertEqual(
            self.api(
                f"/conversations/{other_cid}/files/{fid}",
                "DELETE",
                json={"confirm": True},
            ).status_code,
            404,
        )
        old_actor = self.actor
        for actor in (access(13), access(99, role=100)):
            self.actor = actor
            self.assertEqual(
                self.api(path, "DELETE", json={"confirm": True}).status_code, 404
            )
        self.actor = old_actor
        before = self.api(f"/conversations/{self.cid}/events").json["events"]
        response = self.api(path, "DELETE", json={"confirm": True})
        self.assertEqual(response.status_code, 200, response.json)
        self.assertTrue(response.json["file"]["deleted_at"])
        self.assertEqual(
            self.api(path, "DELETE", json={"confirm": True}).json, response.json
        )
        self.assertEqual(self.api(path + "/download").status_code, 410)
        self.assertEqual(
            self.api(f"/conversations/{self.cid}/events").json["events"], before
        )
        self.assertTrue(
            self.api(f"/conversations/{self.cid}/files").json["files"][0]["deleted_at"]
        )
        self.assertEqual(self.stream.calls, [])


if __name__ == "__main__":
    unittest.main()
