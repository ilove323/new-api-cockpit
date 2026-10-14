"""Owner-authorized synchronous Images requests, without jobs or stored results."""

from contextlib import contextmanager
import hashlib
import json
import logging
import os
from pathlib import Path
import re
from threading import Lock

import psycopg
from psycopg import sql

from . import balance, image_contracts, manual, personal_tokens, quota
from .locks import IMAGE_STUDIO_SLOT_NAMESPACE, IMAGE_STUDIO_USER_NAMESPACE


class StudioError(ValueError):
    def __init__(
        self,
        message,
        status=400,
        code="IMAGE_STUDIO_ERROR",
        uncertain=False,
        details=None,
    ):
        super().__init__(message)
        self.status = status
        self.code = code
        self.uncertain = bool(uncertain)
        self.details = _safe_details(details)


_guard = Lock()
_database_epoch = None
_capacity_failed = False


def process_role():
    return os.environ.get("COCKPIT_PROCESS_ROLE", "web")


def _monitor_connection():
    if not balance.configured():
        raise StudioError("生图工作站需要配置监控数据库以控制并发。", 503)
    return psycopg.connect(
        os.environ["MONITOR_DATABASE_URL"],
        connect_timeout=3,
        autocommit=True,
        options="-c statement_timeout=2000 -c application_name=cockpit-image-request",
    )


def _check_epoch(conn):
    global _database_epoch, _capacity_failed
    epoch = conn.execute("SELECT pg_postmaster_start_time()").fetchone()[0]
    with _guard:
        if _capacity_failed or (
            _database_epoch is not None and epoch != _database_epoch
        ):
            _capacity_failed = True
            raise StudioError("图片请求池需要重新启动，请稍后再试。", 503)
        _database_epoch = epoch


def initialize_capacity():
    """Read-only startup check: image workers never run schema migrations/timers."""
    global _capacity_failed
    try:
        with _monitor_connection() as conn:
            _check_epoch(conn)
        return True
    except (psycopg.Error, StudioError):
        with _guard:
            _capacity_failed = True
        return False


def health():
    try:
        with _monitor_connection() as conn:
            _check_epoch(conn)
        return {"ready": True, "role": "image"}
    except (psycopg.Error, StudioError):
        return {"ready": False, "role": "image"}


@contextmanager
def capacity(user):
    """Acquire before multipart parsing. Never wait or return a locked connection."""
    global _capacity_failed
    if process_role() != "image":
        raise StudioError("图片请求池尚未接入，请检查工作站的反向代理配置。", 503)
    if type(user.get("id")) is not int or not 1 <= user["id"] <= 2147483647:
        raise StudioError("当前账号无效，请重新登录。", 403)
    if _capacity_failed:
        raise StudioError("图片请求池暂不可用，请重新启动后再试。", 503)
    conn = None
    owned = False
    slot = None
    try:
        conn = _monitor_connection()
        _check_epoch(conn)
        owned = conn.execute(
            "SELECT pg_try_advisory_lock(%s,%s)",
            (IMAGE_STUDIO_USER_NAMESPACE, user["id"]),
        ).fetchone()[0]
        if not owned:
            raise StudioError(
                "当前账号已有图片请求正在执行。", 429, "IMAGE_STUDIO_BUSY"
            )
        for index in range(2):
            if conn.execute(
                "SELECT pg_try_advisory_lock(%s,%s)",
                (IMAGE_STUDIO_SLOT_NAMESPACE, index),
            ).fetchone()[0]:
                slot = index
                break
        if slot is None:
            raise StudioError(
                "图片工作站正在忙，请稍后再试。", 429, "IMAGE_STUDIO_BUSY"
            )
        yield
    except psycopg.Error:
        with _guard:
            _capacity_failed = True
        raise StudioError("图片请求池的并发控制连接不可用。", 503) from None
    finally:
        if conn is not None:
            try:
                if slot is not None:
                    conn.execute(
                        "SELECT pg_advisory_unlock(%s,%s)",
                        (IMAGE_STUDIO_SLOT_NAMESPACE, slot),
                    )
                if owned:
                    conn.execute(
                        "SELECT pg_advisory_unlock(%s,%s)",
                        (IMAGE_STUDIO_USER_NAMESPACE, user["id"]),
                    )
            except psycopg.Error:
                with _guard:
                    _capacity_failed = True
            finally:
                conn.close()


def list_tokens(user, credential):
    data = manual.list_tokens(user, credential)
    return {
        "tokens": [row for row in data["tokens"] if row["group"] == "GPT"],
        "token_manager_path": data["token_manager_path"],
    }


def _safe_details(details):
    """Keep only bounded, non-secret diagnostic identifiers from the transport."""
    if not isinstance(details, dict):
        return {}
    result = {}
    for key in ("category", "provider_code", "param", "upstream_request_id"):
        value = details.get(key)
        if (
            isinstance(value, str)
            and re.fullmatch(r"[A-Za-z0-9_.:/\[\]-]{1,128}", value)
            and not value.lower().startswith(("sk-", "bearer"))
        ):
            result[key] = value
    status = details.get("http_status")
    if type(status) is int and 100 <= status <= 599:
        result["http_status"] = status
    return result


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


