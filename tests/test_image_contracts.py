"""Pure protocol fixtures. No credentials, database, Redis or provider access."""

from copy import deepcopy
import json
import unittest

from new_api_cockpit import image_contracts as contracts


def configuration(strategy="native-mask", *, array_only=False):
    """Executable schema-2 example; values are fixtures, NOT verified providers."""
    params = {
        "allowed_fields": [
            "size",
            "quality",
            "output_format",
            "background",
            "output_compression",
        ],
        "sizes": ["auto", "1024x1024"],
        "custom_size": True,
        "qualities": ["auto", "low", "medium", "high"],
        "formats": ["png", "jpeg", "webp"],
        "backgrounds": ["auto", "opaque", "transparent"],
        "input_fidelity": [],
        "max_images": 0,
    }
    response = {"format": "b64_json", "allowed_hosts": [], "allowed_networks": []}
    config = {
        "schema": 2,
        "profiles": {
            "fixture-v1": {
                "revision": "2026-10-12.1",
                "evidence": "Offline protocol fixture; replace with administrator evidence",
                "generation": {"parameters": params, "response": response},
                "edit": {
                    "parameters": {**deepcopy(params), "max_images": 4},
                    "response": deepcopy(response),
                    "local_strategy": strategy,
                    "image_fields": {
                        "single": "image[]" if array_only else "image",
                        "multiple": "image[]",
                    },
                },
            }
        },
        "channels": {
            "5": {
                "type": 1,
                "gateway": "new-api-rc40-openai",
                "mode": "pass-through" if array_only else "rebuild",
                "global_pass_through": False,
                "channel_pass_through": array_only,
            }
        },
        "models": {
            "gpt-image-2": {
                "5": {"profile": "fixture-v1", "mapped_model": "gpt-image-2"}
            }
        },
    }
    return contracts.validate_configuration(config)


def route(model="gpt-image-2", **changes):
    return {
        "model": model,
        "channel_id": 5,
        "type": 1,
        "model_mapping": "{}",
        "pass_through_body_enabled": False,
        "global_pass_through_request_enabled": False,
        "no_param_override": True,
        "no_header_override": True,
        "no_affinity_override": True,
        "affinity_revision": "fixture-affinity",
        "base_url_revision": "fixture-host-v1",
        "settings_revision": "fixture-settings-v1",
        "priority": 1,
        "weight": 1,
        **changes,
    }


def add_channel(config, *, channel_id="6", profile="fixture-v1"):
    config["channels"][channel_id] = deepcopy(config["channels"]["5"])
    config["models"]["gpt-image-2"][channel_id] = {
        "profile": profile,
        "mapped_model": "gpt-image-2",
    }


