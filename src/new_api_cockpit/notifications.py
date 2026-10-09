"""Encrypted channel settings and isolated delivery; never change billing or alerts."""

import logging
import os

from cryptography.fernet import Fernet, InvalidToken
from psycopg.types.json import Jsonb

from new_api_cockpit import balance, scopes
from new_api_cockpit.locks import NOTIFICATION_LOCK
from new_api_cockpit.notification_channels import CHANNELS
from new_api_cockpit.notification_channels.base import DeliveryError
from new_api_cockpit.report import load_site_name


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
    except (InvalidToken, UnicodeDecodeError):
        raise ValueError("无法解密通知凭证，请检查服务器加密密钥。") from None


def public(row):
    # Allowlist prevents any future credential field from leaking through API JSON.
    keys = (
        "version",
        "enabled",
        "channel",
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
    elif row["channel"] == "dingtalk_webhook":
        result.update(
            webhook_configured=bool(row["webhook_encrypted"]),
            signing_enabled=row["signing_enabled"],
            signing_secret_configured=bool(row["secret_encrypted"]),
        )
    elif row["channel"] == "email":
        for key in (
            "smtp_host",
            "smtp_port",
            "smtp_security",
            "auth_enabled",
            "username",
            "from_address",
            "from_name",
            "recipients",
        ):
            result[key] = row[key]
        result["password_configured"] = bool(row["secret_encrypted"])
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
    if channel == "email":
        return conn.execute(
            """SELECT smtp_host,smtp_port,smtp_security,auth_enabled,username,
            secret_encrypted,from_address,from_name,recipients
            FROM notification_email_settings WHERE id=1"""
            + suffix
        ).fetchone()
    raise ValueError("不支持的报警渠道。")


def snapshot(channel=None):
    selected = channel if channel is not None else "feishu_app"
    if not isinstance(selected, str) or selected not in CHANNELS:
        raise ValueError("不支持的报警渠道。")
    with balance.connect() as conn:
        # Read the version and provider fields from one consistent snapshot,
        # without waiting for a network delivery holding its update lock.
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        row = conn.execute(
            "SELECT * FROM notification_settings WHERE channel=%s", (selected,)
        ).fetchone()
        if not row:
            raise ValueError("报警渠道正在初始化，请稍后重试。")
        config = provider_config(conn, selected)
    result = dict(row)
    result.update(config)
    return public(result)


def save_email(conn, body, current):
    fields = {}
    for key in ("smtp_host", "username", "from_address", "from_name"):
        value = body.get(key, "")
        if not isinstance(value, str):
            raise ValueError("邮件通知字段格式无效。")
        fields[key] = value.strip()
    fields.update(
        smtp_port=body.get("smtp_port"),
        smtp_security=body.get("smtp_security"),
        auth_enabled=body.get("auth_enabled"),
        recipients=body.get("recipients"),
    )
    supplied = body.get("password", "")
    if (
        not isinstance(supplied, str)
        or len(supplied) > 1024
        or any(char in supplied for char in "\r\n\0")
    ):
        raise ValueError("邮件密码格式无效。")
    # Never reuse credentials on a new SMTP destination or account.
    identity_changed = any(
        fields[key] != current[key]
        for key in ("smtp_host", "smtp_port", "smtp_security", "username")
    )
    encrypted = "" if identity_changed else current["secret_encrypted"]
    if supplied:
        encrypted = cipher().encrypt(supplied.encode()).decode()
    secret = decrypt(encrypted) if body["enabled"] and fields["auth_enabled"] else ""
    CHANNELS["email"].validate(fields, secret, require_complete=body["enabled"])
    fields["recipients"] = list(dict.fromkeys(fields["recipients"]))
    conn.execute(
        """UPDATE notification_email_settings SET smtp_host=%s,smtp_port=%s,
        smtp_security=%s,auth_enabled=%s,username=%s,secret_encrypted=%s,
        from_address=%s,from_name=%s,recipients=%s WHERE id=1""",
        (
            fields["smtp_host"],
            fields["smtp_port"],
            fields["smtp_security"],
            fields["auth_enabled"],
            fields["username"],
            encrypted,
            fields["from_address"],
            fields["from_name"],
            Jsonb(fields["recipients"]),
        ),
    )


def save(body, username):
    if (
        not isinstance(body, dict)
        or type(body.get("enabled")) is not bool
        or type(body.get("version")) is not int
    ):
        raise ValueError("报警渠道设置无效。")
    channel = body.get("channel")
    if not isinstance(channel, str) or channel not in CHANNELS:
        raise ValueError("不支持的报警渠道。")
    with balance.connect() as conn:
        row = conn.execute(
            "SELECT * FROM notification_settings WHERE channel=%s FOR UPDATE",
            (channel,),
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
        elif channel == "email":
            save_email(conn, body, current)
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
            """UPDATE notification_settings SET enabled=%s,version=version+1,
            updated_at=now(),updated_by=%s,last_attempt_at=NULL,last_success_at=NULL,last_error=NULL
            WHERE channel=%s""",
            (body["enabled"], username, channel),
        )


def deliver(test=False, expected_version=None, scope_id=1, channel=None):
    """Fan out to enabled channels; tests target exactly one saved channel."""
    if test:
        if not isinstance(channel, str) or channel not in CHANNELS:
            raise ValueError("请选择要测试的通知渠道。")
        return deliver_channel(channel, test=True, expected_version=expected_version)
    with balance.connect() as conn:
        selected = [
            row["channel"]
            for row in conn.execute(
                """SELECT channel FROM notification_settings
                WHERE enabled AND channel=ANY(%s) ORDER BY channel""",
                (list(CHANNELS),),
            )
        ]
    for name in selected:
        try:
            deliver_channel(name, scope_id=scope_id)
        except Exception as exc:
            # A database/decryption/provider failure must not skip other channels.
            logging.error("Notification %s failed (%s)", name, type(exc).__name__)


def deliver_channel(name, *, test=False, expected_version=None, scope_id=1):
    """Own transaction/status per provider, separate from committed billing data."""
    with balance.connect() as conn:
        # Serialize deliveries/settings; no job or minute polling is introduced.
        conn.execute("SELECT pg_advisory_xact_lock(%s)", (NOTIFICATION_LOCK,))
        common = conn.execute(
            "SELECT * FROM notification_settings WHERE channel=%s FOR UPDATE", (name,)
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
            secret = (
                ""
                if name == "email" and not config["auth_enabled"]
                else decrypt(config["secret_encrypted"])
            )
            channel.send(config, secret, text)
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
            last_success_at=CASE WHEN %s THEN now() ELSE last_success_at END WHERE channel=%s""",
            (error, error is None, name),
        )
    if test and error:
        raise ValueError(error)


def notify_safely(scope_id=1):
    try:
        deliver(scope_id=scope_id)
    except Exception as exc:
        logging.error("Notification delivery failed: %s", type(exc).__name__)
