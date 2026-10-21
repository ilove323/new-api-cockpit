"""Owner-authorized Images use cases; external services are explicitly injected."""

import logging
import os
from pathlib import Path
import re

from ...platform.identity import Principal, RequestAccess
from . import contracts as image_contracts, source_routes
from .capacity import capacity as capacity
from .capacity import health as health
from .capacity import initialize_capacity as initialize_capacity
from .capacity import process_role as process_role
from .ports import SourceDb, StudioError, TokenProvider, token_id

# A single tuple is replaced atomically at application setup, never per request.
_dependencies = None


def configure(tokens: TokenProvider, source_db: SourceDb):
    """Install dependencies without I/O. Call once per host setup before serving."""
    global _dependencies
    if not all(
        callable(getattr(tokens, name, None))
        for name in ("list_tokens", "list_models", "lease_key")
    ) or not callable(getattr(source_db, "read_connection", None)):
        raise TypeError("Image Studio requires TokenProvider and SourceDb")
    _dependencies = (tokens, source_db)


def _providers():
    if _dependencies is None:
        raise StudioError("生图工作站依赖尚未初始化。", 503)
    return _dependencies


def _access(user, credential=None):
    if isinstance(user, RequestAccess):
        return user
    return RequestAccess(
        Principal(
            user_id=user["id"],
            username=user.get("username", ""),
            role=user.get("role", 1),
            session_id=user.get("session_id"),
            expires_at=user.get("expires_at"),
            auth_kind=user.get("auth_kind", "session"),
        ),
        credential,
    )


def _routes():
    return source_routes.load_routes(_providers()[1])


def list_tokens(user, credential=None):
    rows = _providers()[0].list_tokens(_access(user, credential), group="GPT")
    return {
        "tokens": [row for row in rows if row["group"] == "GPT"],
        "token_manager_path": "/keys",
    }


def _configuration():
    filename = os.environ.get("IMAGE_STUDIO_CAPABILITIES_FILE")
    if not filename:
        return image_contracts.validate_configuration(image_contracts.EMPTY_CONFIG)
    try:
        with Path(filename).open("rb") as file:
            raw = file.read(65537)
        if len(raw) > 65536:
            raise StudioError("图片协议配置超过 64 KiB。", 503, "IMAGE_CONTRACT_CONFIG")
        return image_contracts.validate_configuration(image_contracts.loads(raw))
    except image_contracts.ContractError as exc:
        raise StudioError(str(exc), 503, "IMAGE_CONTRACT_CONFIG") from None
    except OSError:
        raise StudioError(
            "图片协议配置无法读取，请联系管理员。", 503, "IMAGE_CONTRACT_CONFIG"
        ) from None


def _contract(model, routes, config):
    try:
        return image_contracts.model_contract(
            model, [r for r in routes if r["model"] == model], config
        )
    except image_contracts.ContractError as exc:
        raise StudioError(str(exc), 503, "IMAGE_CONTRACT_CONFIG") from None


def _discover(user, credential, body):
    target = token_id(body)
    # Adapter-owned list_models already validates ownership/status and computes
    # /v1/models intersected with strict token model_limits. No raw KEY escapes it.
    try:
        entries = _providers()[0].list_models(
            _access(user, credential), target, group="GPT"
        )
    except ValueError as exc:
        # Stable, non-secret adapter diagnostics retain the old image-specific
        # errors without importing an integration implementation/exception.
        messages = {
            "model_limits_enabled": "令牌模型限制信息无效，请检查 New API 版本。",
            "model_limits": "令牌模型限制响应无效。",
            "model_catalogue": "New API 图片模型目录响应无效。",
        }
        reason = getattr(exc, "reason", None)
        if isinstance(reason, str) and reason in messages:
            raise StudioError(messages[reason], 502) from None
        raise
    if (
        not isinstance(entries, list)
        or len(entries) > 10000
        or any(
            not isinstance(v, dict) or not isinstance(v.get("id"), str) for v in entries
        )
    ):
        raise StudioError("New API 图片模型目录响应无效。", 502)
    config = _configuration()
    routes = _routes()
    authorized = {v["id"] for v in entries}
    names = {
        route["model"]
        for route in routes
        if isinstance(route["model"], str)
        and route["model"].startswith("gpt-image-")
        and route["model"] in authorized
    }
    return {"models": [_contract(name, routes, config) for name in sorted(names)]}


