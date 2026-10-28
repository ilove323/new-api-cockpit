"""Explicit Cockpit grants for upstream-validated, scoped administrator PATs."""

from .auth import LoginForbidden


READ_LOGS = {
    "usage",
    "usage_by_token",
    "usage_tokens",
    "usage_groups",
    "usage_by_selection",
    "usage_details",
    "export",
    "quality_summary",
    "quality_trends",
}
READ_USERS = {
    "quota_users",
    "operations_list",
    "operations_detail",
    "user_profile_groups",
}
WRITE_USERS = {
    "quota_preview",
    "quota_apply",
    "quota_schedule_create",
    "user_profile_action",
}
READ_KEYS = {"management_options", "management_query", "management_token_groups"}
WRITE_KEYS = {"management_action", "management_group_preview", "management_group_apply"}
READ_BALANCE = {
    "scopes",
    "balance_status",
    "balance_api",
    "balance_usage_channels",
    "balance_recalculate_history_preview",
}
WRITE_BALANCE = {
    "balance_alert_api",
    "balance_settings",
    "balance_recalculate_history",
    "balance_check",
    "notification_test",
}


def required_scopes(endpoint, method):
    if endpoint == "channel_flow_usage":
        return {"log:read", "channel:read"}
    if endpoint == "api_schema":
        return {"profile:read"}
    if endpoint in READ_LOGS:
        return {"log:read"}
    if endpoint in READ_USERS:
        return {"user:read"}
    if endpoint in WRITE_USERS:
        return {"user:write"}
    if endpoint == "quota_schedules_list":
        return {"user:read"}
    if endpoint == "quota_schedule_change":
        return {"user:write"}
    if endpoint in READ_KEYS:
        return {"user:read", "api_key:read"}
    if endpoint in WRITE_KEYS:
        return {"user:write", "api_key:write"}
    # These detail reads may provision a missing owner PAT in the existing
    # management adapter, so a read-only grant is deliberately insufficient.
    if endpoint == "user_profile_detail":
        return {"user:write"}
    if endpoint == "management_detail":
        return {"user:write", "api_key:read"}
    if endpoint in READ_BALANCE:
        return {"channel:read", "log:read"}
    if endpoint in WRITE_BALANCE:
        return {"option:write", "channel:read", "log:read"}
    if endpoint == "notification_settings":
        return {"option:read" if method in {"GET", "HEAD"} else "option:write"}
    if endpoint in {"manual_tokens", "image_studio.tokens"}:
        return {"api_key:read"}
    if endpoint in {
        "manual_models",
        "manual_key",
        "image_studio.models",
        "image_studio.generations",
        "image_studio.edits",
    }:
        return {"api_key:read", "api_key:reveal"}
    if endpoint == "intelligence_models":
        return {"channel:read"}
    if endpoint == "intelligence_preview":
        return {"profile:read"}
    if endpoint == "intelligence_test":
        return {"api_key:write", "api_key:reveal", "channel:read"}
    return None


def require_access(identity, endpoint, method):
    granted = getattr(identity, "scopes", None)
    if granted is None:
        # Only legacy PATs positively accepted by New API reach this branch.
        return
    required = required_scopes(endpoint, method)
    if required is None:
        raise LoginForbidden("该接口尚未开放给用户 API PAT，请使用网页登录。")
    missing = required - granted
    if missing:
        raise LoginForbidden("PAT 缺少权限：" + "、".join(sorted(missing)) + "。")


def require_secret_access(identity):
    granted = getattr(identity, "scopes", None)
    if granted is not None and "api_key:reveal" not in granted:
        raise LoginForbidden("PAT 缺少权限：api_key:reveal。")
