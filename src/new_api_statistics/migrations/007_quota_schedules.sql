-- Independent scheduled quota adjustments; never alter New API tables.
CREATE TABLE quota_schedule_rules (
    id bigserial PRIMARY KEY,
    enabled boolean NOT NULL DEFAULT false,
    period text NOT NULL CHECK (period IN ('daily', 'weekly', 'monthly')),
    operation text NOT NULL CHECK (operation IN ('add', 'subtract')),
    amount_units bigint NOT NULL CHECK (amount_units > 0 AND amount_units <= 500000000000000),
    executor_user_id bigint NOT NULL,
    executor_username text NOT NULL,
    created_by_user_id bigint NOT NULL,
    next_run_at timestamptz,
    version bigint NOT NULL DEFAULT 1,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    deleted_at timestamptz,
    CHECK (NOT enabled OR next_run_at IS NOT NULL)
);
CREATE INDEX quota_schedule_due ON quota_schedule_rules (next_run_at)
    WHERE enabled AND deleted_at IS NULL;

CREATE TABLE quota_schedule_rule_groups (
    rule_id bigint NOT NULL REFERENCES quota_schedule_rules(id),
    group_name text NOT NULL,
    PRIMARY KEY (rule_id, group_name)
);

CREATE TABLE quota_schedule_runs (
    id bigserial PRIMARY KEY,
    rule_id bigint NOT NULL REFERENCES quota_schedule_rules(id),
    scheduled_for timestamptz NOT NULL,
    snapshot jsonb NOT NULL,
    status text NOT NULL CHECK (status IN ('queued','running','success','partial','failed','unknown','missed')),
    started_at timestamptz,
    finished_at timestamptz,
    total_count integer NOT NULL DEFAULT 0,
    success_count integer NOT NULL DEFAULT 0,
    failed_count integer NOT NULL DEFAULT 0,
    unknown_count integer NOT NULL DEFAULT 0,
    skipped_count integer NOT NULL DEFAULT 0,
    message text NOT NULL DEFAULT '',
    UNIQUE (rule_id, scheduled_for)
);
CREATE INDEX quota_schedule_runs_recent ON quota_schedule_runs (id DESC);

CREATE TABLE quota_schedule_run_items (
    run_id bigint NOT NULL REFERENCES quota_schedule_runs(id),
    user_id bigint NOT NULL,
    username text NOT NULL,
    display_name text NOT NULL DEFAULT '',
    group_name text NOT NULL,
    operation text NOT NULL CHECK (operation IN ('add','subtract')),
    amount_units bigint NOT NULL CHECK (amount_units > 0),
    status text NOT NULL CHECK (status IN ('pending','sending','success','failed','unknown','skipped')),
    request_started_at timestamptz,
    finished_at timestamptz,
    message text NOT NULL DEFAULT '',
    PRIMARY KEY (run_id, user_id)
);
