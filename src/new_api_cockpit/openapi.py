"""OpenAPI contract and documentation safety metadata, without database access.

Routes are described explicitly: this is not a route scanner pretending to know
request/response shapes. The documentation client consumes the same operation
metadata for headers, confirmation and examples. Backend authorization remains
the authority; these annotations do not grant permissions.
"""

import json
import re
from functools import cache
from importlib.metadata import PackageNotFoundError, version


def obj(properties=None, required=(), **extra):
    return {
        "type": "object",
        "properties": properties or {},
        **({"required": list(required)} if required else {}),
        **extra,
    }


def array(items, **extra):
    return {"type": "array", "items": items, **extra}


def ref(name):
    return {"$ref": "#/components/schemas/" + name}


def text(description="", **extra):
    return {
        "type": "string",
        **({"description": description} if description else {}),
        **extra,
    }


def integer(minimum=0, **extra):
    return {"type": "integer", "minimum": minimum, **extra}


def choice(*values):
    return {"type": "string", "enum": list(values)}


BOOL = {"type": "boolean"}
MONEY = text("元；十进制字符串，API 不按表格的两位小数截断。", examples=["100.00"])
AMOUNT = {
    **MONEY,
    "description": "每人金额；0 < 金额 <= 1000000000，须精确换算为整数 quota。",
}
NULL_MONEY = {**MONEY, "type": ["string", "null"]}
NULL_COUNT = {"type": ["integer", "null"], "minimum": 0}
TIME = text("ISO 8601 时间，含时区。", format="date-time")
ID_LIST = array(integer(1), minItems=1, uniqueItems=True)
GROUP_LIST = array(text(maxLength=256), minItems=1, maxItems=1000, uniqueItems=True)
CHANNEL = choice("feishu_app", "dingtalk_webhook", "email")
SCOPE = {
    "name": "scope_id",
    "in": "query",
    "description": "上游账本 ID，省略为全部；从账本列表取得，不是用户组。",
    "schema": integer(1),
    "example": 1,
}


def query(name, schema, description="", required=False, example=None):
    result = {
        "name": name,
        "in": "query",
        "required": required,
        "schema": schema,
        "description": description,
    }
    if example is not None:
        result["example"] = example
    if schema.get("type") == "array":
        result.update(style="form", explode=True)
    return result


DATES = [
    query(
        "start", text(), "北京时间起始日期/分钟/秒，不带时区偏移。", True, "2026-10-01"
    ),
    query(
        "end",
        text(),
        "北京时间结束边界，包含该秒/当天；区间最多 367 天。",
        True,
        "2026-10-09",
    ),
]
DIAGNOSTICS = [
    query(
        "include_failures", choice("1"), "增加失败次数和错误码；默认不读取失败日志。"
    ),
    query(
        "details",
        choice("lazy"),
        "配置监控库时将公式保存为 15 分钟快照；省略返回完整详情。",
    ),
]
USER_MODEL = [
    query("user", text(), "精确匹配一个用户名。"),
    query("model", text(), "精确匹配一个模型名。"),
]


