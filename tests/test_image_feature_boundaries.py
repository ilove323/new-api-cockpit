"""Equivalent image boundaries; no external services or paid requests."""

import ast
from contextlib import contextmanager, nullcontext
import importlib
import importlib.util
from io import BytesIO
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import MagicMock, patch

from flask import Flask, abort, g, request
from PIL import Image
import psycopg

from new_api_cockpit.features.image_studio import (
    capacity,
    contracts,
    http,
    receipts,
    results,
    service,
    transport,
)
from new_api_cockpit.platform.host import HostServices
from new_api_cockpit.platform.identity import Principal, RequestAccess

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src"
FEATURE = SOURCE / "new_api_cockpit/features/image_studio"
ACCESS = RequestAccess(
    Principal(12, "fixture", session_id="fixture-session"), "fixture-credential"
)
PREFIX = "/cockpit/api/image-studio/"
HEADER = {"X-Cockpit-Studio": "1"}


def png():
    output = BytesIO()
    Image.new("RGB", (8, 7), (10, 20, 30)).save(output, "PNG")
    return output.getvalue()


class ImageFeatureBoundariesTest(unittest.TestCase):
    def test_imports_stay_inside_feature_or_platform_including_decoder_source(self):
        def check(tree, package, label):
            targets = []
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    targets.extend(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom):
                    base = (
                        importlib.util.resolve_name(
                            "." * node.level + (node.module or ""), package
                        )
                        if node.level
                        else node.module or ""
                    )
                    targets.extend(
                        [base, *(base + "." + alias.name for alias in node.names)]
                    )
                elif isinstance(node, ast.Call):
                    name = (
                        node.func.id
                        if isinstance(node.func, ast.Name)
                        else getattr(node.func, "attr", "")
                    )
                    self.assertNotIn(name, {"__import__", "import_module"}, label)
                    # Redis client.eval is the existing atomic Lua receipt
                    # transaction, not Python's eval/import escape hatch.
                    if isinstance(node.func, ast.Name):
                        self.assertNotIn(name, {"exec", "eval"}, label)
            for target in targets:
                if target.startswith("new_api_cockpit."):
                    with self.subTest(file=label, target=target):
                        self.assertTrue(
                            target.startswith(
                                (
                                    "new_api_cockpit.features.image_studio",
                                    "new_api_cockpit.platform",
                                )
                            ),
                            target,
                        )

        for path in FEATURE.glob("*.py"):
            check(
                ast.parse(path.read_text()),
                "new_api_cockpit.features.image_studio",
                str(path),
            )
        check(ast.parse(results._DECODE_CODE), "", "decoder child")

    def test_legacy_imports_alias_one_implementation_and_patch_target(self):
        for old, implementation in (
            ("image_studio", service),
            ("image_contracts", contracts),
            ("image_transport", transport),
            ("image_results", results),
            ("image_receipts", receipts),
        ):
            with self.subTest(module=old):
                self.assertIs(
                    importlib.import_module("new_api_cockpit." + old), implementation
                )
                with patch.object(
                    implementation, "fixture_patch", object(), create=True
                ):
                    self.assertIs(
                        sys.modules["new_api_cockpit." + old].fixture_patch,
                        implementation.fixture_patch,
                    )

    def test_fresh_import_configure_and_blueprint_do_not_connect_or_import_business(
        self,
    ):
        script = r"""
import json, sys
sys.path.insert(0, sys.argv[1])
from unittest.mock import MagicMock, patch
import psycopg, redis, socket
with patch.object(psycopg, "connect", side_effect=AssertionError("database I/O")), \
     patch.object(redis.Redis, "from_url", side_effect=AssertionError("redis I/O")), \
     patch.object(socket.socket, "connect", side_effect=AssertionError("network I/O")):
    from new_api_cockpit.features.image_studio import service, http
    tokens, source, host = MagicMock(), MagicMock(), MagicMock()
    service.configure(tokens, source)
    assert http.create_blueprint(host).name == "image_studio"
    assert not tokens.mock_calls and not source.mock_calls and not host.mock_calls
modules = sorted(name for name in sys.modules if name.startswith("new_api_cockpit.")
                 and not name.startswith(("new_api_cockpit.features", "new_api_cockpit.platform")))
print(json.dumps(modules))
"""
        with tempfile.TemporaryDirectory() as cwd:
            child = subprocess.run(
                [sys.executable, "-I", "-c", script, str(SOURCE)],
                cwd=cwd,
                env={},
                capture_output=True,
                timeout=10,
                check=True,
            )
        self.assertEqual(json.loads(child.stdout), [])

    def test_unconfigured_dependencies_fail_closed_without_implicit_adapter(self):
        with patch.object(service, "_dependencies", None):
            with self.assertRaises(service.StudioError) as caught:
                service.list_tokens(ACCESS)
            self.assertEqual(caught.exception.status, 503)
        with self.assertRaises(TypeError):
            service.configure(object(), object())

    def test_injected_key_lease_survives_until_finalization_and_closes_on_errors(self):
        from test_image_contracts import configuration, route

        config = configuration()
        routes = [route()]
        revision = service._contract("gpt-image-2", routes, config)["revision"]
        body = {
            "token_id": 7,
            "model": "gpt-image-2",
            "prompt": "fixture",
            "parameters": {},
            "request_id": "fixture-lease-1",
            "contract_revision": revision,
            "intent": {
                "operation": "generate",
                "has_annotations": False,
                "reference_roles": [],
            },
        }
        for error, terminal in (
            (None, "succeeded"),
            (transport.ImageError("fixture", uncertain=True), "unknown"),
            (RuntimeError("must-not-leak"), "unknown"),
        ):
            order, active = [], []
            tokens, receipt = MagicMock(), MagicMock()
            tokens.list_models.return_value = [{"id": "gpt-image-2", "protocols": []}]

            @contextmanager
            def lease(*args, **kwargs):
                order.append("lease")
                active.append(True)
                try:
                    yield "sk-fixture-only"
                finally:
                    active.clear()
                    order.append("release")

            def claim(*args, **kwargs):
                self.assertTrue(active)
                order.append("claim")
                return receipt

            def send(path, key, **kwargs):
                self.assertTrue(active)
                self.assertEqual(
                    (path, key), ("/v1/images/generations", "sk-fixture-only")
                )
                order.append("send")
                if error:
                    raise error
                return {"images": []}

            def finish(state):
                self.assertTrue(active)
                self.assertEqual(state, terminal)
                order.append("finish")

            tokens.lease_key.side_effect = lease
            receipt.finish.side_effect = finish
            with (
                self.subTest(error=type(error).__name__),
                patch.object(service, "_dependencies", (tokens, MagicMock())),
                patch.object(service, "_configuration", return_value=config),
                patch.object(service, "_routes", return_value=routes),
                patch.object(receipts, "claim", side_effect=claim),
                patch.object(transport, "request_image", side_effect=send) as paid,
            ):
                if error:
                    with self.assertRaises(service.StudioError) as caught:
                        service.run(ACCESS, None, body)
                    self.assertNotIn("must-not-leak", str(caught.exception))
                else:
                    self.assertEqual(service.run(ACCESS, None, body)["images"], [])
                paid.assert_called_once()
            tokens.list_models.assert_called_once_with(ACCESS, 7)
            tokens.lease_key.assert_called_once_with(ACCESS, 7)
            self.assertEqual(order, ["lease", "claim", "send", "finish", "release"])
            self.assertFalse(active)

    def test_real_isolated_decoder_loads_new_module_from_correct_source_root(self):
        marker = "from new_api_cockpit.features.image_studio.transport import _normalize_image"
        proof = (
            marker
            + """
assert _normalize_image.__module__ == "new_api_cockpit.features.image_studio.transport"
from pathlib import Path
assert Path(sys.modules[_normalize_image.__module__].__file__).resolve() == Path(sys.argv[1]).resolve() / "new_api_cockpit/features/image_studio/transport.py"
assert "new_api_cockpit.image_transport" not in sys.modules
"""
        )
        self.assertIn(marker, results._DECODE_CODE)
        real_run = subprocess.run
        with (
            patch.object(
                results, "_DECODE_CODE", results._DECODE_CODE.replace(marker, proof)
            ),
            patch.object(results.subprocess, "run", wraps=real_run) as child,
        ):
            image = results._normalize_bounded(png(), time.monotonic() + 10)
        self.assertEqual(
            (image["width"], image["height"], image["mime"]), (8, 7, "image/png")
        )
        self.assertEqual(child.call_args.args[0][1], "-I")
        # Match the imported package in both editable checkouts and installed
        # wheels; CI and production do not run from the checkout's src folder.
        self.assertEqual(
            Path(child.call_args.args[0][4]).resolve(),
            Path(transport.__file__).resolve().parents[3],
        )
        self.assertEqual(child.call_args.kwargs["env"], {})
        self.assertNotIn(ACCESS.credential, str(child.call_args))


