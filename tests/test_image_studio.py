"""Image authorization, compilation and receipt regressions; all I/O is mocked."""

from contextlib import nullcontext
from copy import deepcopy
from io import BytesIO
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import psycopg
from PIL import Image

from new_api_cockpit import (
    image_contracts,
    image_receipts,
    image_studio as studio,
    image_transport,
    manual,
)
from new_api_cockpit.app import app, safe_next
from test_image_contracts import configuration, route

OWNER = {
    "id": 12,
    "username": "member",
    "role": 1,
    "session_id": "fixture-session",
    "expires_at": 4102444800,
}
RAW_KEY = "A" * 48
BODY = {
    "token_id": 7,
    "model": "gpt-image-2",
    "prompt": "画一只鸟",
    "parameters": {},
    "request_id": "fixture-request-1",
    "contract_revision": "0" * 64,
    "intent": {
        "operation": "generate",
        "has_annotations": False,
        "reference_roles": [],
    },
}
HEADERS = {"X-Cockpit-Studio": "1"}


def owned(**changes):
    return {
        "id": 7,
        "user_id": 12,
        "group": "GPT",
        "status": 1,
        "expired_time": -1,
        "remain_quota": 100,
        "unlimited_quota": False,
        "name": "My GPT",
        "key": RAW_KEY,
        "model_limits_enabled": False,
        "model_limits": "",
        **changes,
    }


def api(path, credential, user_id, **kwargs):
    if path == "/api/token/7/key":
        return {"data": {"key": RAW_KEY}}
    if path == "/api/token/7":
        return {"data": owned()}
    if path == "/v1/models":
        return {
            "data": [
                {"id": "gpt-image-2"},
                {"id": "gpt-image-1-mini"},
                {"id": "text-model"},
            ]
        }
    raise AssertionError(path)


def png(alpha=255, mode="RGBA", size=(8, 8)):
    file = BytesIO()
    Image.new(
        mode, size, (30, 90, 140, alpha) if mode == "RGBA" else (30, 90, 140)
    ).save(file, "PNG")
    return file.getvalue()


def record(*, mask=False, size=(8, 8), alpha=255):
    return image_transport.inspect_image(png(alpha=alpha, size=size), mask=mask)