class ImageContractsTest(unittest.TestCase):
    def test_missing_configuration_never_infers_type_one_or_model_family(self):
        config = contracts.validate_configuration(contracts.EMPTY_CONFIG)
        model = contracts.model_contract("gpt-image-2", [route()], config)
        self.assertIsNone(model["generation"])
        self.assertIsNone(model["edit"])
        self.assertIn("未配置", model["unavailable_reason"])
        self.assertEqual(len(model["revision"]), 64)

    def test_profiles_are_explicit_separate_and_public_output_is_small(self):
        config = configuration()
        gen = config["profiles"]["fixture-v1"]["generation"]["parameters"]
        gen["qualities"] = ["low"]
        model = contracts.model_contract("gpt-image-2", [route()], config)
        public = contracts.public_contract(model)
        self.assertEqual(public["generation"]["parameters"]["qualities"], ["low"])
        self.assertIn("high", public["edit"]["parameters"]["qualities"])
        self.assertEqual(public["edit"]["max_images"], 4)
        self.assertEqual(public["edit"]["local_strategy"], "native-mask")
        self.assertEqual(public["edit"]["inputs"], contracts.INPUT_LIMITS)
        public["edit"]["inputs"]["mime_types"].clear()
        self.assertTrue(model["edit"]["inputs"]["mime_types"])
        self.assertEqual(set(public), {"id", "revision", "generation", "edit"})
        self.assertNotIn("fixture", json.dumps(public))
        self.assertNotIn("response", public["generation"])

    def test_unknown_disabled_and_unbound_channels_remove_common_capability(self):
        config = configuration()
        for other in (route(channel_id=6), route(channel_id=5, type=14)):
            with self.subTest(other=other):
                model = contracts.model_contract(
                    "gpt-image-2", [route(), other], config
                )
                self.assertIsNone(model["edit"])
                self.assertIsNone(model["generation"])
        # A profile bound to one name never widens another name's permission.
        model = contracts.model_contract(
            "gpt-image-2-alias", [route("gpt-image-2-alias")], config
        )
        self.assertIsNone(model["generation"])

    def test_all_priorities_intersect_parameters_input_budget_and_strategy(self):
        config = configuration()
        config["profiles"]["other"] = deepcopy(config["profiles"]["fixture-v1"])
        op = config["profiles"]["other"]["edit"]
        op["local_strategy"] = "reference-only"
        op["parameters"].update(
            qualities=["low"], sizes=["1024x1024"], custom_size=False, max_images=2
        )
        op["inputs"].update(max_bytes=1024, max_pixels=1024, mime_types=["image/png"])
        add_channel(config, profile="other")
        model = contracts.model_contract(
            "gpt-image-2", [route(), route(channel_id=6, priority=0, weight=0)], config
        )
        self.assertEqual(model["edit"]["parameters"]["qualities"], ["low"])
        self.assertEqual(model["edit"]["parameters"]["sizes"], ["1024x1024"])
        self.assertFalse(model["edit"]["parameters"]["custom_size"])
        self.assertEqual(model["edit"]["parameters"]["max_images"], 2)
        self.assertEqual(model["edit"]["inputs"]["max_bytes"], 1024)
        self.assertEqual(model["edit"]["local_strategy"], "unavailable")
        self.assertIsNotNone(model["generation"])

    def test_no_common_parameter_values_disable_operation_instead_of_guessing_default(
        self,
    ):
        config = configuration()
        config["profiles"]["other"] = deepcopy(config["profiles"]["fixture-v1"])
        config["profiles"]["fixture-v1"]["generation"]["parameters"]["qualities"] = [
            "high"
        ]
        config["profiles"]["other"]["generation"]["parameters"]["qualities"] = ["low"]
        add_channel(config, profile="other")
        model = contracts.model_contract(
            "gpt-image-2", [route(), route(channel_id=6)], config
        )
        self.assertIsNone(model["generation"])
        self.assertIsNotNone(model["edit"])

    def test_rc40_rebuild_rejects_array_only_single_unless_pass_through(self):
        config = configuration("reference-only", array_only=True)
        accepted = contracts.model_contract(
            "gpt-image-2", [route(pass_through_body_enabled=True)], config
        )
        self.assertEqual(
            accepted["edit"]["_wire_fields"],
            {"single": "image[]", "multiple": "image[]"},
        )
        config["channels"]["5"].update(mode="rebuild", channel_pass_through=False)
        with self.assertRaisesRegex(contracts.ContractError, "单图 image"):
            contracts.validate_configuration(config)

    def test_mixed_rebuild_native_and_pass_through_array_share_an_input_wire(self):
        config = configuration()
        config["profiles"]["array"] = deepcopy(config["profiles"]["fixture-v1"])
        config["profiles"]["array"]["edit"]["image_fields"]["single"] = "image[]"
        add_channel(config, profile="array")
        config["channels"]["6"].update(mode="pass-through", channel_pass_through=True)
        config = contracts.validate_configuration(config)
        model = contracts.model_contract(
            "gpt-image-2",
            [route(), route(channel_id=6, pass_through_body_enabled=True)],
            config,
        )
        self.assertEqual(model["edit"]["_wire_fields"]["single"], "image[]")
        # rc40 rebuild changes this ONE image to image; pass-through retains image[].
        self.assertEqual(model["edit"]["parameters"]["max_images"], 4)

    def test_pass_through_rejects_mapping_and_mixed_file_fields_bound_cardinality(self):
        config = configuration(array_only=True)
        config["models"]["gpt-image-2"]["5"]["mapped_model"] = "provider-image"
        with self.assertRaisesRegex(contracts.ContractError, "模型映射"):
            contracts.validate_configuration(config)
        config = configuration(array_only=True)
        config["profiles"]["other"] = deepcopy(config["profiles"]["fixture-v1"])
        config["profiles"]["other"]["edit"]["image_fields"]["multiple"] = "image"
        add_channel(config, profile="other")
        model = contracts.model_contract(
            "gpt-image-2",
            [
                route(pass_through_body_enabled=True),
                route(channel_id=6, pass_through_body_enabled=True),
            ],
            config,
        )
        self.assertEqual(model["edit"]["parameters"]["max_images"], 1)

    def test_mapping_binding_and_every_safe_route_drift_invalidates_revision(self):
        config = configuration()
        original = contracts.model_contract("gpt-image-2", [route()], config)
        for name, value in {
            "type": 14,
            "model_mapping": {"gpt-image-2": "provider-other"},
            "pass_through_body_enabled": True,
            "global_pass_through_request_enabled": True,
            "no_param_override": False,
            "no_header_override": False,
            "no_affinity_override": False,
            "priority": 0,
            "weight": 2,
            "base_url_revision": "new",
            "settings_revision": "new",
            "affinity_revision": "new",
        }.items():
            with self.subTest(field=name):
                changed = contracts.model_contract(
                    "gpt-image-2", [route(**{name: value})], config
                )
                self.assertNotEqual(original["revision"], changed["revision"])
                if name in (
                    "priority",
                    "weight",
                    "base_url_revision",
                    "settings_revision",
                    "affinity_revision",
                ):
                    self.assertIsNotNone(changed["generation"])
                    self.assertIsNotNone(changed["edit"])
                else:
                    self.assertIsNone(changed["generation"])
                    self.assertIsNone(changed["edit"])
        config["profiles"]["fixture-v1"]["revision"] = "next"
        self.assertNotEqual(
            original["revision"],
            contracts.model_contract("gpt-image-2", [route()], config)["revision"],
        )

    def test_revision_ignores_secret_and_source_order_but_tracks_route_removal(self):
        config = configuration()
        add_channel(config)
        original = contracts.model_contract(
            "gpt-image-2", [route(), route(channel_id=6)], config
        )
        changed = contracts.model_contract(
            "gpt-image-2",
            [route(channel_id=6, key="NEVER-READ"), route(key="ANOTHER")],
            config,
        )
        self.assertEqual(original["revision"], changed["revision"])
        self.assertNotEqual(
            original["revision"],
            contracts.model_contract("gpt-image-2", [route()], config)["revision"],
        )

    def test_rc40_mapping_chains_self_maps_cycles_and_modifiers(self):
        config = configuration()
        config["models"]["gpt-image-2"]["5"]["mapped_model"] = "provider-image"
        mapping = {
            "gpt-image-2": "alias",
            "alias": "provider-image",
            "provider-image": "provider-image",
        }
        model = contracts.model_contract(
            "gpt-image-2", [route(model_mapping=mapping)], config
        )
        self.assertIsNotNone(model["edit"])
        for bad in (
            {"gpt-image-2": "alias", "alias": "gpt-image-2"},
            {"gpt-image-2": "gpt-image-2@reasoning=high"},
        ):
            with self.assertRaises(contracts.ContractError):
                contracts.model_contract(
                    "gpt-image-2", [route(model_mapping=bad)], config
                )

    def test_url_response_intersection_and_format_conflicts(self):
        config = configuration()
        response = {
            "format": "url",
            "allowed_hosts": ["cdn.example.com", "other.example.com"],
            "allowed_networks": ["8.8.8.0/24"],
        }
        config["profiles"]["fixture-v1"]["generation"]["response"] = response
        config["profiles"]["other"] = deepcopy(config["profiles"]["fixture-v1"])
        config["profiles"]["other"]["generation"]["response"] = {
            "format": "url",
            "allowed_hosts": ["cdn.example.com"],
            "allowed_networks": ["8.8.8.8/32"],
        }
        add_channel(config, profile="other")
        config = contracts.validate_configuration(config)
        model = contracts.model_contract(
            "gpt-image-2", [route(), route(channel_id=6)], config
        )
        self.assertEqual(
            model["generation"]["response"],
            config["profiles"]["other"]["generation"]["response"],
        )
        config["profiles"]["other"]["generation"]["response"]["format"] = "b64_json"
        self.assertIsNone(
            contracts.model_contract(
                "gpt-image-2", [route(), route(channel_id=6)], config
            )["generation"]
        )

    def test_strict_configuration_has_short_non_secret_errors(self):
        cases = []

        def bad(mutator):
            config = configuration()
            mutator(config)
            cases.append(config)

        bad(lambda c: c.update(schema=True))
        bad(lambda c: c.update(unknown="DO-NOT-ECHO"))
        bad(lambda c: c["profiles"].update(unused={"secret": "DO-NOT-ECHO"}))
        bad(lambda c: c["channels"]["5"].update(type=True))
        bad(lambda c: c["channels"]["5"].update(mode="mystery"))
        bad(lambda c: c["channels"]["5"].update(global_pass_through="false"))
        bad(lambda c: c["channels"]["5"].update(global_pass_through=True))
        bad(lambda c: c["models"]["gpt-image-2"]["5"].update(profile=[]))
        bad(lambda c: c["models"]["gpt-image-2"]["5"].update(mapped_model="\nsecret"))
        for name, value in (
            ("max_images", True),
            ("allowed_fields", ["n"]),
            ("sizes", ["9999x9999"]),
            ("custom_size", 1),
            ("formats", ["bmp"]),
            ("qualities", ["auto", "auto"]),
        ):
            bad(
                lambda c, name=name, value=value: c["profiles"]["fixture-v1"][
                    "generation"
                ]["parameters"].update({name: value})
            )
        for config in cases:
            with (
                self.subTest(config=config),
                self.assertRaises(contracts.ContractError) as caught,
            ):
                contracts.validate_configuration(config)
            self.assertLess(len(str(caught.exception)), 100)
            self.assertNotIn("DO-NOT-ECHO", str(caught.exception))
        for raw in ('{"schema":2,"schema":2}', '{"schema":NaN}', "[]", "null", "{"):
            with self.subTest(raw=raw), self.assertRaises(contracts.ContractError):
                contracts.validate_configuration(contracts.loads(raw))

    def test_strict_url_and_input_limits(self):
        for networks in (["10.0.0.0/8"], ["0.0.0.0/0"], ["8.8.8.1/24"], ["bad"]):
            config = configuration()
            config["profiles"]["fixture-v1"]["edit"]["response"] = {
                "format": "url",
                "allowed_hosts": ["cdn.example.com"],
                "allowed_networks": networks,
            }
            with (
                self.subTest(networks=networks),
                self.assertRaises(contracts.ContractError),
            ):
                contracts.validate_configuration(config)
        for hosts, networks in ((["cdn.example.com"], []), ([], ["8.8.8.8/32"])):
            config = configuration()
            response = {
                "format": "url",
                "allowed_hosts": hosts,
                "allowed_networks": networks,
            }
            config["profiles"]["fixture-v1"]["edit"]["response"] = response
            config = contracts.validate_configuration(config)
            model = contracts.model_contract("gpt-image-2", [route()], config)
            self.assertEqual(model["edit"]["response"], response)
        config["profiles"]["fixture-v1"]["edit"]["response"] = {
            "format": "url",
            "allowed_hosts": [],
            "allowed_networks": [],
        }
        with self.assertRaises(contracts.ContractError):
            contracts.validate_configuration(config)
        for field, value in (
            ("max_pixels", 16_000_001),
            ("max_bytes", 0),
            ("max_aspect_ratio", True),
            ("mime_types", []),
        ):
            config = configuration()
            config["profiles"]["fixture-v1"]["edit"]["inputs"][field] = value
            with self.subTest(field=field), self.assertRaises(contracts.ContractError):
                contracts.validate_configuration(config)

    def test_prompt_roles_annotations_and_reference_selection_are_deterministic(self):
        intent = {
            "operation": "local",
            "has_annotations": True,
            "reference_roles": ["subject", "style"],
        }
        prompt = contracts.compile_prompt("换成红色", intent, strategy="reference-only")
        self.assertIn("青色", prompt)
        self.assertIn("第3张图为人物或物体参考", prompt)
        self.assertIn("第4张图为风格参考", prompt)
        self.assertIn("不要将指导标记画入", prompt)
        self.assertEqual(
            prompt,
            contracts.compile_prompt("换成红色", intent, strategy="reference-only"),
        )
        native = contracts.compile_prompt("", intent, strategy="native-mask")
        self.assertIn("alpha", native)
        self.assertNotIn("青色", native)
        whole = contracts.compile_prompt(
            "", {**intent, "operation": "whole"}, strategy="reference-only"
        )
        self.assertNotIn("选区；", whole)
        self.assertNotIn("青色", whole)
        gen = {"operation": "generate", "has_annotations": False, "reference_roles": []}
        self.assertEqual(contracts.compile_prompt("hello", gen, strategy=None), "hello")


if __name__ == "__main__":
    unittest.main()