class ImageFeatureHttpTest(unittest.TestCase):
    def setUp(self):
        self.app = Flask(
            __name__, template_folder=str(SOURCE / "new_api_cockpit/templates")
        )
        self.app.testing = True
        self.access = MagicMock(return_value=ACCESS)

        def validate(header, content_type):
            if request.headers.get("Origin", "http://localhost") != "http://localhost":
                abort(403, "fixture origin rejected")
            self.assertEqual(request.headers.get(header), "1")
            self.assertEqual(request.mimetype, content_type)

        self.validate = MagicMock(side_effect=validate)
        self.host = HostServices(self.access, self.validate, lambda: "Fixture")

        @self.app.before_request
        def identity():
            g.current_user = ACCESS.principal.as_user()

        self.app.register_blueprint(http.create_blueprint(self.host))
        self.client = self.app.test_client()

    def test_exact_urls_endpoints_methods_and_role_sets(self):
        expected = {
            "/cockpit/image-studio": ("image_studio.page", {"GET", "HEAD", "OPTIONS"}),
            "/cockpit/image-studio/": ("image_studio.page", {"GET", "HEAD", "OPTIONS"}),
            PREFIX + "tokens": ("image_studio.tokens", {"GET", "HEAD", "OPTIONS"}),
            **{
                PREFIX + name: ("image_studio." + name, {"POST", "OPTIONS"})
                for name in ("models", "generations", "edits")
            },
        }
        actual = {
            rule.rule: (rule.endpoint, rule.methods)
            for rule in self.app.url_map.iter_rules()
            if rule.endpoint.startswith("image_studio.")
        }
        self.assertEqual(actual, expected)
        self.assertEqual(http.USER_ENDPOINTS, {value[0] for value in expected.values()})
        self.assertEqual(http.PAGE_ENDPOINTS, {"image_studio.page"})
        self.assertEqual(
            http.ROLE_ENDPOINTS["image"],
            {"image_studio.generations", "image_studio.edits"},
        )
        self.assertEqual(
            http.ROLE_ENDPOINTS["web"],
            {"image_studio.page", "image_studio.tokens", "image_studio.models"},
        )
        self.assertEqual(http.ROLE_ENDPOINTS["chat"], set())

    def test_page_keeps_current_resources_after_template_move(self):
        page = self.client.get("/cockpit/image-studio/")
        self.assertEqual(page.status_code, 200)
        self.assertIn("Fixture · 生图工作站", page.text)
        self.assertIn("/cockpit/static/studio.js", page.text)
        self.validate.assert_not_called()

    def test_tokens_models_forward_only_verified_access(self):
        with patch.object(
            service, "list_tokens", return_value={"tokens": []}
        ) as tokens:
            self.assertEqual(self.client.get(PREFIX + "tokens").json, {"tokens": []})
        tokens.assert_called_once_with(ACCESS)
        with patch.object(service, "models", return_value={"models": []}) as models:
            reply = self.client.post(
                PREFIX + "models", json={"token_id": 7}, headers=HEADER
            )
        self.assertEqual(reply.json, {"models": []})
        models.assert_called_once_with(ACCESS, None, {"token_id": 7})
        self.validate.assert_called_once_with("X-Cockpit-Studio", "application/json")

    def test_invalid_token_selector_keeps_legacy_error_body(self):
        for body in (
            {"token_id": True},
            {"token_id": 0},
            {"token_id": "7"},
            {"token_id": 7, "user_id": 99},
            None,
        ):
            with self.subTest(body=body):
                reply = self.client.post(
                    PREFIX + "models",
                    data=json.dumps(body),
                    content_type="application/json",
                    headers=HEADER,
                )
            self.assertEqual(reply.status_code, 400)
            self.assertEqual(
                reply.json,
                {
                    "error": "请选择有效的令牌；不接受用户 ID 或上游地址。",
                    "code": "MANUAL_ERROR",
                },
            )

    def test_adapter_reason_restores_original_image_error_contract(self):
        expected = {
            "model_limits_enabled": "令牌模型限制信息无效，请检查 New API 版本。",
            "model_limits": "令牌模型限制响应无效。",
            "model_catalogue": "New API 图片模型目录响应无效。",
        }
        for reason, message in expected.items():
            error = ValueError("adapter internal wording must not leak")
            error.reason = reason
            tokens = MagicMock()
            tokens.list_models.side_effect = error
            with (
                self.subTest(reason=reason),
                patch.object(service, "_dependencies", (tokens, MagicMock())),
            ):
                reply = self.client.post(
                    PREFIX + "models", json={"token_id": 7}, headers=HEADER
                )
            self.assertEqual(reply.status_code, 502)
            self.assertEqual(
                reply.json,
                {
                    "error": message,
                    "code": "IMAGE_STUDIO_ERROR",
                    "uncertain": False,
                    "details": {},
                },
            )

    def test_json_suffix_is_still_accepted(self):
        with patch.object(service, "models", return_value={"models": []}):
            reply = self.client.post(
                PREFIX + "models",
                data='{"token_id":7}',
                content_type="application/vnd.api+json",
                headers=HEADER,
            )
        self.assertEqual(reply.status_code, 200)
        self.validate.assert_called_once_with(
            "X-Cockpit-Studio", "application/vnd.api+json"
        )

    def test_capacity_busy_header_mime_origin_and_size_reject_before_parse(self):
        with (
            patch.object(
                service, "capacity", side_effect=service.StudioError("busy", 429)
            ) as lease,
            patch.object(http, "studio_edit_body") as parse,
            patch.object(service, "run") as send,
        ):
            reply = self.client.post(
                PREFIX + "edits",
                data={"junk": "ignored"},
                content_type="multipart/form-data",
                headers=HEADER,
            )
            self.assertEqual(reply.status_code, 429)
            parse.assert_not_called()
            for headers, content_type, status in (
                ({}, "multipart/form-data", 403),
                (HEADER, "application/json", 400),
                (
                    {**HEADER, "Origin": "https://other.invalid"},
                    "multipart/form-data",
                    403,
                ),
            ):
                reply = self.client.post(
                    PREFIX + "edits",
                    data="{}",
                    content_type=content_type,
                    headers=headers,
                )
                self.assertEqual(reply.status_code, status)
            self.assertEqual(lease.call_count, 1)
            parse.assert_not_called()
            send.assert_not_called()
        with patch.object(service, "models") as models:
            reply = self.client.post(
                PREFIX + "models",
                json={"token_id": 7, "padding": "x" * 1100},
                headers=HEADER,
            )
        self.assertEqual(reply.status_code, 413)
        self.assertEqual(
            (reply.json["code"], reply.json["uncertain"]), ("IMAGE_TOO_LARGE", False)
        )
        models.assert_not_called()

    def test_generation_keeps_capacity_then_service_then_release(self):
        order = []
        lease = MagicMock()
        lease.__enter__.side_effect = lambda: order.append("lock")
        lease.__exit__.side_effect = lambda *args: order.append("release")

        def run(access, credential, body):
            self.assertIs(access, ACCESS)
            self.assertIsNone(credential)
            self.assertEqual(body, {"token_id": 7})
            order.append("run")
            return {"images": [], "request_id": "fixture-request"}

        with (
            patch.object(service, "capacity", return_value=lease) as capacity_call,
            patch.object(service, "run", side_effect=run),
        ):
            reply = self.client.post(
                PREFIX + "generations", json={"token_id": 7}, headers=HEADER
            )
        capacity_call.assert_called_once_with(ACCESS.principal.as_user())
        self.assertEqual(reply.json, {"images": [], "request_id": "fixture-request"})
        self.assertEqual(order, ["lock", "run", "release"])

    def test_studio_receipt_adapter_and_http_error_shapes(self):
        errors = (
            service.StudioError(
                "fixture",
                409,
                "IMAGE_CONTRACT_CHANGED",
                details={"category": "conflict"},
            ),
            receipts.ReceiptError(
                "fixture",
                code="IMAGE_REQUEST_DUPLICATE",
                status=409,
                uncertain=True,
                details={"category": "duplicate", "receipt_state": "unknown"},
            ),
        )
        for error in errors:
            with (
                self.subTest(error=type(error).__name__),
                patch.object(service, "run", side_effect=error),
                patch.object(service, "capacity", return_value=nullcontext()),
            ):
                reply = self.client.post(
                    PREFIX + "generations", json={}, headers=HEADER
                )
            self.assertEqual(reply.status_code, error.status)
            self.assertEqual(
                reply.json,
                {
                    "error": str(error),
                    "code": error.code,
                    "uncertain": error.uncertain,
                    "details": error.details,
                },
            )

        class AdapterError(ValueError):
            status, code = 403, "MANUAL_ERROR"

        with patch.object(service, "models", side_effect=AdapterError("owner only")):
            reply = self.client.post(
                PREFIX + "models", json={"token_id": 7}, headers=HEADER
            )
        self.assertEqual(
            (reply.status_code, reply.json),
            (403, {"error": "owner only", "code": "MANUAL_ERROR"}),
        )
        for endpoint, attr, uncertain in (
            ("models", "models", False),
            ("generations", "run", True),
        ):
            with (
                patch.object(service, attr, side_effect=lambda *a, **k: abort(503)),
                patch.object(service, "capacity", return_value=nullcontext()),
            ):
                reply = self.client.post(PREFIX + endpoint, json={}, headers=HEADER)
            self.assertEqual(reply.status_code, 503)
            self.assertEqual(reply.json["code"], "IMAGE_INVALID_REQUEST")
            self.assertIs(reply.json["uncertain"], uncertain)