class ImageStudioTest(unittest.TestCase):
    def setUp(self):
        self.config = configuration()
        self.configuration = self.enterContext(
            patch.object(studio, "_configuration", side_effect=lambda: self.config)
        )
        self.routes = self.enterContext(
            patch.object(studio, "_routes", return_value=[route()])
        )
        self.upstream = self.enterContext(
            patch.object(manual, "upstream", side_effect=api)
        )
        self.claim = self.enterContext(patch.object(image_receipts, "claim"))
        self.send = self.enterContext(
            patch.object(image_transport, "request_image", return_value={"images": []})
        )

    def body(self, *, operation="generate", annotations=False, roles=None, **changes):
        contract = studio._contract(
            "gpt-image-2", self.routes.return_value, self.config
        )
        return {
            **deepcopy(BODY),
            "contract_revision": contract["revision"],
            "intent": {
                "operation": operation,
                "has_annotations": annotations,
                "reference_roles": roles or [],
            },
            **changes,
        }

    def test_own_exact_gpt_tokens_only_and_no_raw_key(self):
        with patch.object(
            manual,
            "list_tokens",
            return_value={
                "tokens": [{"id": 1, "group": "GPT"}, {"id": 2, "group": "auto"}],
                "token_manager_path": "/keys",
            },
        ):
            self.assertEqual(
                studio.list_tokens(OWNER, "fixture")["tokens"],
                [{"id": 1, "group": "GPT"}],
            )
        for changes in (
            {"user_id": 99},
            {"group": "auto"},
            {"group": "gpt"},
            {"group": ""},
            {"group": "default"},
            {"status": 2},
            {"expired_time": 1},
            {"remain_quota": 0},
        ):
            with (
                self.subTest(changes=changes),
                patch.object(
                    manual, "upstream", return_value={"data": owned(**changes)}
                ) as upstream,
            ):
                with self.assertRaises(manual.ManualError):
                    studio.run(OWNER, "fixture", self.body())
                self.assertEqual(upstream.call_count, 1)
        self.claim.assert_not_called()
        self.send.assert_not_called()

    def test_models_intersect_permissions_routes_and_never_expose_key(self):
        self.routes.return_value = [route(), route("gpt-image-1-mini")]
        data = studio.models(OWNER, "fixture", {"token_id": 7})
        self.assertEqual(
            [m["id"] for m in data["models"]], ["gpt-image-1-mini", "gpt-image-2"]
        )
        self.assertIsNone(data["models"][0]["edit"])
        self.assertEqual(data["models"][1]["edit"]["local_strategy"], "native-mask")
        self.assertNotIn(RAW_KEY, json.dumps(data))
        self.assertNotIn("_wire_fields", json.dumps(data))

        def limited(path, *args, **kwargs):
            if path == "/api/token/7":
                return {
                    "data": owned(
                        model_limits_enabled=True, model_limits="gpt-image-1-mini"
                    )
                }
            return api(path, *args, **kwargs)

        self.upstream.side_effect = limited
        self.assertEqual(
            [
                m["id"]
                for m in studio.models(OWNER, "fixture", {"token_id": 7})["models"]
            ],
            ["gpt-image-1-mini"],
        )
        with self.assertRaises(studio.StudioError) as caught:
            studio.run(OWNER, "fixture", self.body())
        self.assertEqual(caught.exception.status, 403)
        self.send.assert_not_called()
        self.routes.return_value = []
        self.assertEqual(
            studio.models(OWNER, "fixture", {"token_id": 7}), {"models": []}
        )

    def test_parse_normalizes_without_mutating_and_requires_revision_intent(self):
        body = self.body(prompt="  hello\r\nworld\r  ")
        original = deepcopy(body)
        normalized = studio.parse_request(body)
        self.assertEqual(normalized["prompt"], "hello\nworld")
        self.assertEqual(body, original)
        normalized["intent"]["reference_roles"].append("style")
        self.assertEqual(body, original)
        for revision in (None, "", "legacy"):
            invalid = self.body(contract_revision=revision)
            if revision is None:
                del invalid["contract_revision"]
            with self.assertRaises(studio.StudioError) as caught:
                studio.run(OWNER, "fixture", invalid)
            self.assertEqual(
                (caught.exception.status, caught.exception.code),
                (409, "IMAGE_CONTRACT_CHANGED"),
            )
        self.upstream.assert_not_called()
        self.claim.assert_not_called()
        self.send.assert_not_called()

    def test_invalid_intent_and_parameter_shapes_fail_before_payment(self):
        cases = [
            self.body(prompt=""),
            self.body(prompt="\ud800"),
            self.body(parameters=[]),
            self.body(user_id=12),
            self.body(channel_id=5),
            self.body(operation="local", prompt=""),
            self.body(operation="whole", prompt=""),
            self.body(operation="generate", annotations=True),
            self.body(operation="generate", roles=["style"]),
            self.body(
                intent={
                    "operation": "whole",
                    "has_annotations": 1,
                    "reference_roles": [],
                }
            ),
            self.body(
                intent={
                    "operation": "whole",
                    "has_annotations": True,
                    "reference_roles": ["mask"],
                }
            ),
            self.body(
                intent={
                    "operation": "whole",
                    "has_annotations": True,
                    "reference_roles": [],
                    "guide": True,
                }
            ),
        ]
        for body in cases:
            with self.subTest(body=body), self.assertRaises(studio.StudioError):
                studio.run(OWNER, "fixture", body)
        self.claim.assert_not_called()
        self.send.assert_not_called()

    def test_generation_and_edit_parameters_differ_and_unsupported_defaults_omitted(
        self,
    ):
        gen = self.config["profiles"]["fixture-v1"]["generation"]["parameters"]
        for field in ("size", "quality", "output_format", "output_compression"):
            gen["allowed_fields"].remove(field)
        gen.update(sizes=[], custom_size=False, qualities=[], formats=[])
        self.assertEqual(studio.parameters({}, gen), {})
        studio.run(OWNER, "fixture", self.body())
        self.assertEqual(
            set(self.send.call_args.kwargs["payload"]), {"model", "prompt", "n"}
        )
        with self.assertRaises(studio.StudioError):
            studio.run(OWNER, "fixture", self.body(parameters={"quality": "high"}))
        studio.run(
            OWNER,
            "fixture",
            self.body(operation="whole", parameters={"quality": "high"}),
            images=[record()],
        )
        self.assertEqual(self.send.call_args.kwargs["fields"]["quality"], "high")

    def test_compiled_prompt_budget_and_unicode_checked_before_receipt_without_truncation(
        self,
    ):
        body = self.body(operation="whole", prompt="字" * 32000)
        original = deepcopy(body)
        with self.assertRaises(studio.StudioError) as caught:
            studio.run(OWNER, "fixture", body, images=[record()])
        self.assertEqual(caught.exception.details["param"], "prompt")
        self.assertIn("原文未截断", str(caught.exception))
        self.assertEqual(body, original)
        for compiled in ("\ud800", "x" * 128001):
            with (
                patch.object(image_contracts, "compile_prompt", return_value=compiled),
                self.assertRaises(studio.StudioError),
            ):
                studio.run(
                    OWNER, "fixture", self.body(operation="whole"), images=[record()]
                )
        self.send.assert_not_called()
        self.claim.assert_not_called()
        # Generation has no instruction overhead; an in-budget raw prompt survives.
        studio.run(OWNER, "fixture", self.body(prompt="字" * 32000))
        self.assertEqual(self.send.call_args.kwargs["payload"]["prompt"], "字" * 32000)

    def test_endpoint_intent_cannot_switch_generate_and_edit(self):
        for body, images in (
            (self.body(operation="whole"), None),
            (self.body(operation="local"), None),
            (self.body(operation="generate"), [record()]),
        ):
            with self.subTest(body=body), self.assertRaises(studio.StudioError):
                studio.run(OWNER, "fixture", body, images=images)
        self.claim.assert_not_called()
        self.send.assert_not_called()

    def test_parameter_bounds_background_and_server_only_fields(self):
        allowed = self.config["profiles"]["fixture-v1"]["generation"]["parameters"]
        self.assertEqual(
            studio.parameters({"size": "2048x2048"}, allowed)["size"], "2048x2048"
        )
        self.assertNotIn(
            "background", studio.parameters({"background": "auto"}, allowed)
        )
        self.assertEqual(
            studio.parameters({"background": "transparent"}, allowed)["background"],
            "transparent",
        )
        for values in (
            {"size": "2049x2048"},
            {"size": []},
            {"size": "3840x512"},
            {"n": 2},
            {"background": "transparent", "output_format": "jpeg"},
            {"output_compression": 50},
            {"output_compression": True, "output_format": "jpeg"},
            {"input_fidelity": "high"},
            {"quality": {}},
            {"response_format": "b64_json"},
            {"image_field": "image"},
        ):
            with self.subTest(values=values), self.assertRaises(studio.StudioError):
                studio.parameters(values, allowed)

    def test_receipt_is_last_before_one_exact_key_call_and_no_input_mutation(self):
        body = self.body()
        original = deepcopy(body)
        order = []
        self.claim.side_effect = lambda *args: order.append("claim") or MagicMock()
        self.send.side_effect = lambda *args, **kwargs: (
            order.append("send") or {"images": []}
        )
        result = studio.run(OWNER, "fixture", body)
        self.assertEqual(order, ["claim", "send"])
        self.assertEqual(body, original)
        self.assertEqual(result["request_id"], body["request_id"])
        self.claim.assert_called_once_with(
            12,
            body["request_id"],
            image_receipts.digest_request(
                studio.parse_request(body), images=None, mask=None
            ),
        )
        self.send.assert_called_once()
        self.assertEqual(
            self.send.call_args.args, ("/v1/images/generations", "sk-" + RAW_KEY)
        )
        self.assertEqual(self.send.call_args.kwargs["payload"]["n"], 1)
        self.assertEqual(
            self.send.call_args.kwargs["response_contract"]["format"], "b64_json"
        )
        self.assertEqual(self.routes.call_count, 2)

    def test_receipt_success_failed_unknown_and_errors_never_repeat_dispatch(self):
        studio.run(OWNER, "fixture", self.body())
        self.claim.return_value.finish.assert_called_once_with("succeeded")
        for uncertain in (False, True):
            self.send.reset_mock()
            self.claim.return_value.finish.reset_mock()
            error = image_transport.ImageError(
                "fixture error",
                status=504 if uncertain else 400,
                uncertain=uncertain,
                details={
                    "category": "network" if uncertain else "parameter",
                    "http_status": 504 if uncertain else 400,
                    "provider_code": "bad_argument",
                    "param": "size",
                    "upstream_request_id": "req_1",
                },
            )
            self.send.side_effect = error
            with self.assertRaises(studio.StudioError) as caught:
                studio.run(OWNER, "fixture", self.body())
            self.assertEqual(caught.exception.uncertain, uncertain)
            self.assertEqual(caught.exception.details["param"], "size")
            self.claim.return_value.finish.assert_called_once_with(
                "unknown" if uncertain else "failed"
            )
            self.send.assert_called_once()
        self.send.side_effect = RuntimeError("DO-NOT-EXPOSE")
        with self.assertRaises(studio.StudioError) as caught:
            studio.run(OWNER, "fixture", self.body())
        self.assertTrue(caught.exception.uncertain)
        self.assertNotIn("DO-NOT-EXPOSE", str(caught.exception))
        self.claim.return_value.finish.assert_called_with("unknown")

    def test_receipt_duplicate_or_store_unavailable_prevents_dispatch(self):
        for code, status in (
            ("IMAGE_REQUEST_DUPLICATE", 409),
            ("IMAGE_RECEIPTS_UNAVAILABLE", 503),
        ):
            error = image_receipts.ReceiptError("fixture", code=code, status=status)
            self.claim.side_effect = error
            with self.assertRaises(image_receipts.ReceiptError) as caught:
                studio.run(OWNER, "fixture", self.body())
            self.assertIs(caught.exception, error)
        self.send.assert_not_called()

    def test_revision_checked_before_validation_and_again_after_discovery(self):
        body = self.body()
        self.routes.return_value = [route(settings_revision="changed")]
        with self.assertRaises(studio.StudioError) as caught:
            studio.run(OWNER, "fixture", body)
        self.assertEqual(caught.exception.status, 409)
        self.claim.assert_not_called()
        self.routes.side_effect = [[route()], [route(no_param_override=False)]]
        with self.assertRaises(studio.StudioError) as caught:
            studio.run(OWNER, "fixture", body)
        self.assertEqual(caught.exception.code, "IMAGE_CONTRACT_CHANGED")
        self.claim.assert_not_called()
        self.send.assert_not_called()
        # A configuration-only revision bump during file validation also blocks.
        self.routes.side_effect = None
        self.routes.return_value = [route()]
        changed = deepcopy(self.config)
        changed["profiles"]["fixture-v1"]["revision"] = "new-revision"
        self.configuration.side_effect = [self.config, changed]
        with self.assertRaises(studio.StudioError) as caught:
            studio.run(OWNER, "fixture", body)
        self.assertEqual(caught.exception.code, "IMAGE_CONTRACT_CHANGED")
        self.send.assert_not_called()

    def test_annotations_only_whole_edit_native_local_mask_and_roles_order(self):
        main, guide, ref = record(), record(), record(size=(10, 10))
        body = self.body(
            operation="whole", annotations=True, roles=["subject"], prompt=""
        )
        studio.run(OWNER, "fixture", body, images=[main, guide, ref])
        fields = self.send.call_args.kwargs["fields"]
        self.assertIn("第3张图为人物或物体参考", fields["prompt"])
        self.assertIsNone(self.send.call_args.kwargs["mask"])
        alpha = record(mask=True, alpha=0)
        studio.run(
            OWNER,
            "fixture",
            self.body(operation="local", annotations=True, prompt=""),
            images=[main, guide],
            mask=alpha,
        )
        self.assertIs(self.send.call_args.kwargs["mask"], alpha)
        self.assertEqual(self.send.call_args.kwargs["images"], [main, guide])
        self.assertIn("alpha", self.send.call_args.kwargs["fields"]["prompt"])

    def test_reference_local_derives_guide_without_annotations_and_never_mask(self):
        self.config = configuration("reference-only", array_only=True)
        self.routes.return_value = [route(pass_through_body_enabled=True)]
        body = self.body(operation="local", roles=["style"])
        images = [record(), record(), record(size=(10, 10))]
        studio.run(OWNER, "fixture", body, images=images)
        kwargs = self.send.call_args.kwargs
        self.assertEqual(kwargs["image_field"], "image[]")
        self.assertIsNone(kwargs["mask"])
        self.assertIn("青色", kwargs["fields"]["prompt"])
        self.assertIn("第3张图为风格参考", kwargs["fields"]["prompt"])
        before = self.send.call_count
        with self.assertRaises(studio.StudioError):
            studio.run(
                OWNER, "fixture", body, images=images, mask=record(mask=True, alpha=0)
            )
        self.assertEqual(self.send.call_count, before)

    def test_rebuild_and_pass_through_single_and_multiple_image_field(self):
        # Verify BOTH the transport field and the rc40-rebuilt provider field.
        for passthrough in (False, True):
            self.config = configuration(array_only=passthrough)
            self.routes.return_value = [route(pass_through_body_enabled=passthrough)]
            for count in (1, 2):
                studio.run(
                    OWNER,
                    "fixture",
                    self.body(operation="whole", roles=["reference"] * (count - 1)),
                    images=[record()] * count,
                )
                kwargs = self.send.call_args.kwargs
                actual_wire = b"".join(
                    image_transport._request_body(
                        "/v1/images/edits",
                        None,
                        kwargs["fields"],
                        kwargs["images"],
                        None,
                        image_field=kwargs["image_field"],
                    )[0]
                )
                field = kwargs["image_field"]
                self.assertEqual(
                    actual_wire.count(('name="' + field + '"; filename=').encode()),
                    count,
                )
                provider_field = (
                    field if passthrough else "image" if count == 1 else "image[]"
                )
                self.assertEqual(
                    provider_field, "image[]" if passthrough or count > 1 else "image"
                )
                self.assertNotIn(b'name="mask"', actual_wire)

    def test_fixed_tier_contract_blocks_arbitrary_size_before_paid_dispatch(self):
        profile = self.config["profiles"]["fixture-v1"]
        profile["edit"]["local_strategy"] = "reference-only"
        for operation in ("generation", "edit"):
            profile[operation]["parameters"].update(
                sizes=["auto", "1024x1024", "1536x1024", "1024x1536"],
                custom_size=False,
            )
        for operation in ("generate", "whole", "local"):
            for size in ("848x1296", "1296x848", "2048x2048"):
                with self.subTest(operation=operation, size=size):
                    body = self.body(
                        operation=operation,
                        annotations=operation != "generate",
                        prompt="" if operation != "generate" else "画一只鸟",
                        parameters={"size": size},
                    )
                    with self.assertRaises(studio.StudioError):
                        studio.run(
                            OWNER,
                            "fixture",
                            body,
                            images=None
                            if operation == "generate"
                            else [record(), record()],
                        )
        self.claim.assert_not_called()
        self.send.assert_not_called()
        studio.run(
            OWNER,
            "fixture",
            self.body(
                operation="local",
                annotations=True,
                prompt="",
                parameters={"size": "1024x1536"},
            ),
            images=[record(size=(1024, 1536)), record(size=(1024, 1536))],
        )
        self.assertEqual(self.send.call_args.kwargs["fields"]["size"], "1024x1536")
        self.assertIsNone(self.send.call_args.kwargs["mask"])

    def test_invalid_file_counts_guide_dimensions_and_mask_block_before_receipt(self):
        cases = [
            (self.body(), [record()], None),
            (self.body(operation="whole"), [], None),
            (self.body(operation="whole", annotations=True), [record()], None),
            (
                self.body(operation="whole", annotations=True),
                [record(), record(size=(9, 8))],
                None,
            ),
            (self.body(operation="whole"), [record()], record(mask=True, alpha=0)),
            (self.body(operation="local"), [record()], None),
            (self.body(operation="local"), [record()], record(mask=True)),
            (
                self.body(operation="local"),
                [record()],
                record(mask=True, alpha=0, size=(9, 8)),
            ),
            (self.body(operation="whole", roles=["reference"]), [record()], None),
            (self.body(operation="whole"), [{"data": b"not-image"}], None),
        ]
        for body, images, mask in cases:
            with (
                self.subTest(intent=body["intent"]),
                self.assertRaises(studio.StudioError),
            ):
                studio.run(OWNER, "fixture", body, images=images, mask=mask)
        self.send.assert_not_called()
        self.claim.assert_not_called()

    def test_profile_input_limits_apply_before_payment(self):
        self.config["profiles"]["fixture-v1"]["edit"]["inputs"]["max_pixels"] = 10
        with self.assertRaises(studio.StudioError):
            studio.run(
                OWNER, "fixture", self.body(operation="whole"), images=[record()]
            )
        self.claim.assert_not_called()
        self.send.assert_not_called()

    def test_response_policy_server_only_and_url_contract_propagates(self):
        gen = self.config["profiles"]["fixture-v1"]["generation"]
        gen["response"] = {
            "format": "url",
            "allowed_hosts": ["cdn.example.com"],
            "allowed_networks": ["8.8.8.8/32"],
        }
        gen["request_response_format"] = "url"
        studio.run(OWNER, "fixture", self.body())
        self.assertEqual(
            self.send.call_args.kwargs["payload"]["response_format"], "url"
        )
        self.assertEqual(
            self.send.call_args.kwargs["response_contract"], gen["response"]
        )
        before = self.send.call_count
        with self.assertRaises(studio.StudioError):
            studio.run(
                OWNER, "fixture", self.body(parameters={"response_format": "url"})
            )
        self.assertEqual(self.send.call_count, before)

    def test_safe_details_reject_raw_messages_keys_and_unknown_fields(self):
        err = studio.StudioError(
            "fixture",
            details={
                "category": "parameter",
                "http_status": 400,
                "provider_code": "invalid_size",
                "param": "image[0]",
                "upstream_request_id": "sk-secret",
                "message": "DO-NOT-EXPOSE",
                "key": RAW_KEY,
            },
        )
        self.assertEqual(
            err.details,
            {
                "category": "parameter",
                "http_status": 400,
                "provider_code": "invalid_size",
                "param": "image[0]",
            },
        )

    def test_headers_origin_and_capacity_precede_multipart_parse(self):
        client = app.test_client()
        with (
            patch("new_api_cockpit.app.request_identity", return_value=OWNER),
            patch.object(
                studio, "capacity", side_effect=studio.StudioError("busy", 429)
            ) as capacity,
            patch("new_api_cockpit.app.studio_edit_body") as parse,
        ):
            self.assertEqual(
                client.post(
                    "/cockpit/api/image-studio/edits",
                    data={"junk": "ignored"},
                    content_type="multipart/form-data",
                    headers=HEADERS,
                ).status_code,
                429,
            )
            parse.assert_not_called()
            for headers in (
                {},
                {**HEADERS, "Origin": "https://elsewhere.invalid"},
                {**HEADERS, "X-Cockpit-Session": "old"},
            ):
                self.assertIn(
                    client.post(
                        "/cockpit/api/image-studio/edits",
                        data={},
                        content_type="multipart/form-data",
                        headers=headers,
                    ).status_code,
                    (403, 409),
                )
            self.assertEqual(capacity.call_count, 1)
            too_large = client.post(
                "/cockpit/api/image-studio/models",
                data=json.dumps({"token_id": 7, "padding": "x" * 1100}),
                content_type="application/json",
                headers=HEADERS,
            )
            self.assertEqual(too_large.status_code, 413)
            self.assertEqual(too_large.json["code"], "IMAGE_TOO_LARGE")
            self.assertFalse(too_large.json["uncertain"])

    def test_image_page_auth_scoped_csp_and_admin_routes_remain_private(self):
        self.assertEqual(safe_next("/cockpit/image-studio"), "/cockpit/image-studio")
        client = app.test_client()
        self.assertEqual(
            client.get("/cockpit/api/image-studio/tokens").status_code, 401
        )
        self.assertIn(
            "next=%2Fcockpit%2Fimage-studio",
            client.get("/cockpit/image-studio").location,
        )
        with (
            patch("new_api_cockpit.app.request_identity", return_value=OWNER),
            patch("new_api_cockpit.app.load_site_name", return_value="Fixture"),
        ):
            page = client.get("/cockpit/image-studio")
            self.assertEqual(page.status_code, 200)
            self.assertIn("blob:", page.headers["Content-Security-Policy"])
            self.assertNotIn("unsafe-inline", page.headers["Content-Security-Policy"])
            self.assertIn('href="/cockpit/image-studio" aria-current="page"', page.text)
            self.assertNotIn('href="/cockpit/users/"', page.text)
            self.assertEqual(client.get("/cockpit/users/").status_code, 403)
            self.assertNotIn(
                "blob:",
                client.get("/cockpit/manual").headers["Content-Security-Policy"],
            )

    def test_edit_contract_normalizes_files_preserves_order_and_checks_mask(self):
        client = app.test_client()
        form = {
            **self.body(operation="local", annotations=True),
            "token_id": "7",
            "parameters": "{}",
        }
        form["intent"] = json.dumps(form["intent"])
        with (
            patch("new_api_cockpit.app.request_identity", return_value=OWNER),
            patch.object(studio, "capacity", return_value=nullcontext()),
            patch.object(studio, "run", return_value={"images": []}) as run,
        ):
            data = {
                **form,
                "image[]": [
                    (BytesIO(png()), "main.png"),
                    (BytesIO(png()), "guide.png"),
                ],
                "mask": (BytesIO(png(alpha=0)), "mask.png"),
            }
            reply = client.post(
                "/cockpit/api/image-studio/edits", data=data, headers=HEADERS
            )
            self.assertEqual(reply.status_code, 200, reply.json)
            self.assertEqual(len(run.call_args.kwargs["images"]), 2)
            run.reset_mock()
            for mask in (png(), png(alpha=0, size=(9, 8))):
                reply = client.post(
                    "/cockpit/api/image-studio/edits",
                    data={
                        **form,
                        "image[]": (BytesIO(png()), "main.png"),
                        "mask": (BytesIO(mask), "mask.png"),
                    },
                    headers=HEADERS,
                )
                self.assertEqual(reply.status_code, 400)
                run.assert_not_called()

    def test_capacity_nonblocking_locks_and_finally_releases_on_exception(self):
        conn = MagicMock()
        conn.execute.return_value.fetchone.side_effect = [("epoch",), (True,), (True,)]
        with (
            patch.dict(os.environ, {"COCKPIT_PROCESS_ROLE": "image"}),
            patch.object(studio, "_monitor_connection", return_value=conn),
            patch.object(studio, "_capacity_failed", False),
            patch.object(studio, "_database_epoch", None),
        ):
            with self.assertRaisesRegex(ValueError, "fixture failure"):
                with studio.capacity(OWNER):
                    raise ValueError("fixture failure")
        calls = [c.args[0] for c in conn.execute.call_args_list]
        self.assertEqual(sum("try_advisory_lock" in sql for sql in calls), 2)
        self.assertEqual(sum("pg_advisory_unlock" in sql for sql in calls), 2)
        conn.close.assert_called_once()
        with (
            patch.dict(os.environ, {"COCKPIT_PROCESS_ROLE": "web"}),
            self.assertRaises(studio.StudioError),
        ):
            with studio.capacity(OWNER):
                pass

    def test_image_role_does_not_serve_other_pages_or_run_migrations_and_timers(self):
        from new_api_cockpit import runtime, gunicorn_conf, timers, balance

        with (
            patch.dict(os.environ, {"COCKPIT_PROCESS_ROLE": "image"}),
            patch.object(balance, "configured", return_value=True),
            patch.object(balance, "initialize") as initialize,
            patch.object(timers, "start") as start,
            patch.object(studio, "initialize_capacity") as capacity,
            patch.object(runtime.sys, "argv", ["runtime"]),
        ):
            runtime.main()
            gunicorn_conf.post_worker_init(MagicMock())
            initialize.assert_not_called()
            start.assert_not_called()
            capacity.assert_called_once()
            self.assertEqual(app.test_client().get("/cockpit/manual").status_code, 404)


