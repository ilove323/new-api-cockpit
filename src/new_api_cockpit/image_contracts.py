"""Versioned image protocols, route intersections and deterministic input compilation.

No family-name/type defaults, network calls, credentials or paid retries. Schema 2
binds each public model AND channel to an expected mapped model and a profile.
The profile describes provider fields; the channel describes rc.40's forwarding
behaviour. Only the common Cockpit->New API wire contract is used for dispatch.
"""

from copy import deepcopy
import hashlib
import ipaddress
import json
import re


class ContractError(ValueError):
    """Short, non-secret operator diagnostic (never echo configuration values)."""


PARAMETER_LISTS = {
    "size": "sizes",
    "quality": "qualities",
    "output_format": "formats",
    "background": "backgrounds",
    "input_fidelity": "input_fidelity",
}
PARAMETER_FIELDS = set(PARAMETER_LISTS) | {"output_compression"}
MIMES = {"image/png", "image/jpeg", "image/webp"}
INPUT_LIMITS = {
    "mime_types": sorted(MIMES),
    "max_bytes": 8 * 1024 * 1024,
    "max_pixels": 16_000_000,
    "max_total_pixels": 32_000_000,
    "max_dimension": 16000,
    "max_aspect_ratio": 100,
}
EMPTY_CONFIG = {"schema": 2, "profiles": {}, "channels": {}, "models": {}}
ROLE_NAMES = {
    "reference": "一般参考",
    "subject": "人物或物体参考",
    "style": "风格参考",
    "composition": "构图参考",
}


def _fail(message):
    raise ContractError("图片协议配置：" + message + "。")


def _keys(value, required, optional=()):
    if (
        not isinstance(value, dict)
        or not set(required) <= set(value)
        or set(value) - (set(required) | set(optional))
    ):
        _fail("字段缺失或存在未知字段")


def _text(value, limit=256):
    return (
        isinstance(value, str)
        and 0 < len(value) <= limit
        and value == value.strip()
        and all(
            ord(c) >= 32 and ord(c) != 127 and not 0xD800 <= ord(c) <= 0xDFFF
            for c in value
        )
    )


def _list(value, choices=None, *, empty=True):
    if (
        not isinstance(value, list)
        or len(value) > 32
        or (not empty and not value)
        or any(not _text(item) for item in value)
        or len(set(value)) != len(value)
        or (choices is not None and set(value) - set(choices))
    ):
        _fail("枚举列表无效")


def _integer(value, lower, upper):
    if type(value) is not int or not lower <= value <= upper:
        _fail("数量或资源限制无效")


def _size(value):
    match = re.fullmatch(r"([1-9][0-9]{0,3})x([1-9][0-9]{0,3})", value)
    if not match:
        return False
    w, h = map(int, match.groups())
    return (
        max(w, h) <= 3840
        and not w % 16
        and not h % 16
        and max(w, h) <= 3 * min(w, h)
        and 655360 <= w * h <= 8294400
    )


def _validate_parameters(params, edit):
    _keys(
        params,
        {
            "allowed_fields",
            "sizes",
            "custom_size",
            "qualities",
            "formats",
            "backgrounds",
            "input_fidelity",
            "max_images",
        },
    )
    _list(params["allowed_fields"], PARAMETER_FIELDS)
    _list(params["sizes"])
    if any(s != "auto" and not _size(s) for s in params["sizes"]):
        _fail("尺寸枚举无效")
    _list(params["qualities"], {"auto", "low", "medium", "high", "xhigh", "max"})
    _list(params["formats"], {"png", "jpeg", "webp"})
    _list(params["backgrounds"], {"auto", "opaque", "transparent"})
    _list(params["input_fidelity"], {"low", "high"})
    if type(params["custom_size"]) is not bool:
        _fail("custom_size 必须为布尔值")
    _integer(params["max_images"], 1 if edit else 0, 4 if edit else 0)
    for field, values in PARAMETER_LISTS.items():
        if (field in params["allowed_fields"]) != bool(params[values]):
            _fail("allowed_fields 与参数枚举不一致")
    if params["custom_size"] and "size" not in params["allowed_fields"]:
        _fail("自定义尺寸需要 size 字段")
    if not edit and "input_fidelity" in params["allowed_fields"]:
        _fail("生成协议不能配置 input_fidelity")
    if "output_compression" in params["allowed_fields"] and not (
        {"jpeg", "webp"} & set(params["formats"])
    ):
        _fail("压缩参数需要 JPEG 或 WebP")


