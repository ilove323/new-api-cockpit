"""Own KEY operations using the caller's PAT and official New API routes.

Explicit calls only: no polling, secret persistence, admin credentials, automatic
write retries or local configuration files. Every mutation reads before/after.
"""

import re
import time
from decimal import Decimal

from ... import quota
from ...integrations.new_api import account
from . import upstream

MAX_ID = 999999999999999
MAX_INT = 2**53 - 1


def integer(value, minimum=0, maximum=MAX_INT):
    return type(value) is int and minimum <= value <= maximum


def text(value, maximum=256):
    return (
        isinstance(value, str)
        and len(value) <= maximum
        and not re.search(r"[\x00-\x1f\x7f]", value)
    )


def key_id(value):
    if not integer(value, 1, MAX_ID):
        raise ValueError("KEY ID 无效。")
    return value


def token(row, user):
    if (
        not isinstance(row, dict)
        or row.get("user_id") != user["id"]
        or type(row.get("user_id")) is not int
        or not integer(row.get("id"), 1, MAX_ID)
        or not text(row.get("name"))
        or not text(row.get("group"))
        or not integer(row.get("status"), 1, 4)
        or not integer(row.get("expired_time"), -1)
        or not integer(row.get("remain_quota"), -MAX_INT)
        or type(row.get("unlimited_quota")) is not bool
    ):
        raise upstream.AccountError("KEY 响应不完整或不属于当前用户。")
    raw = row.get("key")
    raw = raw.removeprefix("sk-") if isinstance(raw, str) else ""
    return {
        **{
            name: row[name]
            for name in (
                "id",
                "name",
                "group",
                "status",
                "expired_time",
                "remain_quota",
                "unlimited_quota",
            )
        },
        "key_masked": "sk-" + raw[:4] + "••••" + raw[-4:]
        if len(raw) > 8
        else "sk-••••",
    }


def list_page(user, credential, page=1):
    if not integer(page, 1, 9999):
        raise ValueError("页码须为 1–9999。")
    data = account.own_keys(f"/api/token/?p={page}&page_size=50", credential)
    if (
        not isinstance(data, dict)
        or not integer(data.get("total"))
        or not isinstance(data.get("items"), list)
        or len(data["items"]) > 50
        or data["total"] < len(data["items"])
    ):
        raise upstream.AccountError("KEY 分页响应无效。")
    items = [token(row, user) for row in data["items"]]
    if len({row["id"] for row in items}) != len(items):
        raise upstream.AccountError("KEY 分页响应含重复项。")
    return {"items": items, "total": data["total"], "page": page, "page_size": 50}


def groups(credential):
    data = account.own_keys("/api/user/self/groups", credential)
    if not isinstance(data, dict) or len(data) > 256:
        raise upstream.AccountError("可用分组响应无效。")
    result = [{"name": "", "description": "默认分组（跟随账户）"}]
    for name, info in data.items():
        if not text(name) or not name or not isinstance(info, dict):
            raise upstream.AccountError("可用分组响应无效。")
        result.append(
            {
                "name": name,
                "description": info.get("desc") if text(info.get("desc")) else "",
            }
        )
    return {"groups": result}


def owned(user, credential, ident):
    row = account.own_keys(f"/api/token/{ident}", credential)
    info = token(row, user)
    if info["id"] != ident:
        raise upstream.AccountError("New API 返回了不同的 KEY。")
    return row


def reveal(user, credential, ident):
    owned(user, credential, ident)
    data = account.own_keys(
        f"/api/token/{ident}/key", credential, method="POST", body={}
    )
    raw = data.get("key") if isinstance(data, dict) else None
    raw = raw.removeprefix("sk-") if isinstance(raw, str) else ""
    if not re.fullmatch(r"[A-Za-z0-9_-]{16,256}", raw) or raw.startswith("sk-"):
        raise upstream.AccountError("New API 未返回有效的模型 KEY。")
    return {"key_id": ident, "key": "sk-" + raw}


