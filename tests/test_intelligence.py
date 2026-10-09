"""Stateless model challenge: all paid calls are mocked, no live credentials."""

from contextlib import nullcontext
import json
import os
import unittest
from unittest.mock import MagicMock, patch

from database import isolated_schema
from session_fixture import fixture_identity, session_auth
from new_api_cockpit import intelligence as intel
from new_api_cockpit.app import app, safe_next

HTML = '<html><body><svg><circle r="2"/></svg><script>window.animation=1</script></body></html>'
ACTOR = {"id": 1, "username": "admin", "role": 100, "status": 1}
RAW_KEY = "A" * 48
KEY = "sk-" + RAW_KEY
CHOICE = {
    "model": "model-a",
    "group": "g",
    "endpoint": "openai",
    "channel_id": 25,
    "channel_type": 1,
}


def token(**changes):
    return dict(
        id=11,
        name=intel.KEY_NAME,
        status=1,
        expired_time=-1,
        remain_quota=1234,
        unlimited_quota=False,
        group="g",
        allow_ips="127.0.0.1",
        model_limits_enabled=True,
        model_limits="model-a",
        cross_group_retry=False,
        auto_groups=None,
        **changes,
    )


class IntelligenceTest(unittest.TestCase):
    def test_catalog_uses_enabled_channels_owner_groups_and_text_endpoints_without_writes(
        self,
    ):
        conn = MagicMock()
        conn.__enter__.return_value = conn
        conn.execute.return_value = [
            {
                "id": 25,
                "type": 14,
                "models": "model-a,image-only,foreign",
                "group": "g,other",
            },
            {"id": 26, "type": 1, "models": "model-a,model-b", "group": "own"},
        ]
        catalog = {
            "success": True,
            "data": {
                "page": 1,
                "total": 5,
                "items": [
                    {
                        "model_name": "model-a",
                        "supported_endpoints": ["openai-response", "openai"],
                    },
                    {"model_name": "model-b", "supported_endpoints": ["anthropic"]},
                    {
                        "model_name": "image-only",
                        "supported_endpoints": ["image-generation"],
                    },
                    {"model_name": "foreign", "supported_endpoints": ["openai"]},
                    {
                        "model_name": "disabled-or-price-only",
                        "supported_endpoints": ["openai"],
                    },
                ],
            },
        }
        with (
            patch.object(intel.management, "actor", return_value=ACTOR),
            patch.object(
                intel.management, "target", return_value={**ACTOR, "group": "own"}
            ),
            patch.object(
                intel.management, "selectable_groups", return_value=["own", "g"]
            ),
            patch.object(intel.quota, "connect", return_value=conn),
            patch.object(intel, "upstream", return_value=catalog) as api,
            patch.object(intel.management, "owner_pat") as pat,
            patch.object(intel.management, "single_action") as write,
        ):
            result = intel.model_options(ACTOR, "fixture-session")
        self.assertEqual(
            [r["model"] for r in result["rows"]], ["foreign", "model-a", "model-b"]
        )
        self.assertEqual(result["rows"][1]["group"], "own")
        self.assertEqual(result["rows"][1]["endpoint"], "openai")
        self.assertEqual(result["rows"][1]["channel_id"], 26)
        self.assertEqual(result["rows"][0]["endpoint"], "anthropic")
        self.assertEqual(result["rows"][2]["endpoint"], "openai")
        self.assertIn("WHERE status=1", conn.execute.call_args.args[0])
        self.assertIn(
            "ORDER BY COALESCE(priority,0) DESC,id", conn.execute.call_args.args[0]
        )
        self.assertIn("/api/models/?include_channel_models=true", api.call_args.args[0])
        pat.assert_not_called()
        write.assert_not_called()

    def test_mixed_protocol_channels_use_highest_priority_route_not_model_name(self):
        for first_type, endpoint in ((14, "anthropic"), (1, "openai"), (24, "openai")):
            conn = MagicMock()
            conn.__enter__.return_value = conn
            # Query returns descending priority: the first route wins within a group.
            conn.execute.return_value = [
                {"id": 25, "type": first_type, "models": "claude-model", "group": "g"},
                {"id": 26, "type": 14, "models": "claude-model", "group": "g"},
            ]
            with (
                self.subTest(channel_type=first_type),
                patch.object(intel.management, "actor", return_value=ACTOR),
                patch.object(
                    intel.management, "target", return_value={**ACTOR, "group": "g"}
                ),
                patch.object(intel.management, "selectable_groups", return_value=["g"]),
                patch.object(intel.quota, "connect", return_value=conn),
                patch.object(
                    intel,
                    "model_catalog",
                    return_value=[
                        {
                            "model_name": "claude-model",
                            "supported_endpoints": ["openai", "anthropic"],
                        }
                    ],
                ),
            ):
                row = intel.model_options(ACTOR, "fixture-session")["rows"][0]
            self.assertEqual(row["endpoint"], endpoint)
            self.assertEqual(row["channel_id"], 25)
            self.assertEqual(row["channel_type"], first_type)

    def test_admin_catalog_reads_every_page_and_rejects_partial_results(self):
        first = {
            "success": True,
            "data": {"page": 1, "total": 101, "items": [{}] * 100},
        }
        second = {"success": True, "data": {"page": 2, "total": 101, "items": [{}]}}
        with patch.object(intel, "upstream", side_effect=[first, second]) as api:
            self.assertEqual(len(intel.model_catalog("fixture-session", 1)), 101)
            self.assertEqual(api.call_count, 2)
            self.assertIn("page=2", api.call_args.args[0])
        incomplete = {"success": True, "data": {"page": 2, "total": 101, "items": []}}
        with (
            patch.object(intel, "upstream", side_effect=[first, incomplete]),
            self.assertRaises(intel.Unavailable),
        ):
            intel.model_catalog("fixture-session", 1)

    def test_existing_key_reused_and_only_group_model_fields_updated(self):
        configured = token()
        old = {**configured, "group": "old", "model_limits": "old-model"}
        with (
            patch.object(intel, "_find_token", return_value=11),
            patch.object(
                intel.management,
                "call_api",
                side_effect=[old, configured, {"key": RAW_KEY}],
            ) as api,
            patch.object(intel.management, "single_action") as write,
        ):
            self.assertEqual(intel.test_key(ACTOR, CHOICE), KEY)
        self.assertEqual(write.call_count, 1)
        changes = write.call_args.args[3]
        self.assertEqual(changes["action"], "edit")
        self.assertEqual(changes["changes"]["group"], "g")
        for field in (
            "remain_quota",
            "unlimited_quota",
            "expired_time",
            "status",
            "allow_ips",
        ):
            self.assertNotIn(field, changes["changes"])
        self.assertTrue(all(call.args[1] == ACTOR["id"] for call in api.call_args_list))

    def test_missing_key_created_once_with_clear_name_and_never_deleted(self):
        with (
            patch.object(intel, "_find_token", side_effect=[None, 11]),
            patch.object(
                intel.management,
                "call_api",
                side_effect=[token(), token(), {"key": RAW_KEY}],
            ) as api,
            patch.object(intel.management, "single_action") as write,
        ):
            intel.test_key(ACTOR, CHOICE)
        write.assert_called_once()
        self.assertEqual(write.call_args.args[:3], ("admin", "token", 1))
        creation = write.call_args.args[3]
        self.assertEqual(creation["action"], "create")
        self.assertEqual(creation["changes"]["name"], intel.KEY_NAME)
        self.assertLessEqual(len(intel.KEY_NAME.encode()), 50)
        self.assertEqual(creation["changes"]["expired_time"], -1)
        self.assertTrue(creation["changes"]["unlimited_quota"])
        self.assertTrue(
            all(call.kwargs.get("method") != "DELETE" for call in api.call_args_list)
        )

    def test_key_accepts_bare_or_prefixed_value_but_not_masked_or_channel_suffix(self):
        for value in (
            RAW_KEY,
            KEY,
            "",
            "sk-",
            None,
            1,
            "A" * 129,
            "sk-A-25",
            "A.25",
            "A\nB",
            "A B",
            "A***B",
        ):
            with (
                self.subTest(value_type=type(value).__name__),
                patch.object(intel, "_find_token", return_value=11),
                patch.object(
                    intel.management,
                    "call_api",
                    side_effect=[token(), token(), {"key": value}],
                ),
                patch.object(intel.management, "single_action") as write,
            ):
                if value in (RAW_KEY, KEY):
                    self.assertEqual(intel.test_key(ACTOR, CHOICE), KEY)
                else:
                    with self.assertRaises(intel.Unavailable) as caught:
                        intel.test_key(ACTOR, CHOICE)
                    self.assertEqual(str(caught.exception), "测试 KEY 响应无效。")
                write.assert_not_called()

    def test_disabled_expired_empty_or_duplicate_key_is_not_silently_recreated(self):
        for changes in (
            {"status": 2},
            {"expired_time": 1},
            {"remain_quota": 0},
            {"name": "renamed-user-key"},
        ):
            with (
                self.subTest(changes=changes),
                patch.object(intel, "_find_token", return_value=11),
                patch.object(
                    intel.management, "call_api", return_value={**token(), **changes}
                ),
                patch.object(intel.management, "single_action") as write,
                self.assertRaises(intel.management.Conflict),
            ):
                intel.test_key(ACTOR, CHOICE)
            write.assert_not_called()
        conn = MagicMock()
        conn.__enter__.return_value = conn
        conn.execute.return_value.fetchall.return_value = [{"id": 11}, {"id": 12}]
        with (
            patch.object(intel.quota, "connect", return_value=conn),
            self.assertRaises(intel.management.Conflict),
        ):
            intel._find_token(1)

    def test_protocols_fixed_prompt_and_output_extraction_do_not_include_reasoning(
        self,
    ):
        cases = [
            (
                "openai",
                {"choices": [{"message": {"content": HTML}, "finish_reason": "stop"}]},
            ),
            (
                "anthropic",
                {
                    "content": [
                        {"type": "thinking", "thinking": "secret-thought"},
                        {"type": "text", "text": HTML},
                    ],
                    "stop_reason": "end_turn",
                },
            ),
        ]
        for endpoint, response in cases:
            with self.subTest(endpoint=endpoint):
                self.assertEqual(intel.generated_text(response, endpoint), (HTML, None))
                path, payload, headers = intel.generation_request(
                    {**CHOICE, "endpoint": endpoint}
                )
                self.assertEqual(
                    path,
                    "/v1/messages"
                    if endpoint == "anthropic"
                    else "/v1/chat/completions",
                )
                self.assertEqual(
                    headers.get("anthropic-version"),
                    "2023-06-01" if endpoint == "anthropic" else None,
                )
                self.assertIn(intel.PROMPT, json.dumps(payload, ensure_ascii=False))
                self.assertNotIn("sk-fixture", json.dumps(payload))
                self.assertNotIn("temperature", payload)
                for field in (
                    "max_tokens",
                    "max_completion_tokens",
                    "max_output_tokens",
                ):
                    self.assertNotIn(field, payload)
        self.assertTrue(
            intel.generated_text({"choices": [{"finish_reason": "length"}]}, "openai")[
                1
            ]
        )
        self.assertEqual(intel.generated_text({"choices": None}, "openai"), ("", None))
        self.assertEqual(
            intel.generated_text({"stop_reason": "max_tokens"}, "anthropic"),
            ("", "max_tokens"),
        )
        for endpoint in ("openai-response", "gemini", "unknown"):
            with self.subTest(endpoint=endpoint):
                with self.assertRaises(intel.Unavailable):
                    intel.generation_request({**CHOICE, "endpoint": endpoint})
                with self.assertRaises(intel.GenerationError):
                    intel.generated_text({}, endpoint)

    def test_run_is_single_synchronous_request_with_no_result_id_or_key_in_response(
        self,
    ):
        for endpoint, channel_type, response, path in (
            (
                "openai",
                1,
                {"choices": [{"message": {"content": HTML}}]},
                "/v1/chat/completions",
            ),
            (
                "anthropic",
                14,
                {"content": [{"type": "text", "text": HTML}]},
                "/v1/messages",
            ),
        ):
            choice = {**CHOICE, "endpoint": endpoint, "channel_type": channel_type}
            with (
                self.subTest(endpoint=endpoint),
                patch.object(intel, "exclusive_test", return_value=nullcontext()),
                patch.object(intel, "model_options", return_value={"rows": [choice]}),
                patch.object(intel.management, "actor", return_value=ACTOR),
                patch.object(intel, "test_key", return_value=KEY),
                patch.object(
                    intel,
                    "upstream",
                    side_effect=[{"data": [{"id": "model-a"}]}, response],
                ) as api,
            ):
                result = intel.run(ACTOR, "fixture-pat", {"model": "model-a"})
            self.assertEqual(result["html"], HTML)
            self.assertEqual(result["source"], HTML)
            self.assertEqual(result["channel_id"], 25)
            self.assertEqual(api.call_count, 2)
            self.assertEqual(api.call_args_list[0].args, ("/v1/models", KEY, 1))
            self.assertEqual(api.call_args.args, (path, KEY + "-25", 1))
            self.assertTrue(api.call_args.kwargs["generation"])
            headers = api.call_args.kwargs["headers"]
            self.assertEqual(
                headers.get("x-api-key"),
                KEY + "-25" if endpoint == "anthropic" else None,
            )
            self.assertNotIn(RAW_KEY, json.dumps(result))
            self.assertNotIn("fixture-pat", json.dumps(result))
            self.assertNotIn("id", result)
        for body in (
            {"model": "model-a", "user_id": 2},
            {"model": "model-a", "group": "foreign"},
            {"model": "a,b"},
            {"model": True},
        ):
            with (
                self.subTest(body=body),
                patch.object(intel, "exclusive_test") as lock,
                self.assertRaises(intel.IntelligenceError),
            ):
                intel.run(ACTOR, "fixture-pat", body)
            lock.assert_not_called()

    def test_generation_failure_does_not_retry_or_switch_protocol(self):
        with (
            patch.object(intel, "exclusive_test", return_value=nullcontext()),
            patch.object(intel, "model_options", return_value={"rows": [CHOICE]}),
            patch.object(intel.management, "actor", return_value=ACTOR),
            patch.object(intel, "test_key", return_value=KEY),
            patch.object(
                intel,
                "upstream",
                side_effect=[
                    {"data": [{"id": "model-a"}]},
                    intel.GenerationError("failure"),
                ],
            ) as api,
            self.assertRaises(intel.GenerationError),
        ):
            intel.run(ACTOR, "fixture-pat", {"model": "model-a"})
        self.assertEqual(api.call_count, 2)
        self.assertEqual(api.call_args.args[:2], ("/v1/chat/completions", KEY + "-25"))

    def test_upstream_truncation_keeps_source_and_names_the_actual_stop_reason(self):
        with (
            patch.object(intel, "exclusive_test", return_value=nullcontext()),
            patch.object(
                intel,
                "model_options",
                return_value={
                    "rows": [{**CHOICE, "endpoint": "anthropic", "channel_type": 14}]
                },
            ),
            patch.object(intel.management, "actor", return_value=ACTOR),
            patch.object(intel, "test_key", return_value=KEY),
            patch.object(
                intel,
                "upstream",
                side_effect=[
                    {"data": [{"id": "model-a"}]},
                    {
                        "content": [{"type": "text", "text": HTML}],
                        "stop_reason": "max_tokens",
                    },
                ],
            ) as api,
        ):
            result = intel.run(ACTOR, "fixture-pat", {"model": "model-a"})
        self.assertIsNone(result["html"])
        self.assertEqual(result["source"], HTML)
        self.assertIn("New API 或上游的输出上限", result["warning"])
        self.assertIn("max_tokens", result["warning"])
        self.assertNotIn("8192", result["warning"])
        self.assertEqual(api.call_count, 2)

    def test_html_only_unwraps_fence_and_does_not_repair_drawing_or_animation(self):
        self.assertEqual(intel.extract_html("```html\n" + HTML + "\n```"), HTML)
        self.assertIn("<script>", intel.extract_html(HTML))
        for text in (
            None,
            "",
            "<svg/>",
            "<html></html>",
            "<html><svg>",
            "x" * (intel.MAX_HTML_BYTES + 1),
        ):
            with (
                self.subTest(text=str(text)[:30]),
                self.assertRaises(intel.IntelligenceError),
            ):
                intel.extract_html(text)

    def test_preview_reuses_only_packaged_script_not_generated_documents(self):
        intel._preview_script.cache_clear()
        self.addCleanup(intel._preview_script.cache_clear)
        resource = MagicMock()
        resource.joinpath.return_value.read_text.return_value = "/* fixture sizing */"
        with patch.object(intel, "files", return_value=resource) as package:
            first = intel.preview_document(HTML)
            second_html = HTML.replace('r="2"', 'r="3"')
            second = intel.preview_document(second_html)
        self.assertEqual(first, HTML + "\n<script>/* fixture sizing */</script>")
        self.assertEqual(
            second, second_html + "\n<script>/* fixture sizing */</script>"
        )
        package.assert_called_once_with(intel.__package__)
        resource.joinpath.return_value.read_text.assert_called_once()

    def test_transport_does_not_retry_redirects_timeout_or_bad_json_and_masks_credentials(
        self,
    ):
        for status, content, timeout in [
            (302, b"fixture-secret", False),
            (200, b"bad-json-fixture-secret", False),
            (200, b"{}", True),
        ]:
            conn = MagicMock()
            response = MagicMock()
            response.__enter__.return_value = response
            response.status = status
            response.read1.side_effect = [content, b""]
            conn.getresponse.return_value = response
            if timeout:
                conn.getresponse.side_effect = TimeoutError()
            with (
                self.subTest(status=status, timeout=timeout),
                patch.object(intel, "HTTPConnection", return_value=conn),
                patch.dict(
                    os.environ, {"NEW_API_INTERNAL_URL": "http://fixture.invalid:3000"}
                ),
                self.assertRaises(intel.GenerationError) as caught,
            ):
                intel.upstream(
                    "/v1/chat/completions",
                    "sk-fixture-secret",
                    1,
                    body={},
                    generation=True,
                )
            self.assertNotIn("fixture-secret", str(caught.exception))
            conn.request.assert_called_once()
            conn.close.assert_called_once()

    def test_page_api_auth_preview_sandbox_and_no_history_routes(self):
        with (
            patch("new_api_cockpit.app.request_identity", side_effect=fixture_identity),
            patch("new_api_cockpit.app.load_site_name", return_value="Fixture"),
            patch.object(intel, "run", return_value={"html": HTML}) as run,
            patch.object(
                intel, "model_options", return_value={"rows": [CHOICE]}
            ) as models,
        ):
            client = app.test_client()
            auth = session_auth("admin")
            self.assertEqual(client.get("/cockpit/intelligence/").status_code, 302)
            self.assertEqual(
                client.get("/cockpit/api/intelligence/models").status_code, 401
            )
            page = client.get("/cockpit/intelligence/", auth=auth)
            self.assertEqual(page.status_code, 200)
            self.assertIn(
                'href="/cockpit/intelligence/" aria-current="page"', page.text
            )
            self.assertIn(intel.PROMPT, page.text)
            self.assertNotIn("unsafe-inline", page.headers["Content-Security-Policy"])
            self.assertEqual(
                safe_next("/cockpit/intelligence/"), "/cockpit/intelligence/"
            )
            client.get("/cockpit/api/intelligence/models", auth=auth)
            self.assertEqual(models.call_args.args[0]["id"], 1)
            self.assertEqual(
                client.post(
                    "/cockpit/api/intelligence/test",
                    auth=auth,
                    json={"model": "model-a"},
                ).status_code,
                403,
            )
            run.assert_not_called()
            headers = {"X-Intelligence-Request": "1"}
            self.assertEqual(
                client.post(
                    "/cockpit/api/intelligence/test",
                    auth=auth,
                    json={"model": "model-a"},
                    headers={**headers, "Origin": "https://other.invalid"},
                ).status_code,
                403,
            )
            self.assertEqual(
                client.post(
                    "/cockpit/api/intelligence/test",
                    auth=auth,
                    json={"model": "model-a"},
                    headers=headers,
                ).status_code,
                200,
            )
            run.assert_called_once()
            preview = client.post(
                "/cockpit/api/intelligence/preview",
                auth=auth,
                json={"html": HTML},
                headers=headers,
            )
            self.assertEqual(preview.status_code, 200)
            self.assertEqual(preview.mimetype, "text/html")
            self.assertTrue(preview.text.startswith(HTML))
            self.assertIn("cockpit-intelligence-size", preview.text)
            self.assertNotIn("allow-same-origin", preview.text)
            csp = preview.headers["Content-Security-Policy"]
            for rule in (
                "sandbox allow-scripts",
                "connect-src 'none'",
                "default-src 'none'",
                "frame-ancestors 'self'",
            ):
                self.assertIn(rule, csp)
            self.assertNotIn("allow-same-origin", csp)
            form = {"html": HTML, "session_id": "fixture-session"}
            self.assertEqual(
                client.post(
                    "/cockpit/api/intelligence/preview",
                    auth=auth,
                    data=form,
                    headers={"Origin": "http://localhost"},
                ).status_code,
                200,
            )
            # Form encoding must not reduce the documented HTML byte limit.
            large_html = HTML.replace("<body>", "<body><!--" + "<" * 200_000 + "-->")
            self.assertEqual(
                client.post(
                    "/cockpit/api/intelligence/preview",
                    auth=auth,
                    data={**form, "html": large_html},
                    headers={"Origin": "http://localhost"},
                ).status_code,
                200,
            )
            self.assertEqual(
                client.post(
                    "/cockpit/api/intelligence/preview", auth=auth, data=form
                ).status_code,
                403,
            )
            self.assertEqual(
                client.post(
                    "/cockpit/api/intelligence/preview",
                    auth=auth,
                    data={**form, "session_id": "wrong"},
                    headers={"Origin": "http://localhost"},
                ).status_code,
                403,
            )
            self.assertEqual(
                client.get("/cockpit/api/intelligence/preview", auth=auth).status_code,
                405,
            )
            self.assertEqual(
                client.get("/cockpit/api/intelligence/runs", auth=auth).status_code, 404
            )


@unittest.skipUnless(
    os.environ.get("MONITOR_DATABASE_URL"), "Requires disposable PostgreSQL"
)
class IntelligenceLockTest(unittest.TestCase):
    def test_lock_is_shared_across_connections_and_released_on_failure(self):
        with (
            isolated_schema() as connect,
            patch.object(intel.balance, "connect", connect),
            patch.object(intel.management, "_audit_ready"),
        ):
            with intel.exclusive_test(1):
                with self.assertRaises(intel.management.Conflict):
                    with intel.exclusive_test(1):
                        self.fail("same user must not run concurrently")
                with intel.exclusive_test(2):
                    pass
            with self.assertRaises(RuntimeError):
                with intel.exclusive_test(1):
                    raise RuntimeError("fixture failure")
            with intel.exclusive_test(1):
                pass


if __name__ == "__main__":
    unittest.main()