def _validate_response(response):
    _keys(response, {"format", "allowed_hosts", "allowed_networks"})
    if response["format"] not in ("b64_json", "url"):
        _fail("响应格式只支持 b64_json 或 url")
    _list(response["allowed_hosts"])
    _list(response["allowed_networks"])
    if response["format"] == "b64_json":
        if response["allowed_hosts"] or response["allowed_networks"]:
            _fail("Base64 响应不能配置 URL 白名单")
        return
    if not response["allowed_hosts"] and not response["allowed_networks"]:
        _fail("URL 响应需要域名或公网网络白名单")
    for host in response["allowed_hosts"]:
        if (
            host != host.lower()
            or len(host) > 253
            or not all(
                re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", part)
                for part in host.split(".")
            )
        ):
            _fail("URL 域名白名单无效")
    for cidr in response["allowed_networks"]:
        try:
            network = ipaddress.ip_network(cidr, strict=True)
            if (
                not network.is_global
                or not network.network_address.is_global
                or not network.broadcast_address.is_global
            ):
                raise ValueError()
        except ValueError:
            _fail("URL 网络白名单必须是公网 CIDR")


def _validate_operation(op, *, edit):
    if op is None:
        return
    _keys(
        op,
        {"parameters", "response"}
        | ({"local_strategy", "image_fields"} if edit else set()),
        {"request_response_format", "inputs"} if edit else {"request_response_format"},
    )
    _validate_parameters(op["parameters"], edit)
    _validate_response(op["response"])
    if (
        "request_response_format" in op
        and op["request_response_format"] != op["response"]["format"]
    ):
        _fail("请求与响应格式不一致")
    if not edit:
        return
    if op["local_strategy"] not in ("native-mask", "reference-only", "unavailable"):
        _fail("局部编辑策略无效")
    _keys(op["image_fields"], {"single", "multiple"})
    if any(v not in ("image", "image[]") for v in op["image_fields"].values()):
        _fail("图片字段只支持 image 或 image[]")
    if op["local_strategy"] == "reference-only" and op["parameters"]["max_images"] < 2:
        _fail("参考图局部编辑至少需要两张输入")
    limits = op.setdefault("inputs", deepcopy(INPUT_LIMITS))
    _keys(limits, INPUT_LIMITS)
    _list(limits["mime_types"], MIMES, empty=False)
    for field, maximum in INPUT_LIMITS.items():
        if field != "mime_types":
            _integer(limits[field], 1, maximum)