def create_payload(arguments):
    name = arguments["name"].strip() if isinstance(arguments.get("name"), str) else ""
    unlimited = arguments.get("unlimited_quota", True)
    expiry = arguments.get("expired_time", -1)
    if (
        not name
        or not text(name, 50)
        or len(name.encode()) > 50
        or type(unlimited) is not bool
        or not integer(expiry, -1)
        or expiry != -1
        and expiry <= time.time()
    ):
        raise ValueError("名称最多 50 字节；有效期须为未来 Unix 秒数或 -1。")
    amount = arguments.get("amount")
    if unlimited:
        if amount is not None:
            raise ValueError("不单独限额时不要填写 amount。")
        remain = 0
    else:
        if not isinstance(amount, str) or not re.fullmatch(
            r"\d{1,16}(?:\.\d{1,6})?", amount
        ):
            raise ValueError("有限额 KEY 必须填写 amount，最多六位小数。")
        value = Decimal(amount) * quota.QUOTA_PER_YUAN
        if value != value.to_integral_value() or not 0 < value <= MAX_INT:
            raise ValueError("额度过大、过小或精度不符合站点要求。")
        remain = int(value)
    return {
        "name": name,
        "group": arguments.get("group", ""),
        "unlimited_quota": unlimited,
        "remain_quota": remain,
        "expired_time": expiry,
        "model_limits_enabled": False,
        "model_limits": "",
        "allow_ips": "",
        "cross_group_retry": False,
        "auto_groups": None,
    }


def group_payload(row, group):
    fields = (
        "name",
        "expired_time",
        "remain_quota",
        "unlimited_quota",
        "model_limits_enabled",
        "model_limits",
        "allow_ips",
        "allow_user_quota_query",
    )
    if (
        any(field not in row for field in fields)
        or type(row["model_limits_enabled"]) is not bool
        or type(row["allow_user_quota_query"]) is not bool
        or not text(row["model_limits"], 32768)
        or row["allow_ips"] is not None
        and (not isinstance(row["allow_ips"], str) or len(row["allow_ips"]) > 32768)
    ):
        raise upstream.AccountError("KEY 配置不完整，未发送修改。")
    if not row["unlimited_quota"] and row["remain_quota"] < 0:
        raise ValueError("KEY 剩余额度为负，New API 不接受原额度写回；请先调整额度。")
    # Same official full PUT as the plugin; never invent a PATCH endpoint.
    return {
        **{name: row[name] for name in fields},
        "id": row["id"],
        "group": group,
        "cross_group_retry": False,
        "auto_groups": None,
    }


def find_after(user, credential, page, ident):
    """Only ambiguous deletion needs a bounded, stable full-list check."""
    found = next((item for item in page["items"] if item["id"] == ident), None)
    seen = {item["id"] for item in page["items"]}
    pages = (page["total"] + 49) // 50
    # A lost response must not trigger hundreds of follow-up requests. Larger
    # collections remain explicitly unknown instead of inferring deletion.
    if found or pages > 4:
        return found, False
    for number in range(2, pages + 1):
        more = list_page(user, credential, number)
        if more["total"] != page["total"]:
            return None, False
        for item in more["items"]:
            if item["id"] == ident:
                return item, False
            if item["id"] in seen:
                return None, False
            seen.add(item["id"])
    return None, len(seen) == page["total"]


def mutate(user, credential, action, arguments):
    ident = key_id(arguments["key_id"]) if action != "create_key" else None
    payload = create_payload(arguments) if action == "create_key" else None
    group = arguments.get("group", "")
    if not text(group):
        raise ValueError("KEY 分组无效。")
    before = list_page(user, credential)
    row = owned(user, credential, ident) if ident else None
    if action != "delete_key" and group not in {
        item["name"] for item in groups(credential)["groups"]
    }:
        raise ValueError("所选分组不可用，请查询可用分组后重新选择。")
    failure = None
    if action == "change_key_group" and row["group"] == group:
        applied = True
    else:
        if action == "change_key_group":
            payload = group_payload(row, group)
        try:
            account.own_keys(
                f"/api/token/{ident}" if action == "delete_key" else "/api/token/",
                credential,
                method={
                    "create_key": "POST",
                    "change_key_group": "PUT",
                    "delete_key": "DELETE",
                }[action],
                body=payload,
            )
            applied = True
        except upstream.AccountError as exc:
            failure, applied = exc, False
    after, verified, result_id = None, None, ident
    try:
        after = list_page(user, credential)
        if action == "change_key_group":
            found = next((item for item in after["items"] if item["id"] == ident), None)
            if found is None:
                found = token(owned(user, credential, ident), user)
            verified = found["group"] == group
        elif action == "delete_key":
            if any(item["id"] == ident for item in after["items"]):
                verified = False
            elif after["total"] == len(after["items"]):
                verified = True
            elif failure and failure.status not in {400, 401, 403, 404, 405, 413, 429}:
                found, complete = find_after(user, credential, after, ident)
                verified = True if complete else False if found else None
        else:
            high = max((item["id"] for item in before["items"]), default=0)
            added = [item for item in after["items"] if item["id"] > high]
            if after["total"] == before["total"] + 1 and len(added) == 1:
                item = added[0]
                if all(
                    item[name] == payload[name]
                    for name in ("name", "group", "expired_time", "unlimited_quota")
                ) and (
                    payload["unlimited_quota"]
                    or item["remain_quota"] <= payload["remain_quota"]
                ):
                    verified, result_id = True, item["id"]
    except upstream.AccountError as exc:
        # Never resend a write because its readback failed.
        if exc.status in {401, 403, 429}:
            raise
    if failure and failure.status in {400, 401, 403, 404, 405, 413, 429}:
        raise failure
    applied = verified is True if failure else applied and verified is not False
    return {
        "applied": applied,
        "verified": verified,
        "key_id": result_id,
        "keys": after,
        "outcome": "applied"
        if applied
        else "unknown"
        if failure or verified is None
        else "not_applied",
        "message": (
            "KEY 操作已完成。" if verified else "New API 已返回成功，结果列表暂未确认。"
        )
        if applied
        else "未能确认修改生效，未自动重发；请查询 KEY 列表查看实际结果。",
    }


