"""SMTP/STARTTLS/SMTPS text notifications with optional SMTP authentication."""

import ipaddress
import re
import smtplib
import ssl
from email.headerregistry import Address
from email.errors import HeaderParseError
from email.message import EmailMessage
from email.utils import formatdate, make_msgid

from .base import DeliveryError

DISPLAY_NAME = "邮件通知"
SECURITY_MODES = {"smtp", "starttls", "smtps"}
TIMEOUT = 8


def host_valid(value):
    try:
        ipaddress.ip_address(value)
        return True
    except ValueError:
        return bool(
            value
            and len(value) <= 253
            and all(
                re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", part)
                for part in value.rstrip(".").split(".")
            )
        )


def mailbox_valid(value):
    if (
        not isinstance(value, str)
        or not value
        or not value.isascii()
        or len(value) > 254
        or any(ord(c) < 32 or ord(c) == 127 for c in value)
    ):
        return False
    try:
        address = Address(addr_spec=value)
        return bool(address.username and address.domain and host_valid(address.domain))
    except (ValueError, HeaderParseError):
        return False


def validate(config, secret, *, require_complete=True):
    for key, limit in (
        ("smtp_host", 253),
        ("username", 512),
        ("from_address", 254),
        ("from_name", 128),
    ):
        value = config.get(key)
        if (
            not isinstance(value, str)
            or len(value) > limit
            or any(ord(c) < 32 or ord(c) == 127 for c in value)
        ):
            raise ValueError("邮件通知字段格式无效。")
    host = config["smtp_host"]
    if (host or require_complete) and not host_valid(host):
        raise ValueError("SMTP 服务器请填写主机名或 IP，不要包含协议、路径或端口。")
    port = config.get("smtp_port")
    if type(port) is not int or not 1 <= port <= 65535:
        raise ValueError("SMTP 端口应为 1～65535 的整数。")
    if (
        not isinstance(config.get("smtp_security"), str)
        or config["smtp_security"] not in SECURITY_MODES
    ):
        raise ValueError("请选择 SMTP、STARTTLS 或 SMTPS。")
    if type(config.get("auth_enabled")) is not bool:
        raise ValueError("邮件认证设置无效。")
    if not isinstance(secret, str) or len(secret) > 1024:
        raise ValueError("邮件密码格式无效。")
    if (
        require_complete
        and config["auth_enabled"]
        and (not config["username"] or not secret)
    ):
        raise ValueError("启用 SMTP 认证后必须填写用户名和密码（或授权码）。")
    sender = config["from_address"]
    if (sender or require_complete) and not mailbox_valid(sender):
        raise ValueError("请填写有效的发件邮箱，仅填写邮箱地址。")
    recipients = config.get("recipients")
    if (
        not isinstance(recipients, list)
        or len(recipients) > 100
        or (require_complete and not recipients)
        or any(not mailbox_valid(value) for value in recipients)
    ):
        raise ValueError("请填写 1～100 个有效的收件邮箱，仅填写邮箱地址。")


def send(config, secret, text):
    validate(config, secret)
    message = EmailMessage()
    message["Subject"] = text.splitlines()[0] if text else "余额监控通知"
    sender = Address(addr_spec=config["from_address"])
    message["From"] = Address(
        display_name=config["from_name"], username=sender.username, domain=sender.domain
    )
    recipients = list(dict.fromkeys(config["recipients"]))
    message["To"] = ", ".join(recipients)
    message["Date"] = formatdate(localtime=True)
    message["Message-ID"] = make_msgid()
    message.set_content(text, charset="utf-8")
    try:
        mode = config["smtp_security"]
        options = {"timeout": TIMEOUT}
        client_type = smtplib.SMTP
        if mode == "smtps":
            client_type = smtplib.SMTP_SSL
            options["context"] = ssl.create_default_context()
        with client_type(config["smtp_host"], config["smtp_port"], **options) as client:
            if mode == "starttls":
                client.ehlo()
                client.starttls(context=ssl.create_default_context())
                client.ehlo()
            if config["auth_enabled"]:
                client.login(config["username"], secret)
            refused = client.send_message(
                message, from_addr=config["from_address"], to_addrs=recipients
            )
            if refused:
                raise DeliveryError(
                    "部分收件人被拒绝，其他收件人可能已送达，请核对后处理。"
                )
    except DeliveryError:
        raise
    except smtplib.SMTPAuthenticationError:
        raise DeliveryError("SMTP 认证失败，请检查用户名、密码或邮箱授权码。") from None
    except ssl.SSLError:
        raise DeliveryError("邮件 TLS 校验失败，请检查服务器证书和加密方式。") from None
    except smtplib.SMTPNotSupportedError:
        raise DeliveryError(
            "邮件服务器不支持所选 TLS 或认证方式，不会降级为明文发送。"
        ) from None
    except (smtplib.SMTPException, OSError, ValueError, UnicodeError):
        # A disconnect/timeout can happen after DATA was accepted. Never retry.
        raise DeliveryError(
            "邮件发送失败，请检查服务器、端口、发件人与收件人；超时后请勿直接重发。"
        ) from None
