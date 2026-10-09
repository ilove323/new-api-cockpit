-- Each notification channel has its own switch, version and delivery status.
-- Keep the former selected channel's state; other channels start disabled.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.columns
               WHERE table_schema=current_schema() AND table_name='notification_settings'
                 AND column_name='id') THEN
        ALTER TABLE notification_settings DROP COLUMN id;
        ALTER TABLE notification_settings ADD PRIMARY KEY (channel);
    END IF;
END $$;
INSERT INTO notification_settings(channel)
VALUES ('feishu_app'), ('dingtalk_webhook'), ('email')
ON CONFLICT (channel) DO NOTHING;

CREATE TABLE IF NOT EXISTS notification_email_settings (
    id integer PRIMARY KEY CHECK (id=1),
    smtp_host text NOT NULL DEFAULT '',
    smtp_port integer NOT NULL DEFAULT 587 CHECK (smtp_port BETWEEN 1 AND 65535),
    smtp_security text NOT NULL DEFAULT 'starttls'
        CHECK (smtp_security IN ('smtp','starttls','smtps')),
    auth_enabled boolean NOT NULL DEFAULT false,
    username text NOT NULL DEFAULT '', secret_encrypted text NOT NULL DEFAULT '',
    from_address text NOT NULL DEFAULT '', from_name text NOT NULL DEFAULT '',
    recipients jsonb NOT NULL DEFAULT '[]' CHECK (jsonb_typeof(recipients)='array')
);
INSERT INTO notification_email_settings(id) VALUES (1) ON CONFLICT DO NOTHING;
