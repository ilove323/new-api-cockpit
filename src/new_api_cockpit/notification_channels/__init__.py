"""One implementation per notification channel; the registry is server-controlled."""

from . import dingtalk_webhook, email_smtp, feishu_app

CHANNELS = {
    "feishu_app": feishu_app,
    "dingtalk_webhook": dingtalk_webhook,
    "email": email_smtp,
}