class ImageCapacityMoveTest(unittest.TestCase):
    def setUp(self):
        self.conn = MagicMock()
        self.enterContext(patch.dict(os.environ, {"COCKPIT_PROCESS_ROLE": "image"}))
        self.enterContext(
            patch.object(capacity, "_monitor_connection", return_value=self.conn)
        )
        self.enterContext(patch.object(capacity, "_capacity_failed", False))
        self.enterContext(patch.object(capacity, "_database_epoch", None))

    def test_same_lock_numbers_one_user_two_slots_and_nonblocking_release(self):
        self.assertEqual(
            (
                capacity.IMAGE_STUDIO_USER_NAMESPACE,
                capacity.IMAGE_STUDIO_SLOT_NAMESPACE,
            ),
            (90216327, 90216328),
        )
        self.conn.execute.return_value.fetchone.side_effect = [
            ("epoch",),
            (True,),
            (False,),
            (True,),
        ]
        with capacity.capacity(ACCESS.principal.as_user()):
            pass
        queries = [
            (c.args[0], c.args[1] if len(c.args) > 1 else None)
            for c in self.conn.execute.call_args_list
        ]
        self.assertEqual(
            [p for sql, p in queries if "pg_try_advisory_lock" in sql],
            [(90216327, 12), (90216328, 0), (90216328, 1)],
        )
        self.assertEqual(
            [p for sql, p in queries if "pg_advisory_unlock" in sql],
            [(90216328, 1), (90216327, 12)],
        )
        self.assertTrue(all("pg_advisory_lock(" not in sql for sql, _ in queries))
        self.conn.close.assert_called_once()

    def test_user_busy_global_exhaustion_release_and_no_additional_slot(self):
        for rows, unlocks in (
            ([("epoch",), (False,)], 0),
            ([("epoch",), (True,), (False,), (False,)], 1),
        ):
            self.conn.reset_mock()
            self.conn.execute.return_value.fetchone.side_effect = rows
            with (
                self.subTest(rows=rows),
                self.assertRaises(service.StudioError) as caught,
            ):
                with capacity.capacity(ACCESS.principal.as_user()):
                    self.fail("busy must not enter")
            self.assertEqual(
                (caught.exception.status, caught.exception.code),
                (429, "IMAGE_STUDIO_BUSY"),
            )
            self.assertEqual(
                sum(
                    "pg_advisory_unlock" in c.args[0]
                    for c in self.conn.execute.call_args_list
                ),
                unlocks,
            )
            self.conn.close.assert_called_once()

    def test_database_epoch_change_and_database_failure_stay_failed_closed(self):
        self.conn.execute.return_value.fetchone.return_value = ("first",)
        capacity._check_epoch(self.conn)
        self.conn.execute.return_value.fetchone.return_value = ("second",)
        with self.assertRaises(service.StudioError):
            capacity._check_epoch(self.conn)
        self.assertTrue(capacity._capacity_failed)
        self.conn.execute.return_value.fetchone.return_value = ("first",)
        with self.assertRaises(service.StudioError):
            capacity._check_epoch(self.conn)
        with (
            patch.object(capacity, "_capacity_failed", False),
            patch.object(
                capacity,
                "_monitor_connection",
                side_effect=psycopg.OperationalError("fixture DSN must not leak"),
            ),
        ):
            with self.assertRaises(service.StudioError) as caught:
                with capacity.capacity(ACCESS.principal.as_user()):
                    self.fail("database failure must not enter")
            self.assertEqual(caught.exception.status, 503)
            self.assertNotIn("DSN", str(caught.exception))
            self.assertTrue(capacity._capacity_failed)


if __name__ == "__main__":
    unittest.main()