class ImageStudioConfigurationTest(unittest.TestCase):
    def test_file_loader_is_strict_bounded_and_never_falls_back(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "protocols.json"
            with patch.dict(os.environ, {"IMAGE_STUDIO_CAPABILITIES_FILE": str(path)}):
                with self.assertRaises(studio.StudioError) as caught:
                    studio._configuration()
                self.assertEqual(caught.exception.status, 503)
                path.write_text(json.dumps(configuration()))
                self.assertEqual(studio._configuration(), configuration())
                for raw in (
                    '{"schema":2,"schema":2}',
                    '{"schema":1}',
                    "bad-secret",
                    " " * 65537,
                ):
                    path.write_text(raw)
                    with (
                        self.subTest(raw=raw[:50]),
                        self.assertRaises(studio.StudioError) as caught,
                    ):
                        studio._configuration()
                    self.assertEqual(caught.exception.status, 503)
                    self.assertNotIn("bad-secret", str(caught.exception))
        with patch.dict(os.environ, {"IMAGE_STUDIO_CAPABILITIES_FILE": ""}):
            self.assertEqual(studio._configuration(), image_contracts.EMPTY_CONFIG)

    def test_route_query_selects_known_columns_only_and_projects_no_credentials(self):
        conn = MagicMock()
        conn.execute.return_value.fetchall.return_value = [route()]
        context = MagicMock()
        context.__enter__.return_value = conn
        policy = {
            "global_pass_through_request_enabled": False,
            "no_affinity_override": True,
            "affinity_revision": "fixture",
        }
        for available in (
            set(),
            {
                "model_mapping",
                "base_url",
                "setting",
                "settings",
                "param_override",
                "header_override",
            },
        ):
            with (
                self.subTest(available=available),
                patch.object(studio.quota, "connect", return_value=context),
                patch.object(studio, "_source_columns", side_effect=[available, set()]),
                patch.object(studio, "_gateway_policy", return_value=policy),
            ):
                rows = studio._routes()
                self.assertTrue(rows[0]["no_affinity_override"])
                sql = conn.execute.call_args.args[0].as_string()
                self.assertNotIn("to_jsonb(c)", sql)
                self.assertNotIn("c.*", sql)
                self.assertNotIn('"c"."key"', sql)
                self.assertNotIn("c.key", sql)
                self.assertIn('a."group"=%s', sql)
                self.assertNotIn("priority=", sql)
                self.assertNotIn("weight>", sql)
                for field in available:
                    self.assertIn('"c"."' + field + '"', sql)
                for field in {
                    "setting",
                    "settings",
                    "param_override",
                    "header_override",
                } - available:
                    self.assertNotIn('"c"."' + field + '"', sql)
                self.assertEqual(conn.execute.call_args.args[1], ("GPT", "gpt-image-%"))

    def test_metadata_distinguishes_missing_column_from_missing_select_grant(self):
        conn = MagicMock()
        conn.execute.return_value.fetchall.side_effect = [
            [{"column_name": "setting"}],
            [{"column_name": "setting"}],
        ]
        self.assertEqual(
            studio._source_columns(conn, "channels", {"setting", "param_override"}),
            {"setting"},
        )
        query = conn.execute.call_args.args[0]
        self.assertIn("information_schema.columns", query)
        self.assertIn("has_column_privilege", query)
        self.assertIn("format('%%I.%%I'", query)
        conn.execute.return_value.fetchall.side_effect = [
            [{"column_name": "setting"}],
            [],
        ]
        with self.assertRaises(studio.StudioError) as caught:
            studio._source_columns(conn, "channels", {"setting"})
        self.assertEqual(caught.exception.status, 503)
        self.assertIn("SELECT", str(caught.exception))

    def test_route_read_failure_has_short_safe_diagnostic(self):
        for error in (
            psycopg.errors.InsufficientPrivilege("secret SQL"),
            psycopg.OperationalError("secret DSN"),
        ):
            with (
                patch.object(studio.quota, "connect", side_effect=error),
                self.assertRaises(studio.StudioError) as caught,
            ):
                studio._routes()
            self.assertEqual(caught.exception.status, 503)
            self.assertNotIn("secret", str(caught.exception))

    def test_gateway_flags_and_affinity_templates_fail_closed_without_reading_values(
        self,
    ):
        conn = MagicMock()

        def check(rules=None, enabled=None, passthrough=None):
            conn.execute.return_value.fetchone.return_value = {
                "pass_through": passthrough,
                "affinity_enabled": enabled,
                "affinity_rules": rules,
            }
            return studio._gateway_policy(conn)

        self.assertTrue(check()["no_affinity_override"])
        self.assertFalse(check()["global_pass_through_request_enabled"])
        self.assertTrue(
            check(passthrough="true")["global_pass_through_request_enabled"]
        )
        self.assertTrue(
            check([{"paths": ["/v1/responses", "^/v1/messages$"], "override": True}])[
                "no_affinity_override"
            ]
        )
        for path in (
            "/v1/images/edits",
            "^/v1/images/generations$",
            "/v1/image",
            "/v1/.*",
            ".*",
            "(a+)+",
        ):
            self.assertFalse(
                check([{"paths": [path], "override": True}])["no_affinity_override"]
            )
            self.assertTrue(
                check([{"paths": [path], "override": True}], enabled="false")[
                    "no_affinity_override"
                ]
            )
        self.assertTrue(
            check([{"paths": [".*"], "override": False}])["no_affinity_override"]
        )
        for kwargs in (
            {"passthrough": "yes"},
            {"enabled": 1},
            {"rules": [{"paths": [], "override": "yes"}]},
        ):
            with self.subTest(kwargs=kwargs), self.assertRaises(studio.StudioError):
                check(**kwargs)
        sql = conn.execute.call_args.args[0]
        self.assertNotIn("SELECT *", sql)
        self.assertIn("jsonb_build_object", sql)
        self.assertNotIn("header_override", sql)

    def test_example_configuration_loads_without_widening_unconfigured_routes(self):
        path = (
            Path(__file__).resolve().parents[1]
            / "image-studio-capabilities.example.json"
        )
        config = image_contracts.validate_configuration(
            image_contracts.loads(path.read_bytes())
        )
        self.assertEqual(set(config), {"schema", "profiles", "channels", "models"})
        strategies = {p["edit"]["local_strategy"] for p in config["profiles"].values()}
        self.assertEqual(strategies, {"native-mask", "reference-only"})
        model = image_contracts.model_contract(
            "gpt-image-2", [route(channel_id=999)], config
        )
        self.assertIsNone(model["generation"])
        self.assertIsNone(model["edit"])


if __name__ == "__main__":
    unittest.main()
