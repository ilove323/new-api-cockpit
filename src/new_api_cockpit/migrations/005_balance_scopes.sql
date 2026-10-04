-- Monitoring database only. Existing singleton rows become the all ledger.
CREATE TABLE balance_scopes (
 id serial PRIMARY KEY, kind text NOT NULL CHECK(kind IN ('all','tag','ungrouped')),
 tag_value text NOT NULL DEFAULT '', created_at timestamptz NOT NULL DEFAULT now(),
 updated_at timestamptz NOT NULL DEFAULT now(), UNIQUE(kind,tag_value),
 CHECK ((kind='tag' AND tag_value<>'') OR (kind<>'tag' AND tag_value=''))
);
INSERT INTO balance_scopes(kind) VALUES ('all'),('ungrouped');
ALTER TABLE balance_settings DROP CONSTRAINT IF EXISTS balance_settings_id_check;
ALTER TABLE balance_state DROP CONSTRAINT IF EXISTS balance_state_id_check;
ALTER TABLE balance_settings ADD COLUMN scope_id integer NOT NULL DEFAULT 1 REFERENCES balance_scopes(id) UNIQUE;
ALTER TABLE balance_state ADD COLUMN scope_id integer NOT NULL DEFAULT 1 REFERENCES balance_scopes(id) UNIQUE;
ALTER TABLE balance_settings_audit ADD COLUMN scope_id integer NOT NULL DEFAULT 1 REFERENCES balance_scopes(id);
DROP INDEX IF EXISTS one_active_balance_alert;
DROP INDEX IF EXISTS one_balance_alert;
ALTER TABLE balance_alerts ADD COLUMN scope_id integer NOT NULL DEFAULT 1 REFERENCES balance_scopes(id) UNIQUE;
ALTER TABLE balance_daily_runs DROP CONSTRAINT balance_daily_runs_pkey;
ALTER TABLE balance_daily_runs ADD COLUMN scope_id integer NOT NULL DEFAULT 1 REFERENCES balance_scopes(id);
ALTER TABLE balance_daily_runs ADD PRIMARY KEY(scope_id,day);
ALTER TABLE balance_channel_inventory ADD COLUMN scope_id integer NOT NULL DEFAULT 2 REFERENCES balance_scopes(id);
ALTER TABLE balance_month_channels ADD COLUMN scope_id integer REFERENCES balance_scopes(id);
ALTER TABLE balance_month_channels ADD COLUMN tag_value text;
CREATE TABLE balance_month_scopes (
 month date NOT NULL, scope_id integer NOT NULL REFERENCES balance_scopes(id),
 tag_value text NOT NULL DEFAULT '', amount numeric(24,6) NOT NULL,
 archived_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY(month,scope_id)
);
-- NULL snapshots are filled once after a successful read-only source catalog sync.
CREATE TABLE balance_scope_backfill (id integer PRIMARY KEY CHECK(id=1), completed boolean NOT NULL DEFAULT false);
INSERT INTO balance_scope_backfill(id) VALUES(1);
INSERT INTO balance_month_scopes(month,scope_id,amount,archived_at)
 SELECT month,1,amount,archived_at FROM balance_months;