def models(user, credential, body):
    catalogue = _discover(user, credential, body)
    return {
        "models": [
            image_contracts.public_contract(model) for model in catalogue["models"]
        ]
    }


def _refresh():
    return StudioError(
        "图片能力契约已变化，请刷新模型后重新提交。",
        409,
        "IMAGE_CONTRACT_CHANGED",
        details={"category": "conflict"},
    )


def parse_request(body):
    if isinstance(body, dict) and "contract_revision" not in body:
        raise _refresh()
    required = {
        "token_id",
        "model",
        "prompt",
        "parameters",
        "request_id",
        "contract_revision",
        "intent",
    }
    if not isinstance(body, dict) or set(body) != required:
        raise StudioError("图片请求字段缺失或包含不允许的字段。")
    token_id({"token_id": body["token_id"]})
    if not image_contracts._text(body["model"]) or not body["model"].startswith(
        "gpt-image-"
    ):
        raise StudioError("请选择有效的 gpt-image 模型。")
    prompt = body["prompt"]
    if not isinstance(prompt, str) or len(prompt) > 32000 or "\x00" in prompt:
        raise StudioError("修改说明最多 32000 个字符，且不能包含空字符。")
    try:
        prompt.encode("utf-8")
    except UnicodeError:
        raise StudioError("修改说明含无效的 Unicode 字符。") from None
    if not isinstance(body["request_id"], str) or not re.fullmatch(
        r"[A-Za-z0-9_-]{8,128}", body["request_id"]
    ):
        raise StudioError("请求 ID 无效。")
    if not isinstance(body["contract_revision"], str) or not re.fullmatch(
        r"[0-9a-f]{64}", body["contract_revision"]
    ):
        raise _refresh()
    if not isinstance(body["parameters"], dict):
        raise StudioError("图片参数必须为 JSON 对象。")
    intent = body["intent"]
    if (
        not isinstance(intent, dict)
        or set(intent) != {"operation", "has_annotations", "reference_roles"}
        or intent["operation"] not in ("generate", "whole", "local")
        or type(intent["has_annotations"]) is not bool
        or not isinstance(intent["reference_roles"], list)
        or len(intent["reference_roles"]) > 3
        or any(
            not isinstance(role, str) or role not in image_contracts.ROLE_NAMES
            for role in intent["reference_roles"]
        )
    ):
        raise StudioError("编辑意图无效，请刷新页面。")
    if intent["operation"] == "generate" and (
        intent["has_annotations"] or intent["reference_roles"]
    ):
        raise StudioError("文生图不能包含标注或参考图片。")
    prompt = prompt.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not prompt and (
        intent["operation"] == "generate" or not intent["has_annotations"]
    ):
        raise StudioError("请输入修改说明或添加指导标注；只有选区不能提交。")
    return {
        **body,
        "prompt": prompt,
        "parameters": dict(body["parameters"]),
        "intent": {**intent, "reference_roles": list(intent["reference_roles"])},
    }