def validate_configuration(value):
    """Validate the entire file, including currently unbound profiles/channels."""
    value = deepcopy(value)
    _keys(value, {"schema", "profiles", "channels", "models"})
    if type(value["schema"]) is not int or value["schema"] != 2:
        _fail("需要 schema=2")
    for field in ("profiles", "channels", "models"):
        if not isinstance(value[field], dict) or len(value[field]) > 256:
            _fail("目录必须为有界对象")
    for name, profile in value["profiles"].items():
        if not _text(name, 64):
            _fail("协议档名称无效")
        _keys(profile, {"revision", "evidence", "generation", "edit"})
        if not _text(profile["revision"], 64) or not _text(profile["evidence"], 1024):
            _fail("协议档需要版本和证据说明")
        _validate_operation(profile["generation"], edit=False)
        _validate_operation(profile["edit"], edit=True)
    for channel_id, channel in value["channels"].items():
        if (
            not isinstance(channel_id, str)
            or not re.fullmatch(r"[1-9][0-9]{0,9}", channel_id)
            or int(channel_id) > 2147483647
        ):
            _fail("渠道 ID 无效")
        _keys(
            channel,
            {"type", "gateway", "mode", "global_pass_through", "channel_pass_through"},
        )
        if (
            type(channel["type"]) is not int
            or channel["type"] != 1
            or channel["gateway"] != "new-api-rc40-openai"
        ):
            _fail("仅支持已实现的 rc40 OpenAI adaptor")
        if channel["mode"] not in ("rebuild", "pass-through") or any(
            type(channel[k]) is not bool
            for k in ("global_pass_through", "channel_pass_through")
        ):
            _fail("网关转发模式或透传前置条件无效")
        enabled = channel["global_pass_through"] or channel["channel_pass_through"]
        if enabled != (channel["mode"] == "pass-through"):
            _fail("转发模式与透传前置条件矛盾")
    for model, bindings in value["models"].items():
        if (
            not _text(model)
            or not model.startswith("gpt-image-")
            or not isinstance(bindings, dict)
            or not bindings
        ):
            _fail("模型绑定无效")
        for channel_id, binding in bindings.items():
            _keys(binding, {"profile", "mapped_model"})
            if (
                channel_id not in value["channels"]
                or not isinstance(binding["profile"], str)
                or binding["profile"] not in value["profiles"]
            ):
                _fail("模型绑定引用了不存在的渠道或协议档")
            if not _text(binding["mapped_model"]):
                _fail("绑定的上游模型无效")
            channel = value["channels"][channel_id]
            if channel["mode"] == "pass-through" and binding["mapped_model"] != model:
                _fail("透传模式不能依赖模型映射")
            edit = value["profiles"][binding["profile"]]["edit"]
            if (
                edit
                and channel["mode"] == "rebuild"
                and (
                    edit["image_fields"]["single"] != "image"
                    or (
                        edit["parameters"]["max_images"] > 1
                        and edit["image_fields"]["multiple"] != "image[]"
                    )
                )
            ):
                _fail("rc40 重构模式必须单图 image、多图 image[]；其他字段需要透传")
    return value