def _source_columns(conn, table, names):
    """Resolve the visible table, and distinguish absent columns from no SELECT.

    information_schema hides unprivileged columns; the catalog existence check
    prevents treating a missing grant as an old-version default. Neither query
    reads source data or discovers credential columns.
    """
    params = (table, list(names))
    actual = {
        r["column_name"]
        for r in conn.execute(
            "SELECT attname AS column_name FROM pg_catalog.pg_attribute "
            "WHERE attrelid=to_regclass(%s) AND attname=ANY(%s) "
            "AND attnum>0 AND NOT attisdropped",
            params,
        ).fetchall()
    }
    readable = {
        r["column_name"]
        for r in conn.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE to_regclass(format('%%I.%%I',table_schema,table_name))=to_regclass(%s) "
            "AND column_name=ANY(%s) "
            "AND has_column_privilege(format('%%I.%%I',table_schema,table_name),column_name,'SELECT')",
            params,
        ).fetchall()
    }
    if actual - readable:
        raise StudioError(
            "源库角色缺少图片路由配置列的 SELECT 权限。", 503, "IMAGE_CONTRACT_SOURCE"
        )
    return actual


def _gateway_policy(conn):
    # rc.40 also merges affinity templates into ParamOverride. Read only paths
    # and whether a template exists, never its values/headers/credentials.
    row = conn.execute(
        "SELECT (SELECT value FROM options WHERE key='global.pass_through_request_enabled') AS pass_through,"
        "(SELECT value FROM options WHERE key='channel_affinity_setting.enabled') AS affinity_enabled,"
        "(SELECT jsonb_agg(jsonb_build_object('paths',r->'path_regex','override',"
        "COALESCE(r->'param_override_template','{}'::jsonb) NOT IN ('{}'::jsonb,'null'::jsonb))) "
        "FROM jsonb_array_elements(COALESCE((SELECT NULLIF(value,'')::jsonb FROM options "
        "WHERE key='channel_affinity_setting.rules'),'[]'::jsonb)) AS r) AS affinity_rules"
    ).fetchone()
    if any(
        row[k] not in (None, "true", "false")
        for k in ("pass_through", "affinity_enabled")
    ):
        raise StudioError("图片网关全局开关无效。", 503, "IMAGE_CONTRACT_SOURCE")
    rules = row["affinity_rules"] or []
    if not isinstance(rules, list) or len(rules) > 256:
        raise StudioError("图片网关亲和规则无效。", 503, "IMAGE_CONTRACT_SOURCE")
    safe = True
    for rule in rules:
        if not isinstance(rule, dict) or type(rule.get("override")) is not bool:
            raise StudioError("图片网关亲和规则无效。", 503, "IMAGE_CONTRACT_SOURCE")
        if not rule["override"]:
            continue
        paths = rule.get("paths")
        # Do not execute arbitrary administrator regex with Python backtracking.
        # Only literal non-image endpoint exclusions are provably safe here.
        if (
            not isinstance(paths, list)
            or not paths
            or any(
                not isinstance(path, str)
                or not re.fullmatch(r"\^?/v1/[a-z/]+\$?", path)
                or any(
                    path.strip("^$") in endpoint
                    for endpoint in ("/v1/images/edits", "/v1/images/generations")
                )
                for path in paths
            )
        ):
            safe = False
    # Absent DB rules use rc.40 defaults restricted to /responses and /messages.
    enabled = row["affinity_enabled"] != "false"
    return {
        "global_pass_through_request_enabled": row["pass_through"] == "true",
        "no_affinity_override": not enabled or safe,
        "affinity_revision": hashlib.sha256(
            json.dumps({"enabled": enabled, "rules": rules}, sort_keys=True).encode()
        ).hexdigest(),
    }