def schema(properties=None, required=()):
    return {
        "type": "object",
        "properties": properties or {},
        "required": list(required),
        "additionalProperties": False,
    }


ID = {
    "type": "integer",
    "minimum": 1,
    "maximum": MAX_ID,
    "description": "list_keys 返回的本人 KEY ID",
}
GROUP = {
    "type": "string",
    "maxLength": 256,
    "description": "list_key_groups 返回的分组名，空字符串表示跟随账户",
}
TOOLS = [
    {
        "name": "list_keys",
        "description": "查询本人 KEY 列表，每页 50 个，仅返回掩码；显式调用才查询，不后台刷新。",
        "inputSchema": schema(
            {"page": {"type": "integer", "minimum": 1, "maximum": 9999}}
        ),
    },
    {
        "name": "list_key_groups",
        "description": "查询本人 KEY 可用分组，含默认分组；不枚举其他用户或管理员分组。",
        "inputSchema": schema(),
    },
    {
        "name": "create_key",
        "description": "申请本人模型 KEY，按指定分组、额度和有效期创建；不是充值。非幂等，结果未知时先查列表，不要重试创建。",
        "inputSchema": schema(
            {
                "name": {"type": "string", "minLength": 1, "maxLength": 50},
                "group": GROUP,
                "unlimited_quota": {"type": "boolean", "default": True},
                "amount": {
                    "type": "string",
                    "description": "仅有限额时必填，账面金额 quota/500000，最多六位小数",
                },
                "expired_time": {
                    "type": "integer",
                    "minimum": -1,
                    "default": -1,
                    "description": "未来 Unix 秒数，-1 为长期有效",
                },
            },
            ("name",),
        ),
    },
    {
        "name": "delete_key",
        "description": "删除本人指定 KEY，使用该 KEY 的客户端将无法继续调用；先查询，再删除并读取结果，不自动重发。",
        "inputSchema": schema({"key_id": ID}, ("key_id",)),
    },
    {
        "name": "change_key_group",
        "description": "修改本人 KEY 分组，使用官方完整 PUT 保留其余配置及读取时的额度；不能保证与并发消费原子隔离。",
        "inputSchema": schema({"key_id": ID, "group": GROUP}, ("key_id", "group")),
    },
    {
        "name": "reveal_key",
        "description": "显式读取本人指定模型 KEY 的完整密钥，需 api_key:reveal；结果敏感，仅在用户要求复制或配置时调用。",
        "inputSchema": schema({"key_id": ID}, ("key_id",)),
    },
]
for tool in TOOLS:
    write = tool["name"] in {"create_key", "delete_key", "change_key_group"}
    tool["annotations"] = {
        "readOnlyHint": not write,
        "destructiveHint": write,
        "idempotentHint": not write,
        "openWorldHint": False,
    }


def call(name, user, credential, arguments):
    spec = next(tool["inputSchema"] for tool in TOOLS if tool["name"] == name)
    if (
        set(arguments) - spec["properties"].keys()
        or set(spec["required"]) - arguments.keys()
    ):
        raise ValueError("工具参数无效；只接受本人 KEY 参数，不接受用户、凭据或地址。")
    if name == "list_keys":
        return list_page(user, credential, arguments.get("page", 1))
    if name == "list_key_groups":
        return groups(credential)
    if name == "reveal_key":
        return reveal(user, credential, key_id(arguments["key_id"]))
    return mutate(user, credential, name, arguments)
