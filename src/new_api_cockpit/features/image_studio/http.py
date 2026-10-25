"""Image HTTP boundary. Authentication/authorization are supplied by the host."""

import re

from flask import Blueprint, jsonify, render_template, request
from werkzeug.exceptions import HTTPException

from ...platform.http_headers import apply_retry_after

from . import service as image_studio
from .receipts import ReceiptError

PAGE_ENDPOINTS = frozenset({"image_studio.page"})
PAID_ENDPOINTS = frozenset({"image_studio.generations", "image_studio.edits"})
USER_ENDPOINTS = (
    PAGE_ENDPOINTS
    | PAID_ENDPOINTS
    | frozenset({"image_studio.tokens", "image_studio.models"})
)
ROLE_ENDPOINTS = {
    "web": PAGE_ENDPOINTS | frozenset({"image_studio.tokens", "image_studio.models"}),
    "image": PAID_ENDPOINTS,
    "chat": frozenset(),
}


def studio_request(host, *, multipart=False):
    if request.headers.get("X-Cockpit-Studio") != "1":
        raise image_studio.StudioError("请求缺少 X-Cockpit-Studio: 1。", 403)
    if multipart:
        if request.mimetype != "multipart/form-data":
            raise image_studio.StudioError("改图请求需要 multipart/form-data。", 400)
        request.max_content_length = 24 * 1024 * 1024
        request.max_form_memory_size = 192 * 1024
        request.max_form_parts = 16
    else:
        if not request.is_json:
            raise image_studio.StudioError("请求需要 JSON。", 400)
        request.max_content_length = 192 * 1024
    # Preserve the existing application/*+json support after is_json validation.
    host.validate_write("X-Cockpit-Studio", request.mimetype)
    if request.content_length and request.content_length > request.max_content_length:
        raise image_studio.StudioError("图片请求过大。", 413)


def studio_edit_body():
    from . import transport as image_transport
    from PIL import Image
    from io import BytesIO
    import json

    form = request.form
    required = {
        "token_id",
        "model",
        "prompt",
        "parameters",
        "request_id",
        "contract_revision",
        "intent",
    }
    if "contract_revision" not in form or "intent" not in form:
        raise image_studio.StudioError(
            "工作站协议已更新，请刷新模型后重新提交。", 409, "IMAGE_CONTRACT_CHANGED"
        )
    if set(form) != required or any(len(form.getlist(k)) != 1 for k in required):
        raise image_studio.StudioError("改图表单字段无效或重复。")
    if not re.fullmatch(r"[1-9][0-9]{0,9}", form["token_id"]):
        raise image_studio.StudioError("令牌 ID 无效。")
    try:
        params = json.loads(form["parameters"])
        intent = json.loads(form["intent"])
    except ValueError:
        raise image_studio.StudioError("图片参数或编辑意图不是有效的 JSON。") from None
    body = image_studio.parse_request(
        {
            **form.to_dict(),
            "token_id": int(form["token_id"]),
            "parameters": params,
            "intent": intent,
        }
    )
    uploads = request.files
    images = uploads.getlist("image[]")
    masks = uploads.getlist("mask")
    if (
        set(uploads) - {"image[]", "mask"}
        or not 1 <= len(images) <= 4
        or len(masks) > 1
    ):
        raise image_studio.StudioError("请上传 1–4 张图像，遮罩最多一张。")
    inspected, total_pixels, sent_bytes = [], 0, 0
    try:
        for index, file in enumerate(images):
            data = file.read(image_transport.MAX_INPUT_BYTES + 1)
            image = image_transport.inspect_image(
                data, max_pixels=16_000_000 if index == 0 else 32_000_000
            )
            total_pixels += image["width"] * image["height"]
            sent_bytes += len(image["data"])
            if total_pixels > 32_000_000:
                raise image_studio.StudioError(
                    "输入图像的解码像素合计超过 32 MP。", 413
                )
            inspected.append(image)
        mask = None
        if masks:
            mask = image_transport.inspect_image(masks[0].read(4_000_001), mask=True)
            if (mask["width"], mask["height"]) != (
                inspected[0]["width"],
                inspected[0]["height"],
            ):
                raise image_studio.StudioError("遮罩尺寸必须与第一张实际输入图像一致。")
            with Image.open(BytesIO(mask["data"])) as decoded:
                if decoded.getchannel("A").getextrema()[0] == 255:
                    raise image_studio.StudioError("局部修改请先画出修改区域。")
            sent_bytes += len(mask["data"])
        if sent_bytes > image_transport.MAX_UPLOAD_BYTES - 65536:
            raise image_studio.StudioError(
                "规范化后的输入图片过大，请减少参考图。", 413
            )
        return body, inspected, mask
    finally:
        for file in images + masks:
            file.close()