def parameters(values, allowed, *, edit=False):
    if not isinstance(values, dict) or set(values) - set(allowed["allowed_fields"]):
        raise StudioError("所选操作包含不支持的图片参数。")
    result = dict(values)
    # Defaults are emitted only when that field AND that value are explicit in
    # the common contract. Unsupported mandatory-looking UI controls are omitted.
    for field, default in (
        ("size", "auto"),
        ("quality", "auto"),
        ("output_format", "png"),
    ):
        if (
            field in allowed["allowed_fields"]
            and field not in result
            and default in allowed[image_contracts.PARAMETER_LISTS[field]]
        ):
            result[field] = default
    for field, options in image_contracts.PARAMETER_LISTS.items():
        if field not in result:
            continue
        value = result[field]
        if not isinstance(value, str) or (
            value not in allowed[options]
            and not (
                field == "size"
                and allowed["custom_size"]
                and image_contracts._size(value)
            )
        ):
            raise StudioError(
                "所选操作不支持该图片参数值。",
                details={"category": "input", "param": field},
            )
    if "input_fidelity" in result and not edit:
        raise StudioError("生成操作不接受输入保真参数。")
    if result.get("background") == "transparent" and result.get(
        "output_format"
    ) not in ("png", "webp"):
        raise StudioError("透明背景需要明确选择 PNG 或 WebP。")
    if "output_compression" in result and (
        type(result["output_compression"]) is not int
        or not 0 <= result["output_compression"] <= 100
        or result.get("output_format") not in ("jpeg", "webp")
    ):
        raise StudioError("压缩参数只允许 JPEG/WebP 的 0–100 整数。")
    if result.get("background") == "auto":
        result.pop("background")
    return result


def _validate_inputs(body, op, images, mask):
    from . import transport as image_transport
    from io import BytesIO
    from PIL import Image

    intent = body["intent"]
    if intent["operation"] == "generate":
        if images is not None or mask is not None:
            raise StudioError("文生图不能包含输入图片或遮罩。")
        return
    if not isinstance(images, (list, tuple)) or not images:
        raise StudioError("编辑操作需要干净主图。")
    local = intent["operation"] == "local"
    strategy = op["local_strategy"]
    if local and strategy == "unavailable":
        raise StudioError("当前共同协议不支持局部编辑。", 409)
    guide = intent["has_annotations"] or (local and strategy == "reference-only")
    expected = 1 + int(guide) + len(intent["reference_roles"])
    if len(images) != expected or len(images) > op["parameters"]["max_images"]:
        raise StudioError("主图、指导图与参考用途的数量不符或超过模型限制。")
    if (local and strategy == "native-mask") != (mask is not None):
        raise StudioError("原生局部编辑必须带遮罩；整图和参考图协议不能发送遮罩。")
    limits = op["inputs"]
    records = list(images) + ([] if mask is None else [mask])
    if any(
        not isinstance(r, dict) or not isinstance(r.get("data"), bytes) for r in records
    ):
        raise StudioError("输入图片记录无效。")
    if sum(len(r["data"]) for r in records) > image_transport.MAX_UPLOAD_BYTES - 65536:
        raise StudioError("输入图片总大小超过限制。", 413)
    total = 0
    for index, item in enumerate(images):
        checked = image_transport._checked_record(item)
        w, h = checked["width"], checked["height"]
        total += w * h
        if (
            checked["mime"] not in limits["mime_types"]
            or len(checked["data"]) > limits["max_bytes"]
            or w * h > limits["max_pixels"]
            or max(w, h) > limits["max_dimension"]
            or max(w, h) > limits["max_aspect_ratio"] * min(w, h)
        ):
            raise StudioError("输入图片超出当前协议的类型、大小或尺寸限制。", 400)
        if guide and index == 1 and (w, h) != (images[0]["width"], images[0]["height"]):
            raise StudioError("指导图必须与干净主图同尺寸、同坐标。")
    if total > limits["max_total_pixels"]:
        raise StudioError("输入图片总像素超过当前协议限制。", 413)
    if mask is not None:
        checked = image_transport._checked_record(mask, mask=True)
        if (checked["width"], checked["height"]) != (
            images[0]["width"],
            images[0]["height"],
        ):
            raise StudioError("遮罩必须与干净主图同尺寸。")
        with Image.open(BytesIO(checked["data"])) as decoded:
            with decoded.convert("RGBA") as rgba:
                if rgba.getchannel("A").getextrema()[0] == 255:
                    raise StudioError("局部修改请先选择修改区域。")