SCHEMAS = {
    "Error": obj(
        {"error": text(), "code": text(), "login_url": text(), "uncertain": BOOL},
        ("error",),
    ),
    "Scope": obj(
        {
            "id": integer(1),
            "kind": choice("all", "tag", "ungrouped"),
            "tag_value": text(),
        },
        ("id", "kind", "tag_value"),
    ),
    "Balance": obj(
        {
            "code": {"type": "integer", "const": 0},
            "data": obj(
                {
                    "site": text(),
                    "currency": text(examples=["CNY"]),
                    "scope": ref("Scope"),
                    **{
                        key: {
                            "type": "number",
                            "description": "金额，元；保留实际精度。",
                        }
                        for key in (
                            "total_quota",
                            "used_quota",
                            "remaining_quota",
                            "alert_threshold",
                        )
                    },
                    "usage_percent": {"type": ["number", "null"]},
                    "checked_at": TIME,
                },
                (
                    "site",
                    "total_quota",
                    "used_quota",
                    "remaining_quota",
                    "alert_threshold",
                    "checked_at",
                ),
            ),
        }
    ),
    "Alert": obj(
        {
            "scope": ref("Scope"),
            "has_alert": BOOL,
            "alert": {
                "type": ["object", "null"],
                "description": "当前报警；无报警为 null。",
            },
        }
    ),
    "UsageRow": obj(
        {
            "user_id": integer(),
            "username": text(),
            "display_name": text(),
            "model_name": text(),
            "tier_name": text(),
            "token_id": integer(),
            "token_name": text(),
            "request_count": integer(),
            **{
                key: NULL_COUNT
                for key in (
                    "total_tokens",
                    "input_tokens",
                    "output_tokens",
                    "cache_read_tokens",
                    "cache_write_tokens",
                    "original_cache_read_tokens",
                    "original_total_tokens",
                )
            },
            **{
                key: NULL_MONEY
                for key in (
                    "group_ratio",
                    "input_price",
                    "output_price",
                    "cache_price",
                    "write_price",
                )
            },
            "amount": {
                **MONEY,
                "description": "日志实际消费，不以当前价格覆盖历史金额。",
            },
            "failure_count": integer(),
            "failure_codes": {"type": "object", "additionalProperties": integer()},
            "cost_formula": obj(
                description="完整价格/配平公式；lazy 模式通过详情接口取得。"
            ),
            "report_id": text(format="uuid"),
            "row_id": integer(),
        }
    ),
    "Usage": obj(
        {
            "scope": ref("Scope"),
            "start": text(),
            "end": text(),
            "rows": array(ref("UsageRow")),
            "totals": obj(
                {"amount": MONEY, "total_tokens": NULL_COUNT},
                description="金额、各项 Token、请求数、用户/模型数、区间秒数及平均 TPM/RPM。",
            ),
            "rankings": obj(
                description="model_amount、user_tokens、user_amount 排名。"
            ),
            "updated_at": TIME,
        }
    ),
    "Settings": obj(
        {
            "enabled": BOOL,
            "budget": {
                **MONEY,
                "description": "非负，最多两位小数，最大 999999999999。",
            },
            "threshold": MONEY,
            "start_month": text("1970-01 至本月。", pattern=r"^\d{4}-\d{2}$"),
            "version": integer(1),
        },
        ("enabled", "budget", "threshold", "start_month", "version"),
    ),
    "BalanceStatus": obj(
        {
            "configured": BOOL,
            "scope": ref("Scope"),
            "settings": ref("Settings"),
            "state": {
                "type": ["object", "null"],
                "description": "最近检查的本月、已归档消费、余额、截止时间及错误。",
            },
            "months": array(obj({"month": text(), "amount": MONEY})),
            "alerts": array(obj()),
            "valid": BOOL,
            "stale": BOOL,
        },
        ("configured",),
    ),
    "HistoryRow": obj(
        {"month": text(), "before": MONEY, "after": MONEY}, ("month", "before", "after")
    ),
    "Notification": obj(
        {
            "channel": CHANNEL,
            "enabled": BOOL,
            "version": integer(1),
            "last_attempt_at": {"type": ["string", "null"]},
            "last_success_at": {"type": ["string", "null"]},
            "last_error": text(),
            "app_id": text(),
            "receive_id_type": choice("chat_id", "user_id"),
            "receive_id": text(),
            "secret_configured": BOOL,
            "webhook_configured": BOOL,
            "signing_enabled": BOOL,
            "signing_secret_configured": BOOL,
            "smtp_host": text(),
            "smtp_port": integer(1, maximum=65535),
            "smtp_security": choice("smtp", "starttls", "smtps"),
            "auth_enabled": BOOL,
            "username": text(),
            "password_configured": BOOL,
            "from_address": text(),
            "from_name": text(),
            "recipients": array(text()),
        },
        description="只返回所选渠道的非敏感字段和凭据配置标记，不回传凭据。",
    ),
    "User": obj(
        {
            "id": integer(1),
            "username": text(),
            "display_name": text(),
            "user_group": text(),
            "role": integer(),
            "status": {"type": "integer", "enum": [1, 2]},
            "remark": text(),
            "token_count": integer(),
            "quota": {"type": "integer"},
            "quota_yuan": MONEY,
            "used_quota_yuan": MONEY,
        }
    ),
    "UserDetail": obj(
        {
            "id": integer(1),
            "username": text(),
            "display_name": text(),
            "group": text(),
            "remark": text(),
            "status": {"type": "integer"},
            "role": integer(),
            "available_groups": array(text()),
        }
    ),
    "Key": obj(
        {
            "id": integer(1),
            "user_id": integer(1),
            "name": text(),
            "masked_key": text(),
            "token_group": text(),
            "group": text(),
            "status": {"type": "integer"},
            "effective_status": {"type": "integer"},
            "expired_time": {"type": "integer"},
            "remain_quota": {"type": "integer"},
            "remain_quota_yuan": MONEY,
            "used_quota_yuan": MONEY,
            "unlimited_quota": BOOL,
            "model_limits_enabled": BOOL,
            "model_limits": text(),
            "allow_ips": text(),
            "cross_group_retry": BOOL,
            "auto_groups": array(text()),
            "available_groups": array(text()),
        },
        description="列表使用 masked_key；详情不返回明文 KEY。",
    ),
    "KeyFilters": obj(
        {
            "user_statuses": array({"type": "integer", "enum": [1, 2]}, default=[1]),
            "token_statuses": array(
                {"type": "integer", "enum": [1, 2, 3, 4]}, default=[1, 2, 3, 4]
            ),
            "user_groups": {**array(text(maxLength=64)), "type": ["array", "null"]},
            "token_groups": {**array(text(maxLength=64)), "type": ["array", "null"]},
            "user_id": integer(1),
            "search": text(maxLength=200),
            "page": integer(1, default=1),
            "page_size": integer(1, maximum=100, default=50),
        },
        description="组精确匹配，搜索为字面关键字；按用户分页。数组省略/null 不限制，空数组不匹配。",
    ),
    "ActionResult": obj(
        {
            "ok": BOOL,
            "operation_id": text(format="uuid"),
            "result": {
                "type": ["object", "null"],
                "description": "通常 null；KEY reveal 返回 key 明文，仅明确调用才提供。",
            },
        }
    ),
    "QuotaRequest": obj(
        {"user_ids": ID_LIST, "mode": choice("add", "subtract"), "amount_yuan": AMOUNT},
        ("user_ids", "mode", "amount_yuan"),
    ),
    "QuotaPreview": obj(
        {
            "mode": choice("add", "subtract"),
            "amount_yuan": MONEY,
            "users": array(
                obj(
                    {
                        "id": integer(1),
                        "username": text(),
                        "before_yuan": MONEY,
                        "estimated_after_yuan": MONEY,
                    }
                )
            ),
        }
    ),
    "QuotaResult": obj(
        {
            "operation_id": text(format="uuid"),
            "mode": text(),
            "amount_yuan": MONEY,
            "results": array(
                obj(
                    {
                        "id": integer(1),
                        "username": text(),
                        "ok": BOOL,
                        "state": choice("success", "failed", "unknown"),
                        "message": text(),
                    }
                )
            ),
            "completed": BOOL,
            "remaining_user_ids": array(integer(1)),
        }
    ),
    "Schedule": obj(
        {
            "enabled": BOOL,
            "period": choice("daily", "weekly", "monthly"),
            "operation": choice("add", "subtract"),
            "amount_yuan": AMOUNT,
            "groups": GROUP_LIST,
            "version": integer(1),
        },
        ("enabled", "period", "operation", "amount_yuan", "groups"),
    ),
    "Record": obj(
        {
            "id": {"type": ["string", "integer"]},
            "source": choice("management", "schedule"),
            "occurred_at": TIME,
            "operator_id": integer(),
            "operator_name": text(),
            "action": text(),
            "state": text(),
            "parameters": obj(),
            "counts": obj(
                {key: integer() for key in ("total", "success", "failed", "uncertain")}
            ),
            "target_users": array(obj()),
            "target_user_count": integer(),
            "target_rule": obj(),
        }
    ),
    "Session": obj(
        {
            "id": integer(1),
            "username": text(),
            "role": integer(),
            "session_id": text(),
            "expires_at": integer(),
        },
        description="浏览器会话身份；不包含 JWT 或 PAT。",
    ),
}


def action_body(actions):
    return {
        "oneOf": [
            obj(
                {"action": {"type": "string", "const": name}, "changes": schema},
                ("action", "changes"),
                additionalProperties=False,
            )
            for name, schema in actions.items()
        ]
    }