def loads(raw):
    def object_pairs(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                _fail("JSON 字段重复")
            result[key] = value
        return result

    try:
        return json.loads(
            raw,
            object_pairs_hook=object_pairs,
            parse_constant=lambda _: _fail("JSON 非有限数值"),
        )
    except (ValueError, UnicodeError, RecursionError):
        _fail("JSON 无效或过深")


def _json_object(value):
    if value in (None, ""):
        return {}
    if isinstance(value, str):
        if len(value) > 65536:
            _fail("源库模型映射过大")
        value = loads(value)
    if not isinstance(value, dict) or any(
        not _text(k) or not _text(v) for k, v in value.items()
    ):
        _fail("源库模型映射无效")
    return value


def _mapped_model(model, mapping):
    # rc.40 follows the entire mapping chain (a self-map terminates it).
    # Explicit @ model modifiers need a different gateway contract; their
    # global alias/blacklist policies must not be guessed by this compiler.
    current, seen = model, {model}
    while True:
        if "@" in current:
            _fail("图片协议不支持模型修饰符")
        target = mapping.get(current, current)
        if target == current:
            return current
        if target in seen:
            _fail("源库模型映射存在循环")
        seen.add(target)
        current = target


def _route(model, row, config):
    """Return contract + non-secret revision material; never keep whole DB rows."""
    channel_id = str(row["channel_id"])
    channel = config["channels"].get(channel_id)
    binding = config["models"].get(model, {}).get(channel_id)
    mapped = _mapped_model(model, _json_object(row.get("model_mapping")))
    # Whitelist hashes/properties selected by _routes; no KEY/header/settings values.
    source = {
        k: row.get(k)
        for k in (
            "channel_id",
            "type",
            "priority",
            "weight",
            "base_url_revision",
            "settings_revision",
            "pass_through_body_enabled",
            "global_pass_through_request_enabled",
            "no_param_override",
            "no_header_override",
            "no_affinity_override",
            "affinity_revision",
        )
    }
    source["mapped_model"] = mapped
    source["binding"] = binding
    source["channel"] = channel
    if not channel or not binding:
        return None, source, "存在未配置协议的图片路由，请联系管理员。"
    profile = config["profiles"][binding["profile"]]
    source["profile"] = profile
    if row["type"] != channel["type"] or mapped != binding["mapped_model"]:
        return None, source, "图片路由类型或模型映射已变化，请联系管理员。"
    for observed, expected in (
        ("pass_through_body_enabled", "channel_pass_through"),
        ("global_pass_through_request_enabled", "global_pass_through"),
    ):
        if type(row.get(observed)) is not bool or row[observed] != channel[expected]:
            return None, source, "图片路由透传条件不符，请联系管理员。"
    if (
        row.get("no_param_override") is not True
        or row.get("no_header_override") is not True
        or row.get("no_affinity_override") is not True
    ):
        return None, source, "图片路由存在未支持的参数或请求头覆盖，请联系管理员。"
    result = deepcopy(profile)
    if result["edit"]:
        result["edit"]["_wire_fields"] = {
            count: (
                ["image", "image[]"]
                if channel["mode"] == "rebuild"
                else [result["edit"]["image_fields"][count]]
            )
            for count in ("single", "multiple")
        }
    return result, source, None


def _intersect_lists(items):
    return sorted(set(items[0]).intersection(*map(set, items[1:])))


def _common(operations, *, edit):
    if any(op is None for op in operations):
        return None
    result = deepcopy(operations[0])
    if any(
        op["response"]["format"] != result["response"]["format"]
        or op.get("request_response_format") != result.get("request_response_format")
        for op in operations
    ):
        return None
    if result["response"]["format"] == "url":
        result["response"]["allowed_hosts"] = _intersect_lists(
            [op["response"]["allowed_hosts"] for op in operations]
        )
        networks = [
            ipaddress.ip_network(n) for n in result["response"]["allowed_networks"]
        ]
        for op in operations[1:]:
            others = [
                ipaddress.ip_network(n) for n in op["response"]["allowed_networks"]
            ]
            networks = list(
                {
                    a if a.prefixlen >= b.prefixlen else b
                    for a in networks
                    for b in others
                    if a.version == b.version and a.overlaps(b)
                }
            )
        result["response"]["allowed_networks"] = sorted(map(str, networks))
        if not result["response"]["allowed_hosts"] and not networks:
            return None
    params = result["parameters"]
    params["allowed_fields"] = _intersect_lists(
        [op["parameters"]["allowed_fields"] for op in operations]
    )
    params["custom_size"] = all(op["parameters"]["custom_size"] for op in operations)
    params["max_images"] = min(op["parameters"]["max_images"] for op in operations)
    for field, key in PARAMETER_LISTS.items():
        if field == "size":
            candidates = set().union(*(op["parameters"][key] for op in operations))
            params[key] = sorted(
                size
                for size in candidates
                if all(
                    size in op["parameters"][key]
                    or (op["parameters"]["custom_size"] and _size(size))
                    for op in operations
                )
            )
        else:
            params[key] = _intersect_lists([op["parameters"][key] for op in operations])
        if field not in params["allowed_fields"]:
            params[key] = []
        elif not params[key]:
            # Do not assume a provider's omitted-field default is valid when all
            # routes advertise a field but have no common accepted value.
            return None
    if "size" not in params["allowed_fields"]:
        params["custom_size"] = False
    if (
        not ({"jpeg", "webp"} & set(params["formats"]))
        and "output_compression" in params["allowed_fields"]
    ):
        params["allowed_fields"].remove("output_compression")
    if not edit:
        return result
    if any(op["local_strategy"] != result["local_strategy"] for op in operations):
        result["local_strategy"] = "unavailable"
    for key in ("single", "multiple"):
        fields = _intersect_lists([op["_wire_fields"][key] for op in operations])
        if not fields:
            if key == "single":
                return None
            params["max_images"] = 1
        result["_wire_fields"][key] = (
            "image[]" if "image[]" in fields else (fields[0] if fields else None)
        )
    if result["local_strategy"] == "reference-only" and params["max_images"] < 2:
        result["local_strategy"] = "unavailable"
    for key in INPUT_LIMITS:
        result["inputs"][key] = (
            _intersect_lists([op["inputs"][key] for op in operations])
            if key == "mime_types"
            else min(op["inputs"][key] for op in operations)
        )
    if not result["inputs"]["mime_types"]:
        return None
    return result


def model_contract(model, routes, config):
    profiles, material, reasons = [], [], []
    for row in sorted(routes, key=lambda r: r["channel_id"]):
        profile, source, reason = _route(model, row, config)
        profiles.append(profile)
        material.append(source)
        if reason:
            reasons.append(reason)
    operations = {}
    for name in ("generation", "edit"):
        operations[name] = (
            _common([p[name] if p else None for p in profiles], edit=name == "edit")
            if profiles
            else None
        )
    revision = hashlib.sha256(
        json.dumps(
            {
                "schema": 2,
                "compiler": 1,
                "model": model,
                "routes": material,
                "operations": operations,
            },
            sort_keys=True,
            ensure_ascii=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode()
    ).hexdigest()
    result = {"id": model, "revision": revision, **operations}
    if any(value is None for value in operations.values()) or (
        operations["edit"] and operations["edit"]["local_strategy"] == "unavailable"
    ):
        result["unavailable_reason"] = (
            reasons[0]
            if reasons
            else "部分操作没有所有路由共同支持的图片协议，请联系管理员。"
        )
    return result


def public_contract(model):
    result = {"id": model["id"], "revision": model["revision"]}
    for name in ("generation", "edit"):
        op = model[name]
        result[name] = (
            None if op is None else {"parameters": deepcopy(op["parameters"])}
        )
        if op and name == "edit":
            result[name].update(
                local_strategy=op["local_strategy"],
                max_images=op["parameters"]["max_images"],
                inputs=deepcopy(op["inputs"]),
            )
    if "unavailable_reason" in model:
        result["unavailable_reason"] = model["unavailable_reason"]
    return result


def compile_prompt(prompt, intent, *, strategy):
    """Use only explicit user intent; annotations/selection never invent a task."""
    if intent["operation"] == "generate":
        return prompt
    lines = ["第1张图是干净主图，请在主图基础上编辑。"]
    guide = intent["has_annotations"] or (
        intent["operation"] == "local" and strategy == "reference-only"
    )
    if guide:
        lines.append("第2张图是与主图同坐标的指导图，不是另一张待生成的图片。")
    if intent["operation"] == "local":
        if strategy == "native-mask":
            lines.append("仅修改 alpha 遮罩的透明选区；保留区域外内容。")
        else:
            lines.append(
                "仅修改指导图青色覆盖的选区；保留区域外内容。青色覆盖只表示修改范围。"
            )
    if intent["has_annotations"]:
        lines.append(
            "按指导图的文字、圈线和箭头理解修改要求；不要将指导标记画入最终图片。"
        )
    if guide:
        lines.append("最终输出应为无选区覆盖、无指导标记的编辑结果。")
    for index, role in enumerate(intent["reference_roles"], 3 if guide else 2):
        lines.append(f"第{index}张图为{ROLE_NAMES[role]}，仅按该用途参考，不替代主图。")
    if prompt:
        lines.append("用户补充修改要求：\n" + prompt)
    return "\n".join(lines)