def _compiled_prompt(body, operation):
    prompt = image_contracts.compile_prompt(
        body["prompt"], body["intent"], strategy=operation.get("local_strategy")
    )
    try:
        encoded = prompt.encode("utf-8")
    except UnicodeError:
        raise StudioError(
            "整理后的修改说明含无效 Unicode 字符。",
            details={"category": "input", "param": "prompt"},
        ) from None
    # Budget the compiled instructions, not only the raw text. Never truncate
    # user instructions to make an otherwise invalid paid request fit.
    if len(prompt) > 32000 or len(encoded) > 128000 or "\x00" in prompt:
        raise StudioError(
            "加入指导图和参考用途说明后超过长度限制，请缩短修改说明（最多 32000 字符、128000 UTF-8 字节）；原文未截断。",
            details={"category": "input", "param": "prompt"},
        )
    return prompt


def run(user, credential, body, *, images=None, mask=None):
    from . import receipts as image_receipts, transport as image_transport

    body = parse_request(body)
    access = _access(user, credential)
    user = access.principal.as_user()
    catalogue = _discover(access, None, {"token_id": body["token_id"]})
    with _providers()[0].lease_key(access, body["token_id"], group="GPT") as key:
        selected = next(
            (m for m in catalogue["models"] if m["id"] == body["model"]), None
        )
        if selected is None:
            raise StudioError("该令牌当前无权使用所选图片模型。", 403)
        if body["contract_revision"] != selected["revision"]:
            raise _refresh()
        edit = body["intent"]["operation"] != "generate"
        op = selected["edit" if edit else "generation"]
        if op is None:
            raise StudioError(
                selected.get("unavailable_reason", "当前模型未配置此图片操作。"),
                409,
                "IMAGE_OPERATION_UNAVAILABLE",
            )
        params = parameters(body["parameters"], op["parameters"], edit=edit)
        try:
            _validate_inputs(body, op, images, mask)
        except image_transport.ImageError as exc:
            raise StudioError(
                str(exc),
                exc.status,
                exc.code,
                exc.uncertain,
                getattr(exc, "details", None),
            ) from None
        payload = {
            "model": body["model"],
            "prompt": _compiled_prompt(body, op),
            "n": 1,
            **params,
        }
        if "request_response_format" in op:
            payload["response_format"] = op["request_response_format"]
        kwargs = {"response_contract": op["response"]}
        path = "/v1/images/edits" if edit else "/v1/images/generations"
        if edit:
            kwargs.update(
                fields=payload,
                images=images,
                mask=mask,
                image_field=op["_wire_fields"][
                    "single" if len(images) == 1 else "multiple"
                ],
            )
        else:
            kwargs["payload"] = payload
        # A fresh source/config read closes drift during KEY discovery/file validation.
        # New API cannot atomically lock routing with our POST; deployment must keep
        # its in-memory channel cache synchronized with these source settings.
        latest = _contract(body["model"], _routes(), _configuration())
        if latest["revision"] != body["contract_revision"]:
            raise _refresh()
        digest = image_receipts.digest_request(body, images=images, mask=mask)
        receipt = image_receipts.claim(user["id"], body["request_id"], digest)
        try:
            result = image_transport.request_image(path, key, **kwargs)
        except image_transport.ImageError as exc:
            receipt.finish("unknown" if exc.uncertain else "failed")
            logging.getLogger(__name__).warning(
                "Studio image failed request_id=%s user_id=%s code=%s uncertain=%s",
                body["request_id"],
                user["id"],
                exc.code,
                exc.uncertain,
            )
            raise StudioError(
                str(exc),
                exc.status,
                exc.code,
                exc.uncertain,
                getattr(exc, "details", None),
            ) from None
        except Exception:
            receipt.finish("unknown")
            raise StudioError(
                "图片请求结果未知，请先查看用量，勿重复提交。",
                502,
                "IMAGE_RESULT_UNKNOWN",
                True,
                {"category": "network_unknown"},
            ) from None
        receipt.finish("succeeded")
        return {**result, "model": body["model"], "request_id": body["request_id"]}
