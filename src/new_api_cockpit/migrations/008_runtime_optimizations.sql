-- Monitoring database only: no existing accounting rows are rewritten.
CREATE TABLE channel_catalog_sync_state (
 id integer PRIMARY KEY CHECK (id=1), history_discovered boolean NOT NULL DEFAULT false
);
INSERT INTO channel_catalog_sync_state(id) VALUES (1);

-- Immutable, short-lived tooltip details. Never store credentials or raw logs.
CREATE TABLE report_snapshots (
 id uuid PRIMARY KEY, owner_name text NOT NULL, scope_id integer NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now(), expires_at timestamptz NOT NULL
);
CREATE INDEX report_snapshots_expiry ON report_snapshots(expires_at);
CREATE INDEX report_snapshots_owner ON report_snapshots(owner_name,created_at);
CREATE TABLE report_snapshot_rows (
 report_id uuid NOT NULL REFERENCES report_snapshots(id) ON DELETE CASCADE,
 row_id integer NOT NULL, detail jsonb NOT NULL, PRIMARY KEY(report_id,row_id)
);