USER_ACTION = action_body(
    {
        "create": obj(
            {
                "username": text(maxLength=20),
                "display_name": text(maxLength=20),
                "password": text(writeOnly=True, format="password"),
            },
            ("username", "password"),
        ),
        "edit": obj(
            {
                "username": text(maxLength=20),
                "display_name": text(maxLength=20),
                "group": text(),
                "remark": text(maxLength=255),
            },
            additionalProperties=False,
        ),
        "group": obj({"group": text()}, ("group",), additionalProperties=False),
        "password": obj(
            {
                "password": text(
                    "8～72 UTF-8 字节。", writeOnly=True, format="password"
                ),
                "password_confirm": text(writeOnly=True, format="password"),
            },
            ("password", "password_confirm"),
            additionalProperties=False,
        ),
        **{
            action: obj(additionalProperties=False)
            for action in ("enable", "disable", "delete")
        },
    }
)
KEY_FIELDS = {
    "name": text("最多 50 UTF-8 字节。"),
    "group": text(maxLength=64),
    "expired_time": {
        "type": "integer",
        "description": "Unix 秒，-1 永不过期。",
        "default": -1,
    },
    "model_limits_enabled": BOOL,
    "model_limits": text(maxLength=10000),
    "allow_ips": text(maxLength=10000),
    "cross_group_retry": BOOL,
    "auto_groups": array(text()),
}
KEY_ACTION = action_body(
    {
        "create": obj(
            {**KEY_FIELDS, "amount_yuan": MONEY, "unlimited_quota": BOOL},
            ("name",),
            additionalProperties=False,
        ),
        "edit": obj(KEY_FIELDS, additionalProperties=False),
        "group": obj(
            {"group": text(maxLength=64)}, ("group",), additionalProperties=False
        ),
        "quota": obj(
            {
                "mode": choice("set", "add", "subtract", "unlimited"),
                "amount_yuan": MONEY,
            },
            ("mode",),
            additionalProperties=False,
        ),
        **{
            action: obj(additionalProperties=False)
            for action in ("reveal", "enable", "disable", "delete")
        },
    }
)
NOTICE_FIELDS = {"channel": CHANNEL, "enabled": BOOL, "version": integer(1)}
NOTIFICATION_BODY = {
    "oneOf": [
        obj(
            {
                **NOTICE_FIELDS,
                "channel": {"const": "feishu_app", "type": "string"},
                "app_id": text(maxLength=512),
                "app_secret": text(
                    "留空保持原值；更改 App ID 后需重填。",
                    writeOnly=True,
                    format="password",
                ),
                "receive_id_type": choice("chat_id", "user_id"),
                "receive_id": text(maxLength=512),
            },
            ("channel", "enabled", "version"),
        ),
        obj(
            {
                **NOTICE_FIELDS,
                "channel": {"const": "dingtalk_webhook", "type": "string"},
                "webhook_url": text(writeOnly=True, maxLength=2048),
                "signing_enabled": BOOL,
                "signing_secret": text(
                    writeOnly=True, format="password", maxLength=512
                ),
            },
            ("channel", "enabled", "version", "signing_enabled"),
        ),
        obj(
            {
                **NOTICE_FIELDS,
                "channel": {"const": "email", "type": "string"},
                "smtp_host": text(maxLength=253),
                "smtp_port": integer(1, maximum=65535),
                "smtp_security": choice("smtp", "starttls", "smtps"),
                "auth_enabled": BOOL,
                "username": text(maxLength=512),
                "password": text(
                    "留空保留；改变连接目标或用户名后需重新填写。",
                    writeOnly=True,
                    format="password",
                    maxLength=1024,
                ),
                "from_address": text(maxLength=254),
                "from_name": text(maxLength=128),
                "recipients": array(text(maxLength=254), maxItems=100),
            },
            (
                "channel",
                "enabled",
                "version",
                "smtp_port",
                "smtp_security",
                "auth_enabled",
            ),
        ),
    ]
}


def operation(
    method,
    path,
    summary,
    tag,
    response,
    *,
    description="",
    params=(),
    body=None,
    example=None,
    header=None,
    effect="read",
    pat=False,
    status=200,
    testable=True,
):
    parameters = list(params)
    for name in re.findall(r"\{([^}]+)\}", path):
        schema = (
            text(format="uuid")
            if name == "operation_id"
            else choice("management", "schedule")
            if name == "source"
            else text("management 使用 UUID；schedule 使用正整数 ID。")
            if name == "record_id"
            else integer(
                0 if name in {"user_id", "key_id"} and path.endswith("/action") else 1
            )
        )
        parameters.append(
            {
                "name": name,
                "in": "path",
                "required": True,
                "schema": schema,
                "example": "management"
                if name == "source"
                else "00000000-0000-4000-8000-000000000001"
                if name in {"operation_id", "record_id"}
                else 23,
            }
        )
    confirm_headers = {header[0]: header[1]} if header else {}
    for name, value in confirm_headers.items():
        parameters.append(
            {
                "name": name,
                "in": "header",
                "required": True,
                "schema": {"type": "string", "const": value},
                "example": value,
                "description": "调试器自动填写；不表示幂等或已确认实际操作。",
            }
        )
    responses = {
        str(status): {
            "description": "成功；批量接口还须核对逐项结果。",
            "content": {"application/json": {"schema": response}},
        }
    }
    for code, meaning in (
        (400, "参数不合法"),
        (401, "凭据缺失或无效"),
        (403, "权限、来源或确认头不符合要求"),
        (409, "版本/预览/执行状态冲突，先重新核对"),
        (502, "上游结果不明确，可能已生效，不重发"),
        (503, "数据库或上游服务不可用"),
    ):
        responses[str(code)] = {
            "description": meaning,
            "content": {"application/json": {"schema": ref("Error")}},
        }
    result = {
        "operationId": re.sub(
            r"[^a-zA-Z0-9]+", "_", method + "_" + path.removeprefix("/cockpit/api/")
        ),
        "summary": summary,
        "description": description,
        "tags": [tag],
        "parameters": parameters,
        "responses": responses,
        "x-cockpit-effect": effect,
        "x-cockpit-testable": testable,
        "x-cockpit-pat-only": pat,
        "x-cockpit-confirm-headers": confirm_headers,
    }
    if pat:
        result["security"] = [{"adminPAT": []}]
    if body is not None:
        media = {"schema": body}
        if example is not None:
            media["example"] = example
        result["requestBody"] = {
            "required": True,
            "content": {"application/json": media},
        }
    return path, method.lower(), result