def create_blueprint(host):
    """Create handlers without connecting to any external service.

    host.access() returns a verified RequestAccess. host.validate_write(header,
    content_type) must preserve the host's same-origin/session policy and raise
    or abort on rejection; it must not consume the body. service.configure(...)
    is called by the composition root before the blueprint starts serving.
    """
    blueprint = Blueprint("image_studio", __name__)

    @blueprint.get("/cockpit/image-studio", endpoint="page")
    @blueprint.get("/cockpit/image-studio/", endpoint="page")
    def page():
        return render_template("image_studio/index.html", site_name=host.site_name())

    @blueprint.get("/cockpit/api/image-studio/tokens", endpoint="tokens")
    def tokens():
        return jsonify(image_studio.list_tokens(host.access()))

    @blueprint.post("/cockpit/api/image-studio/models", endpoint="models")
    def models():
        studio_request(host)
        request.max_content_length = 1024
        return jsonify(
            image_studio.models(host.access(), None, request.get_json(silent=True))
        )

    @blueprint.post("/cockpit/api/image-studio/generations", endpoint="generations")
    def generations():
        studio_request(host)
        access = host.access()
        with image_studio.capacity(access.principal.as_user()):
            return jsonify(
                image_studio.run(access, None, request.get_json(silent=True))
            )

    @blueprint.post("/cockpit/api/image-studio/edits", endpoint="edits")
    def edits():
        from . import transport as image_transport

        studio_request(host, multipart=True)
        access = host.access()
        try:
            with image_studio.capacity(access.principal.as_user()):
                body, images, mask = studio_edit_body()
                return jsonify(
                    image_studio.run(access, None, body, images=images, mask=mask)
                )
        except image_transport.ImageError as exc:
            raise image_studio.StudioError(
                str(exc), exc.status, exc.code, exc.uncertain
            ) from None

    @blueprint.errorhandler(image_studio.StudioError)
    @blueprint.errorhandler(ReceiptError)
    def studio_error(exc):
        return jsonify(
            error=str(exc),
            code=exc.code,
            uncertain=exc.uncertain,
            details=getattr(exc, "details", {}),
        ), exc.status

    @blueprint.errorhandler(ValueError)
    def provider_error(exc):
        # The injected adapter supplies the existing safe TokenError shape.
        # No concrete integration exception or implementation is imported here.
        if hasattr(exc, "status") and hasattr(exc, "code"):
            response = jsonify(error=str(exc), code=exc.code)
            response.status_code = exc.status
            return apply_retry_after(response, exc)
        return jsonify(error=str(exc)), 400

    @blueprint.errorhandler(HTTPException)
    def http_error(exc):
        response = exc.get_response()
        response.set_data(
            blueprint_json(
                {
                    "error": exc.description,
                    "code": "IMAGE_TOO_LARGE"
                    if exc.code == 413
                    else "IMAGE_INVALID_REQUEST",
                    "uncertain": exc.code >= 500 and request.endpoint in PAID_ENDPOINTS,
                }
            )
        )
        response.content_type = "application/json"
        return response

    return blueprint


def blueprint_json(value):
    from flask import current_app

    return current_app.json.dumps(value)
