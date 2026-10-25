"""Image Studio documentation metadata only: no HTTP, database or paid calls."""

import json
from pathlib import Path
import re
import unittest

from new_api_cockpit.openapi import document


PREFIX = "/cockpit/api/image-studio/"
ROOT = Path(__file__).resolve().parents[1]


class ImageStudioSchemaTest(unittest.TestCase):
    def setUp(self):
        self.spec = document()
        self.schemas = self.spec["components"]["schemas"]

    def operation(self, suffix):
        return self.spec["paths"][PREFIX + suffix][
            "get" if suffix == "tokens" else "post"
        ]

    def test_exact_routes_and_no_legacy_paths(self):
        self.assertEqual(
            {p for p in self.spec["paths"] if p.startswith(PREFIX)},
            {PREFIX + s for s in ("tokens", "models", "generations", "edits")},
        )
        self.assertNotIn("/cockpit/api/studio/", json.dumps(self.spec))
        for filename in (
            "README.md",
            "docs/api.md",
            "docs/deployment.md",
            "docs/image-studio.md",
        ):
            content = (ROOT / filename).read_text()
            self.assertNotIn("/cockpit/studio", content, filename)
            self.assertNotIn("/cockpit/api/studio", content, filename)

    def test_all_enabled_users_have_explicit_security_not_admin_only(self):
        for suffix in ("tokens", "models", "generations", "edits"):
            with self.subTest(suffix=suffix):
                operation = self.operation(suffix)
                self.assertEqual(operation["x-cockpit-auth"], "enabled-user")
                self.assertFalse(operation["x-cockpit-pat-only"])
                self.assertEqual(
                    operation["security"],
                    [{"browserSession": []}, {"browserJWT": []}, {"adminPAT": []}],
                )
                self.assertIn("管理员无代用例外", operation["description"])
                for alternative in operation["security"]:
                    for name in alternative:
                        self.assertIn(name, self.spec["components"]["securitySchemes"])

    def test_discovery_is_read_and_only_takes_token_id(self):
        for suffix in ("tokens", "models"):
            operation = self.operation(suffix)
            self.assertEqual(operation["x-cockpit-effect"], "read")
            self.assertFalse(operation["x-cockpit-paid"])
        body = self.schemas["ImageStudioModelRequest"]
        self.assertEqual(set(body["properties"]), {"token_id"})
        self.assertEqual(body["required"], ["token_id"])
        self.assertFalse(body["additionalProperties"])
        header = next(
            p for p in self.operation("models")["parameters"] if p["in"] == "header"
        )
        self.assertTrue(header["required"])
        self.assertEqual(body["properties"]["token_id"]["maximum"], 2147483647)
        self.assertNotIn(
            "enum", self.schemas["ImageStudioToken"]["properties"]["group"]
        )
        self.assertNotIn("key", self.schemas["ImageStudioToken"]["properties"])

    def test_paid_mutations_require_confirmation_and_cannot_try_it(self):
        # Assert safety policy without constructing or executing a paid request.
        for suffix in ("generations", "edits"):
            operation = self.operation(suffix)
            self.assertEqual(operation["x-cockpit-effect"], "write")
            self.assertTrue(operation["x-cockpit-paid"])
            self.assertFalse(operation["x-cockpit-testable"])
            self.assertEqual(
                operation["x-cockpit-confirm-headers"], {"X-Cockpit-Studio": "1"}
            )
            header = next(p for p in operation["parameters"] if p["in"] == "header")
            self.assertTrue(header["required"])
            self.assertEqual(header["schema"]["const"], "1")
            self.assertIn("认证刷新不重放 POST", operation["description"])
            self.assertIn("不保证幂等", operation["description"])
            for status in ("413", "429", "502", "503", "504"):
                self.assertEqual(
                    operation["responses"][status]["content"]["application/json"][
                        "schema"
                    ],
                    {"$ref": "#/components/schemas/ImageStudioError"},
                )

    def test_generation_has_revision_intent_and_optional_parameters(self):
        body = self.schemas["ImageStudioGenerationRequest"]
        fields = {
            "token_id",
            "model",
            "prompt",
            "parameters",
            "request_id",
            "contract_revision",
            "intent",
        }
        self.assertEqual(set(body["required"]), fields)
        self.assertEqual(set(body["properties"]), fields)
        self.assertFalse(body["additionalProperties"])
        parameters = self.schemas["ImageStudioParameters"]
        self.assertFalse(parameters.get("required"))
        self.assertFalse(parameters["additionalProperties"])
        self.assertNotIn("n", parameters["properties"])
        self.assertEqual(
            {
                name: value["default"]
                for name, value in parameters["properties"].items()
                if "default" in value
            },
            {
                "size": "auto",
                "quality": "auto",
                "output_format": "png",
                "background": "auto",
            },
        )
        self.assertIn(
            {"not": {"required": ["input_fidelity"]}},
            self.schemas["ImageStudioGenerationParameters"]["allOf"],
        )
        request_id = self.schemas["ImageStudioRequestId"]["pattern"]
        self.assertRegex("studio-request-1", request_id)
        self.assertIsNone(re.fullmatch(request_id, "short"))
        self.assertIsNone(re.fullmatch(request_id, "invalid id"))

    def test_edit_is_multipart_with_ordered_image_files_and_optional_mask(self):
        content = self.operation("edits")["requestBody"]["content"]
        self.assertEqual(set(content), {"multipart/form-data"})
        body = self.schemas["ImageStudioEditRequest"]
        self.assertEqual(
            set(body["required"]),
            {
                "token_id",
                "model",
                "prompt",
                "parameters",
                "request_id",
                "contract_revision",
                "intent",
                "image[]",
            },
        )
        self.assertFalse(body["additionalProperties"])
        properties = body["properties"]
        images = properties["image[]"]
        self.assertEqual(images["type"], "array")
        self.assertEqual(images["items"]["format"], "binary")
        self.assertEqual((images["minItems"], images["maxItems"]), (1, 4))
        self.assertEqual(properties["mask"]["format"], "binary")
        self.assertNotIn("mask", body["required"])
        parameters = properties["parameters"]
        self.assertEqual(parameters["type"], "string")
        self.assertEqual(parameters["contentMediaType"], "application/json")
        self.assertEqual(
            parameters["contentSchema"],
            {"$ref": "#/components/schemas/ImageStudioParameters"},
        )
        encoding = content["multipart/form-data"]["encoding"]
        self.assertTrue(encoding["image[]"]["explode"])
        self.assertEqual(encoding["mask"]["contentType"], "image/png")

    def test_contract_is_operation_specific_and_versions_paid_requests(self):
        model = self.schemas["ImageStudioModel"]
        self.assertEqual(
            set(model["required"]), {"id", "revision", "generation", "edit"}
        )
        self.assertNotIn("capabilities", model["properties"])
        edit = model["properties"]["edit"]["anyOf"][0]
        self.assertEqual(
            set(edit["properties"]["local_strategy"]["enum"]),
            {"native-mask", "reference-only", "unavailable"},
        )
        self.assertRegex(
            "a" * 64, self.schemas["ImageStudioContractRevision"]["pattern"]
        )
        self.assertEqual(
            self.schemas["ImageStudioEditRequest"]["properties"]["prompt"]["minLength"],
            0,
        )
        self.assertEqual(
            self.schemas["ImageStudioIntent"]["properties"]["reference_roles"]["items"][
                "enum"
            ],
            ["reference", "subject", "style", "composition"],
        )
        self.assertIn("回执有效期", self.schemas["ImageStudioRequestId"]["description"])

    def test_normalized_result_and_errors(self):
        result = self.schemas["ImageStudioResult"]
        self.assertEqual(
            set(result["required"]),
            {"images", "model", "request_id", "duration_seconds"},
        )
        self.assertEqual(result["properties"]["images"]["maxItems"], 1)
        self.assertNotIn("usage", result["required"])
        self.assertNotIn("upstream_request_id", result["required"])
        image = self.schemas["ImageStudioImage"]
        self.assertEqual(
            set(image["required"]), {"b64_json", "mime", "width", "height"}
        )
        self.assertNotIn("url", image["properties"])
        self.assertEqual(
            set(self.schemas["ImageStudioError"]["required"]), {"error", "code"}
        )

    def test_public_input_limits_match_backend_contract(self):
        from new_api_cockpit.features.image_studio.contracts import INPUT_LIMITS

        edit = self.schemas["ImageStudioModel"]["properties"]["edit"]["anyOf"][0]
        self.assertIn("inputs", edit["required"])
        limits = self.schemas["ImageStudioInputLimits"]
        self.assertEqual(set(limits["required"]), set(INPUT_LIMITS))
        for field, value in INPUT_LIMITS.items():
            schema = limits["properties"][field]
            if field == "mime_types":
                self.assertEqual(set(schema["items"]["enum"]), set(value))
            else:
                self.assertEqual(schema["maximum"], value, field)


if __name__ == "__main__":
    unittest.main()
