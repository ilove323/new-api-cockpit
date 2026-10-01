"""Encrypted channel settings and isolated delivery; never change billing or alerts."""

import logging
import os

from cryptography.fernet import Fernet, InvalidToken

from new_api_statistics import balance, scopes
from new_api_statistics.locks import NOTIFICATION_LOCK
from new_api_statistics.notification_channels import CHANNELS
from new_api_statistics.notification_channels.base import DeliveryError
from new_api_statistics.report import load_site_name


def format_alert_message(alert, site_name):
    """One canonical text representation shared by channels and the read-only API."""
    return (
        "【余额不足报警】\n"
        f"站点：{site_name}\n"
        f"账本：{alert.get('scope_name', '全部')}\n"
        f"总额度：¥{alert['budget']:,.2f}\n累计消费：¥{alert['spent']:,.2f}\n"
        f"剩余额度：¥{alert['remaining']:,.2f}\n报警阈值：¥{alert['threshold']:,.2f}\n"
        f"检查时间：{alert['updated_at'].astimezone(balance.TZ):%Y-%m-%d %H:%M:%S}（北京时间）"
    )


def current_alert_message(scope_id=1):
    """Read the persisted active alert without running a balance check or notifying."""
    with balance.connect() as conn:
        alert = conn.execute(
            """SELECT remaining,threshold,spent,budget,updated_at
            FROM balance_alerts WHERE resolved_at IS NULL AND scope_id=%s ORDER BY updated_at DESC,id DESC LIMIT 1""",
            (scope_id,),
        ).fetchone()
        scope = scopes.get_scope(scope_id, conn)
        if alert:
            alert["scope_name"] = scopes.scope_name(scope)
    return format_alert_message(alert, load_site_name()) if alert else None


def current_alert_record(scope_id=1):
    """Return typed API fields for the active persisted alert."""
    with balance.connect() as conn:
        alert = conn.execute(
            """SELECT remaining,threshold,spent,budget,updated_at
            FROM balance_alerts WHERE resolved_at IS NULL AND scope_id=%s ORDER BY updated_at DESC,id DESC LIMIT 1""",
            (scope_id,),
        ).fetchone()
        scope = scopes.get_scope(scope_id, conn)
        if alert:
            alert["scope_name"] = scopes.scope_name(scope)
    if not alert:
        return None
    return {
        "title": "余额不足报警",
        "scope_id": scope["id"],
        "scope_name": scopes.scope_name(scope),
        "site_name": load_site_name(),
        "budget": float(alert["budget"]),
        "spent": float(alert["spent"]),
        "remaining": float(alert["remaining"]),
        "threshold": float(alert["threshold"]),
        "currency": "CNY",
        "checked_at": alert["updated_at"]
        .astimezone(balance.TZ)
        .isoformat(timespec="seconds"),
        "timezone": "Asia/Shanghai",
    }


def cipher():
    try:
        return Fernet(os.environ.get("NOTIFICATION_ENCRYPTION_KEY", "").encode())
    except (ValueError, TypeError):
        raise ValueError("通知加密密钥尚未配置或无效，请联系部署管理员。") from None


def decrypt(value):
    if not value:
        return ""
    try:
        return cipher().decrypt(value.encode()).decode()
    except InvalidToken:
        raise ValueError("无法解密通知凭证，请检查服务器加密密钥。") from None


def public(row):
    # Allowlist prevents any future credential field from leaking through API JSON.
    keys = (
        "version",
        "enabled",
        "channel",
        "active_channel",
        "last_attempt_at",
        "last_success_at",
        "last_error",
    )
    result = {key: row[key] for key in keys}
    if row["channel"] == "feishu_app":
        result.update(
            app_id=row["app_id"],
            receive_id_type=row["receive_id_type"],
            receive_id=row["receive_id"],
            secret_configured=bool(row["secret_encrypted"]),
        )
    else:
        result.update(
            webhook_configured=bool(row["webhook_encrypted"]),
            signing_enabled=row["signing_enabled"],
            signing_secret_configured=bool(row["secret_encrypted"]),
        )
    return result


def provider_config(conn, channel, lock=False):
    """Read only the selected provider's isolated credential row."""
    suffix = " FOR UPDATE" if lock else ""
    if channel == "feishu_app":
        return conn.execute(
            """SELECT app_id,secret_encrypted,receive_id_type,receive_id
            FROM notification_feishu_settings WHERE id=1"""
            + suffix
        ).fetchone()
    if channel == "dingtalk_webhook":
        return conn.execute(
            """SELECT webhook_encrypted,secret_encrypted,signing_enabled
            FROM notification_dingtalk_webhook_settings WHERE id=1"""
            + suffix
        ).fetchone()
    raise ValueError("不支持的报警渠道。")


def snapshot(channel=None):
    with balance.connect() as conn:
        row = conn.execute("SELECT * FROM notification_settings WHERE id=1").fetchone()
        selected = channel or (row["channel"] if row else None)
        if selected not in CHANNELS:
            raise ValueError("不支持的报警渠道。")
        config = provider_config(conn, selected)
    if not row:
        raise ValueError("报警渠道正在初始化，请稍后重试。")
    result = dict(row)
    result.update(config)
    result["active_channel"] = row["channel"]
    result["channel"] = selected
    if selected != row["channel"]:
        result.update(
            enabled=False, last_attempt_at=None, last_success_at=None, last_error=None
        )
    return public(result)


