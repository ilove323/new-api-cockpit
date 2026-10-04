-- Archive every New API channel before applying monitoring exclusions.
CREATE TABLE IF NOT EXISTS balance_channel_inventory (
    channel_id bigint PRIMARY KEY,
    channel_name text NOT NULL,
    first_seen_at timestamptz NOT NULL DEFAULT now(),
    last_seen_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS balance_channel_archive_months (
    month date PRIMARY KEY,
    archived_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS balance_month_channels (
    month date NOT NULL REFERENCES balance_channel_archive_months(month) ON DELETE CASCADE,
    channel_id bigint,
    channel_name text NOT NULL DEFAULT '',
    amount numeric(24,6) NOT NULL,
    archived_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE NULLS NOT DISTINCT (month, channel_id)
);

CREATE INDEX IF NOT EXISTS balance_month_channels_month_idx
    ON balance_month_channels(month);
