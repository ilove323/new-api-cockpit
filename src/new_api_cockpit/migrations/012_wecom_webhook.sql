-- Add a disabled channel without changing existing delivery settings or budgets.
INSERT INTO notification_settings(channel) VALUES ('wecom_webhook')
ON CONFLICT (channel) DO NOTHING;

CREATE TABLE IF NOT EXISTS notification_wecom_webhook_settings (
    id integer PRIMARY KEY CHECK (id=1),
    webhook_encrypted text NOT NULL DEFAULT '',
    mentioned_list jsonb NOT NULL DEFAULT '[]'
        CHECK (jsonb_typeof(mentioned_list)='array'),
    mentioned_mobile_list jsonb NOT NULL DEFAULT '[]'
        CHECK (jsonb_typeof(mentioned_mobile_list)='array')
);
INSERT INTO notification_wecom_webhook_settings(id) VALUES (1) ON CONFLICT DO NOTHING;