def document():
    """Build a fresh, credential-free contract; no source/monitoring queries."""
    stat, manage, quota = (
        ("X-Statistics-Request", "1"),
        ("X-Management-Action", "confirm"),
        ("X-Quota-Action", "schedule"),
    )
    quota_example = {"user_ids": [23], "mode": "add", "amount_yuan": "100.00"}
    settings_example = {
        "enabled": True,
        "budget": "10000.00",
        "threshold": "2000.00",
        "start_month": "2026-01",
        "version": 1,
    }
    ops = [
        operation(
            "GET",
            "/cockpit/api/openapi.json",
            "下载 OpenAPI 描述",
            "接口描述",
            obj(
                {"openapi": text(), "info": obj(), "paths": obj(), "components": obj()}
            ),
        ),
        operation(
            "GET",
            "/cockpit/api/statistics/balance",
            "实时查询余额",
            "余额与报警",
            ref("Balance"),
            params=[SCOPE],
            pat=True,
            description="每次实时计算，不发送报警；这是上游账本预算，不是个人用户或 KEY 额度。总预算为零时 usage_percent 为 null。",
        ),
        operation(
            "GET",
            "/cockpit/api/statistics/alert",
            "检查并发送余额报警",
            "余额与报警",
            ref("Alert"),
            params=[SCOPE],
            pat=True,
            effect="notify",
            description="虽然是 GET，也会执行检查、保存报警并向所有启用渠道发送通知；重复调用可能重复发送。",
        ),
        operation(
            "GET",
            "/cockpit/api/statistics/scopes",
            "列出上游账本",
            "用量统计",
            obj({"rows": array(ref("Scope"))}),
            description="标签来自渠道当前分组，包含全部和未分组；隐藏账本的历史不删除。",
        ),
    ]
    for suffix, title, schema, filters, diagnostics in (
        ("", "查询用户模型用量", ref("Usage"), USER_MODEL, DIAGNOSTICS),
        ("/by-token", "按令牌拆分用量", ref("Usage"), USER_MODEL, DIAGNOSTICS),
        (
            "/tokens",
            "列出区间令牌选项",
            obj(
                {
                    "rows": array(obj({"token_id": integer(), "token_name": text()})),
                    "scope": ref("Scope"),
                    "start": text(),
                    "end": text(),
                }
            ),
            [],
            DIAGNOSTICS[:1],
        ),
        (
            "/groups",
            "列出区间日志分组选项",
            obj(
                {
                    "rows": array(obj({"group_name": text()})),
                    "scope": ref("Scope"),
                    "start": text(),
                    "end": text(),
                }
            ),
            [],
            DIAGNOSTICS[:1],
        ),
        (
            "/by-selection",
            "按令牌或日志分组筛选",
            ref("Usage"),
            [
                query(
                    "token_id",
                    array(integer(), maxItems=500),
                    "重复参数；0 为历史无有效令牌 ID。",
                ),
                query(
                    "group",
                    array(text(maxLength=128), maxItems=500),
                    "重复参数；空值匹配空分组。",
                ),
                query("by_token", choice("1"), "过滤后按令牌拆行。"),
            ],
            DIAGNOSTICS,
        ),
    ):
        ops.append(
            operation(
                "GET",
                "/cockpit/api/statistics/usage" + suffix,
                title,
                "用量统计",
                schema,
                params=[*DATES, SCOPE, *filters, *diagnostics],
                description="区间最多 367 天，金额取实际日志。无分页；by-selection 至少选择 token_id/group 一种，两种同时提供取交集。by-token/by-selection 不含 totals/rankings。",
            )
        )
    ops += [
        operation(
            "POST",
            "/cockpit/api/statistics/usage/details",
            "读取查询快照的公式详情",
            "用量统计",
            obj({"rows": array(obj({"row_id": integer(), "detail": obj()}))}),
            params=[SCOPE],
            body=obj(
                {
                    "report_id": text(format="uuid"),
                    "row_ids": array(
                        integer(maximum=2147483647), minItems=1, maxItems=500
                    ),
                },
                ("report_id", "row_ids"),
            ),
            example={
                "report_id": "00000000-0000-4000-8000-000000000001",
                "row_ids": [0],
            },
            header=stat,
            description="来自本账号/账本的不可变快照，不重新查日志；15 分钟过期后返回 410。",
        ),
        operation(
            "GET",
            "/cockpit/api/statistics/export",
            "下载消费 Excel",
            "用量统计",
            text(format="binary"),
            params=[*DATES, SCOPE, *USER_MODEL],
            description="返回 XLSX 附件；独立实时查询。不支持令牌/分组筛选、分令牌或失败次数导出。",
        ),
        operation(
            "GET",
            "/cockpit/api/statistics/balance/status",
            "读取预算、归档和余额状态",
            "余额与报警",
            ref("BalanceStatus"),
            params=[
                SCOPE,
                query("live", choice("1"), "实时计算而不是最近检查状态，不发通知。"),
            ],
            description="未配置监控库时只有 configured=false；state 可为 null。无效或过旧状态不能当作真实零余额。",
        ),
        operation(
            "GET",
            "/cockpit/api/statistics/balance/usage-channels",
            "读取当前账本渠道库存",
            "余额与报警",
            obj(
                {
                    "rows": array(
                        obj(
                            {
                                "channel_id": integer(),
                                "channel_name": text(),
                                "channel_status": {"type": ["integer", "null"]},
                                "deleted": BOOL,
                            }
                        )
                    ),
                    "scope": ref("Scope"),
                }
            ),
            params=[SCOPE],
        ),
        operation(
            "POST",
            "/cockpit/api/statistics/balance/check",
            "手工检查余额并按配置报警",
            "余额与报警",
            ref("BalanceStatus"),
            params=[SCOPE],
            body=obj(),
            example={},
            header=stat,
            effect="notify",
        ),
        operation(
            "PUT",
            "/cockpit/api/statistics/balance/settings",
            "保存账本预算设置",
            "余额与报警",
            obj({"saved": BOOL}),
            params=[SCOPE],
            body=ref("Settings"),
            example=settings_example,
            header=stat,
            effect="write",
            description="先读取实际 version；可补齐缺失归档。关闭监控会清除本账本报警，不删除费用。",
        ),
        operation(
            "POST",
            "/cockpit/api/statistics/balance/recalculate-history/preview",
            "预览历史归档重建",
            "余额与报警",
            obj(
                {
                    "rows": array(ref("HistoryRow")),
                    "version": integer(1),
                    "scope_id": integer(1),
                    "rebuild_scope": text(),
                    "affects_all_scopes": BOOL,
                    "warning": text(),
                }
            ),
            params=[SCOPE],
            body=ref("Settings"),
            example=settings_example,
            header=stat,
            effect="preview",
            description="只预览，不覆盖归档；缺日志/低于归档基线返回 409。重建将影响所有账本，必须逐月核对。",
        ),
        operation(
            "POST",
            "/cockpit/api/statistics/balance/recalculate-history",
            "确认覆盖历史归档",
            "余额与报警",
            obj({"recalculated": BOOL, "version": integer(1), "months": integer()}),
            params=[SCOPE],
            body=obj(
                {"settings": ref("Settings"), "preview": array(ref("HistoryRow"))},
                ("settings", "preview"),
            ),
            example={
                "settings": settings_example,
                "preview": [
                    {"month": "2026-01", "before": "120.00", "after": "125.00"}
                ],
            },
            header=stat,
            effect="write",
            description="必须使用预览返回的完整 rows 数组。重建所有渠道费用并影响所有账本，会保存设置；版本/数据变化或数据缺失时保留原归档。",
        ),
        operation(
            "GET",
            "/cockpit/api/statistics/balance/channel",
            "读取指定通知渠道配置",
            "通知渠道",
            ref("Notification"),
            params=[
                query("channel", CHANNEL, "省略默认为 feishu_app。", example="email")
            ],
        ),
        operation(
            "PUT",
            "/cockpit/api/statistics/balance/channel",
            "保存指定通知渠道",
            "通知渠道",
            ref("Notification"),
            body=NOTIFICATION_BODY,
            example={
                "channel": "email",
                "enabled": False,
                "version": 1,
                "smtp_host": "smtp.example.test",
                "smtp_port": 587,
                "smtp_security": "starttls",
                "auth_enabled": False,
                "from_address": "alerts@example.test",
                "recipients": ["ops@example.test"],
            },
            header=stat,
            effect="write",
            description="只修改所选渠道，各渠道独立启停。版本须来自刚读取的配置；凭据留空保留。修改邮件连接目标或用户名后需重填密码。",
        ),
        operation(
            "POST",
            "/cockpit/api/statistics/balance/channel/test",
            "发送一次真实测试通知",
            "通知渠道",
            obj({"sent": BOOL}),
            body=obj(
                {"channel": CHANNEL, "version": integer(1)}, ("channel", "version")
            ),
            example={"channel": "email", "version": 1},
            header=stat,
            effect="notify",
            description="使用指定渠道已保存配置，即使渠道关闭也会发送；超时可能已投递，不盲目重发。",
        ),
        operation(
            "GET",
            "/cockpit/api/users",
            "列出用户与配额",
            "用户管理",
            obj({"rows": array(ref("User"))}),
            description="所有未删除用户，无分页或服务端状态筛选；status=1 启用、2 禁用。返回不含密码/PAT/KEY。",
        ),
        operation(
            "GET",
            "/cockpit/api/users/{user_id}",
            "读取用户资料",
            "用户管理",
            ref("UserDetail"),
            effect="pat",
            description="通过官方接口读取；缺失管理员 PAT 时可能补建并写操作记录。",
        ),
        operation(
            "GET",
            "/cockpit/api/users/{user_id}/groups",
            "读取用户可切换分组",
            "用户管理",
            obj(
                {
                    "id": integer(1),
                    "username": text(),
                    "group": text(),
                    "available_groups": array(text()),
                    "persistence": BOOL,
                }
            ),
            description="只读源库，不补建 PAT；组来自全局 GroupRatio。",
        ),
        operation(
            "POST",
            "/cockpit/api/users/{user_id}/action",
            "创建或修改用户",
            "用户管理",
            ref("ActionResult"),
            body=USER_ACTION,
            example={"action": "group", "changes": {"group": "default"}},
            header=manage,
            effect="write",
            description="create 使用路径 ID 0，仅超级管理员可创建；密码需 8～72 UTF-8 字节并二次一致。权限限制由后端核对。分组改变不覆盖固定分组 KEY。",
        ),
        operation(
            "POST",
            "/cockpit/api/users/quota/preview",
            "预览每人额度增减",
            "用户额度",
            ref("QuotaPreview"),
            body=ref("QuotaRequest"),
            example=quota_example,
            header=("X-Quota-Action", "preview"),
            effect="preview",
            description="不冻结余额、不发起修改，也不进入操作记录；执行前核对用户及每人金额。减少后允许负余额。",
        ),
        operation(
            "POST",
            "/cockpit/api/users/quota/apply",
            "执行每人额度增减",
            "用户额度",
            ref("QuotaResult"),
            body=ref("QuotaRequest"),
            example=quota_example,
            header=("X-Quota-Action", "confirm"),
            effect="write",
            description="通过官方原子增减接口；每组最多 5 人并发，失败/不明确停止后续组。无需提交预览响应，无幂等键，重复执行会重复增减。",
        ),
        operation(
            "GET",
            "/cockpit/api/users/schedules",
            "读取定时规则与可选用户组",
            "定时额度",
            obj(
                {
                    "configured": BOOL,
                    "rows": array(
                        obj(
                            {
                                "id": integer(1),
                                **SCHEMAS["Schedule"]["properties"],
                                "next_run_at": TIME,
                                "can_edit": BOOL,
                                "executor_user_id": integer(1),
                                "executor_username": text(),
                            }
                        )
                    ),
                    "groups": array(text()),
                    "timezone": text(),
                    "current_user_id": integer(1),
                    "current_username": text(),
                }
            ),
        ),
        operation(
            "POST",
            "/cockpit/api/users/schedules",
            "新增定时额度规则",
            "定时额度",
            obj({"id": integer(1), "next_run_at": TIME}),
            body=ref("Schedule"),
            example={
                "enabled": True,
                "period": "monthly",
                "operation": "add",
                "amount_yuan": "100.00",
                "groups": ["default"],
            },
            header=quota,
            effect="write",
            status=201,
            description="保存后从下周期执行，不立即发额度；每日/周一/月初北京时间 00:00，执行时读取组内启用用户。",
        ),
        operation(
            "PUT",
            "/cockpit/api/users/schedules/{rule_id}",
            "编辑定时规则",
            "定时额度",
            obj({"id": integer(1), "next_run_at": TIME}),
            body={"allOf": [ref("Schedule"), {"required": ["version"]}]},
            example={
                "enabled": True,
                "period": "monthly",
                "operation": "add",
                "amount_yuan": "100.00",
                "groups": ["default"],
                "version": 1,
            },
            header=quota,
            effect="write",
        ),
        operation(
            "PATCH",
            "/cockpit/api/users/schedules/{rule_id}",
            "启停定时规则",
            "定时额度",
            obj({"id": integer(1), "next_run_at": TIME}),
            body=obj({"enabled": BOOL, "version": integer(1)}, ("enabled", "version")),
            example={"enabled": False, "version": 1},
            header=quota,
            effect="write",
        ),
        operation(
            "DELETE",
            "/cockpit/api/users/schedules/{rule_id}",
            "删除定时规则",
            "定时额度",
            obj({"deleted": BOOL}),
            body=obj({"version": integer(1)}, ("version",)),
            example={"version": 1},
            header=quota,
            effect="write",
            description="软删除，保留执行历史；DELETE 也必须提供 JSON 版本号。",
        ),
        operation(
            "GET",
            "/cockpit/api/keys/options",
            "读取全局 KEY 分组选项",
            "令牌管理",
            obj(
                {
                    "groups": array(text()),
                    "operator": obj(
                        {"id": integer(1), "username": text(), "role": integer()}
                    ),
                    "persistence": BOOL,
                }
            ),
            description="全局组不能当作任意 KEY 的可用目标组；应先读目标 KEY 的 groups。",
        ),
        operation(
            "POST",
            "/cockpit/api/keys/query/grouped",
            "按用户查询 KEY",
            "令牌管理",
            obj(
                {
                    "rows": array(
                        {"allOf": [ref("User"), obj({"tokens": array(ref("Key"))})]}
                    ),
                    "total": integer(),
                    "total_keys": integer(),
                    "page": integer(1),
                    "page_size": integer(1),
                }
            ),
            body=ref("KeyFilters"),
            example={"user_statuses": [1], "page": 1, "page_size": 50},
            header=manage,
            description="只查询，不补建 PAT。分页单位为用户，同用户下 KEY 不拆页；不返回完整密钥。",
        ),
        operation(
            "GET",
            "/cockpit/api/keys/{key_id}",
            "读取 KEY 配置",
            "令牌管理",
            ref("Key"),
            effect="pat",
            description="以所属用户身份调用官方接口，缺失用户 PAT 时会补建；详情不含明文 KEY。",
        ),
        operation(
            "GET",
            "/cockpit/api/keys/{key_id}/groups",
            "读取 KEY 可切换分组",
            "令牌管理",
            obj(
                {
                    "id": integer(1),
                    "group": text(),
                    "user_status": integer(),
                    "available_groups": array(text()),
                }
            ),
            description="不补 PAT；空字符串表示跟随用户组，auto 表示自动分组。其他组须在所属用户允许范围。",
        ),
        operation(
            "POST",
            "/cockpit/api/keys/{key_id}/action",
            "创建、修改或查看 KEY",
            "令牌管理",
            ref("ActionResult"),
            body=KEY_ACTION,
            example={"action": "group", "changes": {"group": "default"}},
            header=manage,
            effect="write",
            description="create 时路径 ID 是所属用户 ID，其余是 KEY ID。reveal 会返回明文密钥。改组/编辑/额度更新前先暂停 KEY 并发调用；官方覆盖更新不与推理扣费原子执行。",
        ),
        operation(
            "POST",
            "/cockpit/api/keys/groups/preview",
            "预览并冻结批量改组名单",
            "令牌管理",
            obj(
                {
                    "operation_id": text(format="uuid"),
                    "count": integer(),
                    "rows": array(
                        obj(
                            {
                                "id": integer(1),
                                "user_id": integer(1),
                                "username": text(),
                                "before_group": text(),
                                "after_group": text(),
                            }
                        )
                    ),
                    "preview_truncated": BOOL,
                }
            ),
            body={
                "oneOf": [
                    obj(
                        {
                            "target_group": text(maxLength=64),
                            "token_ids": ID_LIST,
                            "all_filtered": {"const": False, "type": "boolean"},
                        },
                        ("target_group", "token_ids"),
                    ),
                    obj(
                        {
                            "target_group": text(maxLength=64),
                            "all_filtered": {"const": True, "type": "boolean"},
                            "filters": ref("KeyFilters"),
                        },
                        ("target_group", "all_filtered", "filters"),
                    ),
                ]
            },
            example={
                "target_group": "default",
                "token_ids": [10],
                "all_filtered": False,
            },
            header=manage,
            effect="preview",
            description="有效期 15 分钟，冻结完整名单；只展示前 100 项。不改 KEY、不补 PAT、不进操作记录。",
        ),
        operation(
            "POST",
            "/cockpit/api/keys/operations/{operation_id}/apply",
            "执行下一组 KEY 改组",
            "令牌管理",
            obj(
                {
                    "results": array(
                        obj({"id": integer(1), "state": text(), "message": text()})
                    ),
                    "done": BOOL,
                    "remaining": integer(),
                    "state": text(),
                }
            ),
            body=obj(),
            example={},
            header=manage,
            effect="write",
            description="每次最多 5 个。只在收到完整响应、全部成功且 done=false 时继续下一组；不是重试上一组，失败/断连后先查记录。",
        ),
        operation(
            "GET",
            "/cockpit/api/operations",
            "查询已发起的操作记录",
            "操作记录",
            obj(
                {
                    "rows": array(ref("Record")),
                    "next_before": {"type": ["string", "null"]},
                }
            ),
            params=[
                query("kind", choice("user", "token", "quota", "schedule")),
                query("before", text(), "原样使用上一页 next_before，不自行构造。"),
            ],
            description="每页 50 项；普通管理员仅本人，超级管理员可看全部。未发起/只预览不记录。",
        ),
        operation(
            "GET",
            "/cockpit/api/operations/{source}/{record_id}",
            "查询逐项执行结果",
            "操作记录",
            obj(
                {
                    "rows": array(
                        obj(
                            {
                                "target_type": text(),
                                "target_id": integer(),
                                "user_id": integer(),
                                "target_user": obj(),
                                "target_rule": obj(),
                                "before_data": obj(),
                                "after_data": obj(),
                                "state": text(),
                                "message": text(),
                            }
                        )
                    ),
                    "next_after": {"type": ["integer", "null"]},
                }
            ),
            params=[
                query(
                    "after",
                    {"type": "integer", "minimum": -1},
                    "上一页 next_after，默认 -1。",
                )
            ],
            description="每页最多 100 项；不再次执行原操作，不返回密码/PAT/明文 KEY。",
        ),
    ]
    for method, title, body in (
        ("GET", "读取当前浏览器会话", None),
        (
            "POST",
            "承接 New API 浏览器 JWT",
            obj({"access_token": text(writeOnly=True)}, ("access_token",)),
        ),
        ("DELETE", "清除 Cockpit 会话 Cookie", obj()),
    ):
        entry = operation(
            method,
            "/cockpit/api/auth/session",
            title,
            "浏览器会话",
            ref("Session") if method != "DELETE" else obj({"ok": BOOL}),
            body=body,
            header=("X-Cockpit-Auth", "1") if body is not None else None,
            effect="session",
            testable=False,
            description="仅用于网页登录，不接受 PAT 换 Cookie。文档页不执行会话变更；请使用正常登录/退出入口。",
        )
        entry[2]["security"] = [{"browserSession": []}]
        ops.append(entry)
    quality_params = [
        query(
            "start",
            text(),
            "北京时间起点；省略默认近 24 小时。",
            example="2026-10-09T00:00:00",
        ),
        query(
            "end",
            text(),
            "北京时间终点，包含该秒；单次最多 31 天。",
            example="2026-10-09T23:59:59",
        ),
        query(
            "mode",
            choice("channel", "upstream"),
            "渠道明细或按渠道当前标签汇总；默认 channel。",
        ),
        query("stream", choice("all", "stream", "nonstream"), "请求方式，默认 all。"),
        query(
            "channel_status",
            choice("enabled", "all"),
            "默认 enabled，只统计 New API 当前启用且未删除的渠道；all 可查询包含禁用、已删除和未知渠道的历史。",
        ),
        query(
            "latency_scope",
            choice("direct", "all"),
            "总耗时和平均 token/s 的样本范围，默认只采用确认无重试的成功调用；旧日志重试不计算速度。不改变请求/费用总数。",
        ),
        query(
            "tag",
            text(maxLength=256),
            "按渠道当前标签限定上游；tag= 表示未分组。省略时展示全部上游，与余额账本无关。",
        ),
        *[
            query(name, array(text(maxLength=256), maxItems=100), meaning)
            for name, meaning in (
                ("model", "精确匹配模型，可重复。"),
                ("user", "精确匹配用户名，可重复。"),
            )
        ],
        *[
            query(name, array(integer(), maxItems=100), "非负 ID，可重复。")
            for name in ("channel_id", "token_id")
        ],
    ]
    nullable_metric = {"type": ["number", "null"]}
    quality_row = obj(
        {
            "model_name": {"type": ["string", "null"]},
            "target": {"type": ["string", "null"]},
            "channel_id": integer(),
            "channel_name": text(),
            "tag_value": text(),
            "channel_status": NULL_COUNT,
            "is_deleted": BOOL,
            **{
                key: integer()
                for key in (
                    "request_count",
                    "success_count",
                    "failure_count",
                    "ignored_count",
                    "unknown_count",
                    "legacy_count",
                    "retry_or_unknown_count",
                    "unique_requests",
                    "duration_samples",
                    "frt_samples",
                    "tps_samples",
                    "tps_output_tokens",
                    "billed_records",
                    "price_bucket_count",
                )
            },
            **{
                key: nullable_metric
                for key in (
                    "success_rate",
                    "outcome_coverage",
                    "duration_coverage",
                    "duration_total_ms",
                    "frt_coverage",
                    "tps_coverage",
                    "tps_duration_ms",
                    "duration_p50_ms",
                    "duration_p95_ms",
                    "frt_p50_ms",
                    "frt_p95_ms",
                )
            },
            "avg_tokens_per_second": {
                **nullable_metric,
                "description": "tps_output_tokens * 1000 / tps_duration_ms，包含首字等待，不是纯生成速度；无有效成功样本时为 null。汇总重新累计后计算，不平均各行速度。",
            },
            "avg_duration_ms": {
                **nullable_metric,
                "description": "成功调用的有效总耗时之和 / duration_samples；样本范围与总耗时 P50/P95 一致。无有效样本为 null，汇总不平均各行均值。",
            },
            "sample_insufficient": BOOL,
            "sample_request_ids": array(text()),
            "amount": MONEY,
            "failure_codes": obj(additionalProperties=integer()),
            "pricing_buckets": array(
                obj(
                    {
                        "group": text(),
                        "group_ratio": {"type": ["string", "null"]},
                        "tier": text(),
                        "request_count": integer(),
                        "amount": MONEY,
                        "prices": {
                            "type": ["object", "null"],
                            "properties": {
                                key: NULL_MONEY
                                for key in (
                                    "input_price",
                                    "output_price",
                                    "cache_price",
                                    "write_price",
                                )
                            },
                        },
                    }
                )
            ),
        }
    )
    quality_schemas = {"QualityRow": quality_row}
    quality_schemas["Quality"] = obj(
        {
            "start": text(),
            "end": text(),
            "start_ts": integer(),
            "end_ts": integer(),
            "mode": choice("channel", "upstream"),
            "latency_scope": choice("direct", "all"),
            "channel_status": choice("enabled", "all"),
            "rows": array(ref("QualityRow")),
            "totals": ref("QualityRow"),
            "top_models": array(
                obj(
                    {
                        "model_name": {"type": ["string", "null"]},
                        "request_count": integer(),
                        "success_count": integer(),
                        "failure_count": integer(),
                        "success_rate": nullable_metric,
                    }
                ),
                maxItems=6,
                description="按当前时间、筛选和渠道范围合并同模型的全部渠道调用，以请求次数降序列出前六个模型；成功率按成功/失败合计重新计算。",
            ),
            "trends": array(obj({"hour": TIME, **quality_row["properties"]})),
            "source_records": integer(),
            "unsupported_records": integer(),
            "warnings": array(text()),
            "updated_at": TIME,
            "options": obj(
                {
                    "models": array(text()),
                    "users": array(text()),
                    "tokens": array(obj({"id": integer(), "name": text()})),
                    "channels": array(
                        obj(
                            {
                                "channel_id": integer(),
                                "channel_name": text(),
                                "channel_status": NULL_COUNT,
                                "tag_value": text(),
                                "is_deleted": BOOL,
                            }
                        )
                    ),
                }
            ),
        }
    )
    for suffix, title in (
        ("summary", "查询渠道质量"),
        ("trends", "查询质量与小时趋势"),
    ):
        ops.append(
            operation(
                "GET",
                "/cockpit/api/quality/" + suffix,
                title,
                "渠道质量",
                ref("Quality"),
                params=quality_params,
                description="只读文本生成日志，默认统计当前启用渠道的全部上游，不按余额账本、计费分组或档位筛选，不应用余额渠道屏蔽规则。不接受 scope_id/group/tier 参数。按 request_id/attempt 去重路由记录；明确错误（含 400 等业务拒绝）计入失败，客户端取消和未知结果不计入成功率分母。关联同请求、同渠道、同尝试的错误日志读取 HTTP 错误码；没有错误码的流式失败明确标注，不推测成 500。页面不展示排除/未知列，API 保留相应计数。缺失指标 null；非完整可用率。全局 P50/P95 从原始有效样本重算，费用取历史 quota。summary 不加载历史价格组合；trends 才加载。超过 1000 个模型/渠道组合或异常 JSON 回退上限时返回 503，不返回部分合计。",
            )
        )
    intelligence_models = obj(
        {
            "rows": array(
                obj(
                    {
                        "model": text(),
                        "group": text(),
                        "available_groups": array(text()),
                        "channel_id": integer(),
                        "channel_type": integer(),
                        "endpoint": choice("openai", "anthropic"),
                    }
                )
            ),
            "key_name": text(),
            "prompt": text(),
        }
    )
    ops.extend(
        [
            operation(
                "GET",
                "/cockpit/api/intelligence/models",
                "查询可测试模型与自动分组",
                "智力测试",
                intelligence_models,
                description="使用真实管理员身份查询官方 /api/models/ 管理目录（包含渠道模型），不依赖公开价格页开关。完整读取最多 2000 条目录记录，并与当前启用渠道、所属用户的分组权限取交集，只列支持文本生成的模型。优先用户组，否则按组名排序；组内按渠道优先级降序、ID 升序选取启用渠道。channel_id/channel_type 表示该渠道，类型 14 用 anthropic（Messages），其他用 openai（Chat Completions），不按模型名称或目录端点顺序判断。不创建 PAT/KEY，不保存测试结果。",
            ),
            operation(
                "POST",
                "/cockpit/api/intelligence/test",
                "鹈鹕骑车测试（真实计费）",
                "智力测试",
                obj(
                    {
                        "model": text(),
                        "group": text(),
                        "endpoint": choice("openai", "anthropic"),
                        "channel_id": integer(),
                        "channel_type": integer(),
                        "key_name": text(),
                        "elapsed_ms": integer(),
                        "html": {"type": ["string", "null"]},
                        "source": text(),
                        "warning": text(),
                    }
                ),
                body=obj(
                    {"model": text(maxLength=256)},
                    ("model",),
                    additionalProperties=False,
                ),
                example={"model": "gpt-6-astra"},
                header=("X-Intelligence-Request", "1"),
                effect="write",
                description="同步等待模型返回，不创建后台任务或测试历史。开始时重新选择可用分组及渠道，Anthropic 渠道（类型 14）调用 /v1/messages，其他调用 /v1/chat/completions；通过官方管理员指定渠道机制固定本次路由，失败不换渠道或协议。当前管理员名下复用 cockpit-智力测试专用 KEY，没有才通过官方 API 创建；自动改组并限制为所选模型。首次创建永久有效、无限 KEY 额度，仍扣账户余额；已有 KEY 额度/到期/启禁配置不重置。只允许 model，不允许指定用户、渠道、分组、凭据或题目。同一管理员并发返回 409；生成最长 180 秒，Cockpit 不设置输出 Token 上限参数；Claude 必需的 max_tokens 由 New API 配置补齐，实际仍受上游限制。超时/失败不自动重试，可能已产生费用。测试结果只在响应中；KEY 创建/配置沿用现有审计。不支持接续或按 ID 取回结果。html 为 null 时看 source/warning，不自动补代码或再次调用模型。",
            ),
            operation(
                "POST",
                "/cockpit/api/intelligence/preview",
                "沙箱预览当前 HTML",
                "智力测试",
                text(),
                body=obj(
                    {"html": text(maxLength=524288)},
                    ("html",),
                    additionalProperties=False,
                ),
                example={
                    "html": '<html><body><svg viewBox="0 0 100 100"><circle cx="50" cy="50" r="20"/></svg></body></html>'
                },
                header=("X-Intelligence-Request", "1"),
                description="仅在当前请求中返回 HTML，最大 512 KiB，不保存、不调用模型。响应通过 HTTP CSP 强制 sandbox allow-scripts，禁止同源访问、外部资源/连接、表单和顶层跳转。只在预览响应附加回传宽高的尺寸脚本，让页面自适应缩放，不改变测试返回的源码。管理页面脚本策略不放宽。页面 iframe 使用同源会话表单传入 HTML；表单必须有 Origin 和匹配的 session_id，不能以 PAT 表单绕过。这里描述外部调用的 JSON 形式。",
            ),
        ]
    )
    paths = {}
    for path, method, spec in ops:
        paths.setdefault(path, {})[method] = spec
    export_response = paths["/cockpit/api/statistics/export"]["get"]["responses"]["200"]
    export_response["content"] = {
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": {
            "schema": text(format="binary")
        }
    }
    paths["/cockpit/api/intelligence/preview"]["post"]["responses"]["200"][
        "content"
    ] = {"text/html": {"schema": text()}}
    paths["/cockpit/api/intelligence/test"]["post"]["responses"]["504"] = {
        "description": "同步生成超时，可能已计费；不自动重试。",
        "content": {"application/json": {"schema": ref("Error")}},
    }
    paths["/cockpit/api/statistics/balance"]["get"]["responses"]["503"] = {
        "description": "余额不可用时返回 code/message；认证或数据库故障也可能返回通用错误。",
        "content": {
            "application/json": {
                "schema": {
                    "oneOf": [
                        obj(
                            {"code": {"type": "integer"}, "message": text()},
                            ("code", "message"),
                        ),
                        ref("Error"),
                    ]
                }
            }
        },
    }
    paths["/cockpit/api/statistics/usage/details"]["post"]["responses"]["410"] = {
        "description": "快照过期/账号或账本不匹配，重新查询报表。",
        "content": {"application/json": {"schema": ref("Error")}},
    }
    try:
        app_version = version("new-api-cockpit")
    except PackageNotFoundError:
        app_version = "development"
    return {
        "openapi": "3.1.0",
        "info": {
            "title": "New API Cockpit API",
            "version": app_version,
            "description": "接口使用 New API 管理员 PAT 或同站浏览器会话。PAT 不是模型调用 KEY，原值不添加 sk-。金额多为十进制字符串；balance/alert 使用 JSON number。所有写入仍受后端权限与审计约束；确认头不是幂等键。结果不明确时查实际数据和操作记录，不自动重发。",
        },
        "servers": [{"url": "/", "description": "当前站点（保留协议、域名和端口）"}],
        "security": [{"adminPAT": []}, {"browserSession": []}],
        "tags": [
            {"name": name}
            for name in (
                "余额与报警",
                "用量统计",
                "渠道质量",
                "智力测试",
                "通知渠道",
                "用户管理",
                "用户额度",
                "定时额度",
                "令牌管理",
                "操作记录",
                "浏览器会话",
                "接口描述",
            )
        ],
        "paths": paths,
        "components": {
            "securitySchemes": {
                "adminPAT": {
                    "type": "http",
                    "scheme": "bearer",
                    "bearerFormat": "New API administrator PAT",
                    "description": "管理员 PAT，每次重新验证账号状态；不是 tokens.key。",
                },
                "browserSession": {
                    "type": "apiKey",
                    "in": "cookie",
                    "name": "cockpit_access",
                    "description": "HttpOnly 登录会话，由浏览器发送，不能从页面读取。",
                },
            },
            "schemas": {**SCHEMAS, **quality_schemas},
        },
    }


@cache
def serialized_document():
    """Reuse immutable contract JSON; authentication still runs for each request."""
    return json.dumps(document(), ensure_ascii=False, separators=(",", ":"))