def _routes():
    """Read every GPT priority/retry path with explicit, quoted column access.

    Do not select c.*, to_jsonb(c), KEY, raw header/setting/proxy values. Only
    optional known columns found by metadata inspection enter this SQL. Absent
    rc.40 pass-through flags/options have the upstream's false default.
    """
    try:
        with quota.connect() as conn:
            gateway_policy = _gateway_policy(conn)
            columns = _source_columns(
                conn,
                "channels",
                {
                    "model_mapping",
                    "base_url",
                    "setting",
                    "settings",
                    "param_override",
                    "header_override",
                },
            )
            ability_columns = _source_columns(conn, "abilities", {"priority", "weight"})

            def column(name):
                return sql.Identifier("c", name) if name in columns else sql.SQL("NULL")

            def json_object(name):
                return sql.SQL(
                    "COALESCE(NULLIF({}::text,'')::jsonb,'{{}}'::jsonb)"
                ).format(column(name))

            projections = [
                sql.SQL("a.model"),
                sql.SQL("a.channel_id"),
                sql.SQL("c.type"),
            ]
            expressions = {
                "model_mapping": column("model_mapping"),
                "base_url_revision": sql.SQL("md5(COALESCE({}::text,''))").format(
                    column("base_url")
                ),
                "settings_revision": sql.SQL(
                    "md5(({} - 'task_plugin_key')::text || COALESCE({}::text,''))"
                ).format(json_object("setting"), column("settings")),
                "pass_through_body_enabled": sql.SQL(
                    "{}->'pass_through_body_enabled'"
                ).format(json_object("setting")),
                "no_param_override": sql.SQL("{}='{{}}'::jsonb").format(
                    json_object("param_override")
                ),
                "no_header_override": sql.SQL("{}='{{}}'::jsonb").format(
                    json_object("header_override")
                ),
                **{
                    name: sql.Identifier("a", name)
                    if name in ability_columns
                    else sql.SQL("NULL")
                    for name in ("priority", "weight")
                },
            }
            projections.extend(
                sql.SQL("{} AS {}").format(expr, sql.Identifier(name))
                for name, expr in expressions.items()
            )
            query = sql.SQL(
                "SELECT {} FROM abilities a JOIN channels c ON c.id=a.channel_id "
                'WHERE a."group"=%s AND a.model LIKE %s AND a.enabled=true AND c.status=1 '
                "ORDER BY a.model,a.channel_id LIMIT 10001"
            ).format(sql.SQL(",").join(projections))
            rows = conn.execute(query, ("GPT", "gpt-image-%")).fetchall()
    except psycopg.errors.InsufficientPrivilege:
        raise StudioError(
            "源库角色需要图片路由配置和指定 options 的只读权限。",
            503,
            "IMAGE_CONTRACT_SOURCE",
        ) from None
    except psycopg.Error:
        raise StudioError(
            "图片路由配置读取失败，请检查源库连接及 JSON 设置。",
            503,
            "IMAGE_CONTRACT_SOURCE",
        ) from None
    if len(rows) > 10000:
        raise StudioError("图片渠道目录过大，未返回不完整清单。", 503)
    for row in rows:
        row.update(gateway_policy)
        channel_flag = row.get("pass_through_body_enabled")
        if channel_flag is not None and type(channel_flag) is not bool:
            raise StudioError(
                "图片渠道透传设置无效。", 503, "IMAGE_CONTRACT_SOURCE"
            ) from None
        row["pass_through_body_enabled"] = channel_flag is True
    return rows


def _limited_models(row):
    enabled = row.get("model_limits_enabled")
    if type(enabled) is not bool:
        raise StudioError("令牌模型限制信息无效，请检查 New API 版本。", 502)
    if not enabled:
        return None
    value = row.get("model_limits") or ""
    if not isinstance(value, str) or len(value) > 100000:
        raise StudioError("令牌模型限制响应无效。", 502)
    return {item.strip() for item in value.split(",") if item.strip()}


def _contract(model, routes, config):
    try:
        return image_contracts.model_contract(
            model, [r for r in routes if r["model"] == model], config
        )
    except image_contracts.ContractError as exc:
        raise StudioError(str(exc), 503, "IMAGE_CONTRACT_CONFIG") from None


def _discover(user, credential, body):
    target = personal_tokens.token_id(body)
    row = personal_tokens.owned_row(
        user, credential, body, upstream=manual.upstream, group="GPT"
    )
    limits = _limited_models(row)
    config = _configuration()
    routes = _routes()
    # Non-inference endpoint checks the actual owner's KEY/group/IP/pricing.
    key = personal_tokens.key_for(
        user, credential, {"token_id": target}, upstream=manual.upstream, group="GPT"
    )
    reply = manual.upstream("/v1/models", key, user["id"], model_key=True)
    entries = reply.get("data")
    if (
        not isinstance(entries, list)
        or len(entries) > 10000
        or any(
            not isinstance(v, dict) or not isinstance(v.get("id"), str) for v in entries
        )
    ):
        raise StudioError("New API 图片模型目录响应无效。", 502)
    authorized = {v["id"] for v in entries}
    names = {
        route["model"]
        for route in routes
        if isinstance(route["model"], str)
        and route["model"].startswith("gpt-image-")
        and route["model"] in authorized
        and (limits is None or route["model"] in limits)
    }
    return {"models": [_contract(name, routes, config) for name in sorted(names)]}, key


def models(user, credential, body):
    catalogue, _ = _discover(user, credential, body)
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
    personal_tokens.token_id({"token_id": body["token_id"]})
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
    from . import image_transport
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
    from . import image_receipts, image_transport

    body = parse_request(body)
    catalogue, key = _discover(user, credential, {"token_id": body["token_id"]})
    selected = next((m for m in catalogue["models"] if m["id"] == body["model"]), None)
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
            str(exc), exc.status, exc.code, exc.uncertain, getattr(exc, "details", None)
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
            str(exc), exc.status, exc.code, exc.uncertain, getattr(exc, "details", None)
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