def save(body, username):
    if (
        not isinstance(body, dict)
        or type(body.get("enabled")) is not bool
        or type(body.get("version")) is not int
    ):
        raise ValueError("报警渠道设置无效。")
    channel = body.get("channel")
    if channel not in CHANNELS:
        raise ValueError("不支持的报警渠道。")
    with balance.connect() as conn:
        row = conn.execute(
            "SELECT * FROM notification_settings WHERE id=1 FOR UPDATE"
        ).fetchone()
        if row["version"] != body["version"]:
            raise balance.SettingsConflict()
        current = provider_config(conn, channel, lock=True)
        if channel == "feishu_app":
            fields = {}
            for key in ("app_id", "receive_id_type", "receive_id", "app_secret"):
                value = body.get(key, "")
                if not isinstance(value, str) or len(value) > 512:
                    raise ValueError("报警渠道字段格式无效。")
                fields[key] = value.strip()
            supplied = fields.pop("app_secret")
            encrypted = (
                ""
                if fields["app_id"] != current["app_id"] and not supplied
                else current["secret_encrypted"]
            )
            if supplied:
                encrypted = cipher().encrypt(supplied.encode()).decode()
            if body["enabled"]:
                CHANNELS[channel].validate(fields, decrypt(encrypted))
            elif fields["receive_id_type"] not in ("chat_id", "user_id"):
                raise ValueError("接收目标类型无效。")
            conn.execute(
                """UPDATE notification_feishu_settings SET app_id=%s,secret_encrypted=%s,
                receive_id_type=%s,receive_id=%s WHERE id=1""",
                (
                    fields["app_id"],
                    encrypted,
                    fields["receive_id_type"],
                    fields["receive_id"],
                ),
            )
        else:
            webhook = body.get("webhook_url", "")
            supplied = body.get("signing_secret", "")
            signing_enabled = body.get("signing_enabled")
            if (
                not isinstance(webhook, str)
                or len(webhook) > 2048
                or not isinstance(supplied, str)
                or len(supplied) > 512
                or type(signing_enabled) is not bool
            ):
                raise ValueError("钉钉 Webhook 配置格式无效。")
            webhook, supplied = webhook.strip(), supplied.strip()
            webhook_encrypted = current["webhook_encrypted"]
            secret_encrypted = current["secret_encrypted"]
            if webhook:
                webhook_encrypted = cipher().encrypt(webhook.encode()).decode()
            if supplied:
                secret_encrypted = cipher().encrypt(supplied.encode()).decode()
            config = {
                "webhook_url": decrypt(webhook_encrypted),
                "signing_enabled": signing_enabled,
            }
            if body["enabled"]:
                CHANNELS[channel].validate(config, decrypt(secret_encrypted))
            conn.execute(
                """UPDATE notification_dingtalk_webhook_settings
                SET webhook_encrypted=%s,secret_encrypted=%s,signing_enabled=%s WHERE id=1""",
                (webhook_encrypted, secret_encrypted, signing_enabled),
            )
        conn.execute(
            """UPDATE notification_settings SET enabled=%s,channel=%s,version=version+1,
            updated_at=now(),updated_by=%s,last_attempt_at=NULL,last_success_at=NULL,last_error=NULL
            WHERE id=1""",
            (body["enabled"], channel, username),
        )


def deliver(test=False, expected_version=None, scope_id=1):
    """Send after the balance transaction commits. Failures cannot roll it back."""
    with balance.connect() as conn:
        # Serialize deliveries/settings; no job or minute polling is introduced.
        conn.execute("SELECT pg_advisory_xact_lock(%s)", (NOTIFICATION_LOCK,))
        common = conn.execute(
            "SELECT * FROM notification_settings WHERE id=1 FOR UPDATE"
        ).fetchone()
        config = dict(common)
        config.update(provider_config(conn, common["channel"], lock=True))
        if test and expected_version != config["version"]:
            raise balance.SettingsConflict()
        if not test and not config["enabled"]:
            return
        if test:
            alert = None
        else:
            alert = conn.execute(
                "SELECT * FROM balance_alerts WHERE resolved_at IS NULL AND scope_id=%s",
                (scope_id,),
            ).fetchone()
            enabled = conn.execute(
                "SELECT enabled FROM balance_settings WHERE scope_id=%s", (scope_id,)
            ).fetchone()
            if not alert or not enabled or not enabled["enabled"]:
                return
            alert["scope_name"] = scopes.scope_name(scopes.get_scope(scope_id, conn))
        error = None
        try:
            # Share the page's live SystemName lookup, rather than a deployment label.
            if test:
                site_name = load_site_name()
                channel = CHANNELS[config["channel"]]
                text = f"【余额监控测试】\n站点：{site_name}\n{channel.DISPLAY_NAME}连接成功。"
            else:
                text = format_alert_message(alert, load_site_name())
            channel = CHANNELS[config["channel"]]
            if config["channel"] == "dingtalk_webhook":
                config["webhook_url"] = decrypt(config["webhook_encrypted"])
            channel.send(config, decrypt(config["secret_encrypted"]), text)
        except ValueError as exc:
            error = str(exc)
        except Exception as exc:
            error = (
                str(exc)
                if isinstance(exc, DeliveryError)
                else "通知发送失败，请检查服务配置。"
            )
        conn.execute(
            """UPDATE notification_settings SET last_attempt_at=now(),last_error=%s,
            last_success_at=CASE WHEN %s THEN now() ELSE last_success_at END WHERE id=1""",
            (error, error is None),
        )
    if test and error:
        raise ValueError(error)


def notify_safely(scope_id=1):
    try:
        deliver(scope_id=scope_id)
    except Exception as exc:
        logging.error("Notification delivery failed: %s", type(exc).__name__)
