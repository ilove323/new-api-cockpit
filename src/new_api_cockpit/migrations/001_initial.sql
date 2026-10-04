-- Stored only in the separate monitoring database, never in New API.
CREATE TABLE IF NOT EXISTS balance_daily_runs (
    day date PRIMARY KEY, checked_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS balance_settings (
    id integer PRIMARY KEY CHECK (id=1), budget numeric(24,6) NOT NULL DEFAULT 0,
    threshold numeric(24,6) NOT NULL DEFAULT 0, start_month date NOT NULL,
    enabled boolean NOT NULL DEFAULT false, version bigint NOT NULL DEFAULT 1,
    updated_at timestamptz NOT NULL DEFAULT now(), updated_by text
);
CREATE TABLE IF NOT EXISTS balance_settings_audit (
    id bigserial PRIMARY KEY, username text NOT NULL, version bigint NOT NULL,
    budget numeric(24,6) NOT NULL, threshold numeric(24,6) NOT NULL,
    start_month date NOT NULL, enabled boolean NOT NULL, created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS balance_months (
    month date PRIMARY KEY, amount numeric(24,6) NOT NULL,
    archived_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS balance_state (
    id integer PRIMARY KEY CHECK (id=1), version bigint, current_month date,
    current_amount numeric(24,6), archived_amount numeric(24,6),remaining numeric(24,6),
    checked_at timestamptz, last_error text
);
CREATE TABLE IF NOT EXISTS balance_alerts (
    id bigserial PRIMARY KEY, remaining numeric(24,6) NOT NULL, threshold numeric(24,6) NOT NULL,
    spent numeric(24,6) NOT NULL,budget numeric(24,6) NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),updated_at timestamptz NOT NULL DEFAULT now(),
    resolved_at timestamptz
);
CREATE UNIQUE INDEX IF NOT EXISTS one_active_balance_alert ON balance_alerts ((true)) WHERE resolved_at IS NULL;
-- Remove the retired acknowledgment table, including its read_at column.
DROP TABLE IF EXISTS balance_alert_reads;
-- Migrate older installations to a single persisted latest alert.
DELETE FROM balance_alerts WHERE id NOT IN (
    SELECT id FROM balance_alerts ORDER BY updated_at DESC,id DESC LIMIT 1
);
CREATE UNIQUE INDEX IF NOT EXISTS one_balance_alert ON balance_alerts ((true));

-- Common channel selection and delivery status contain no channel credentials.
CREATE TABLE IF NOT EXISTS notification_settings (
    id integer PRIMARY KEY CHECK (id=1), version bigint NOT NULL DEFAULT 1,
    enabled boolean NOT NULL DEFAULT false, channel text NOT NULL DEFAULT 'feishu_app',
    updated_at timestamptz NOT NULL DEFAULT now(), updated_by text,
    last_attempt_at timestamptz, last_success_at timestamptz, last_error text
);
INSERT INTO notification_settings(id) VALUES (1) ON CONFLICT DO NOTHING;

-- Each provider owns a separate credential table. Secrets remain encrypted.
CREATE TABLE IF NOT EXISTS notification_feishu_settings (
    id integer PRIMARY KEY CHECK (id=1), app_id text NOT NULL DEFAULT '',
    secret_encrypted text NOT NULL DEFAULT '', receive_id_type text NOT NULL DEFAULT 'chat_id',
    receive_id text NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS notification_dingtalk_webhook_settings (
    id integer PRIMARY KEY CHECK (id=1), webhook_encrypted text NOT NULL DEFAULT '',
    secret_encrypted text NOT NULL DEFAULT '', signing_enabled boolean NOT NULL DEFAULT false
);

-- One-time migration from the former shared credential columns, then remove them.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.columns
               WHERE table_schema=current_schema() AND table_name='notification_settings'
                 AND column_name='app_id') THEN
        EXECUTE $migration$
            INSERT INTO notification_feishu_settings(id,app_id,secret_encrypted,receive_id_type,receive_id)
            SELECT 1,app_id,secret_encrypted,receive_id_type,receive_id FROM notification_settings
            WHERE id=1 AND channel='feishu_app' ON CONFLICT (id) DO NOTHING
        $migration$;
    END IF;
END $$;
INSERT INTO notification_feishu_settings(id) VALUES (1) ON CONFLICT DO NOTHING;
INSERT INTO notification_dingtalk_webhook_settings(id) VALUES (1) ON CONFLICT DO NOTHING;
DROP TABLE IF EXISTS notification_dingtalk_settings;
ALTER TABLE notification_settings DROP COLUMN IF EXISTS app_id;
ALTER TABLE notification_settings DROP COLUMN IF EXISTS secret_encrypted;
ALTER TABLE notification_settings DROP COLUMN IF EXISTS robot_code;
ALTER TABLE notification_settings DROP COLUMN IF EXISTS receive_id_type;
ALTER TABLE notification_settings DROP COLUMN IF EXISTS receive_id;

-- Open IDs cannot be reinterpreted as tenant user IDs. Preserve Feishu credentials.
UPDATE notification_feishu_settings SET receive_id_type='user_id',receive_id=''
WHERE receive_id_type='open_id';
UPDATE notification_settings SET enabled=false,
    version=version+1,updated_at=now(),last_attempt_at=NULL,last_success_at=NULL,
    last_error='个人接收方式已改为 user_id，请重新填写接收目标并启用渠道。'
WHERE channel='feishu_app' AND enabled=true
  AND EXISTS (SELECT 1 FROM notification_feishu_settings WHERE id=1 AND receive_id_type='user_id' AND receive_id='');
