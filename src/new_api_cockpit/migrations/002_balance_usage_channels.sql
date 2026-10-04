-- Store only New API channels explicitly excluded from budget calculations.
CREATE TABLE IF NOT EXISTS balance_excluded_channels (
    channel_id bigint PRIMARY KEY,
    excluded_at timestamptz NOT NULL DEFAULT now(),
    updated_by text
);
