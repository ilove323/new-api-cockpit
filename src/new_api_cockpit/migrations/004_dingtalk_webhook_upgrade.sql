-- Upgrade published v0.1.0 installations from DingTalk app credentials to Webhook.
CREATE TABLE IF NOT EXISTS notification_dingtalk_webhook_settings (
    id integer PRIMARY KEY CHECK (id=1), webhook_encrypted text NOT NULL DEFAULT '',
    secret_encrypted text NOT NULL DEFAULT '', signing_enabled boolean NOT NULL DEFAULT false
);
INSERT INTO notification_dingtalk_webhook_settings(id) VALUES (1) ON CONFLICT DO NOTHING;

-- App credentials cannot be converted into a group robot Webhook URL.
UPDATE notification_settings SET channel='dingtalk_webhook',enabled=false,
    version=version+1,updated_at=now(),last_attempt_at=NULL,last_success_at=NULL,
    last_error='钉钉通知已改为自定义 Webhook 机器人，请重新配置并启用渠道。'
WHERE channel='dingtalk_app';

DROP TABLE IF EXISTS notification_dingtalk_settings;
