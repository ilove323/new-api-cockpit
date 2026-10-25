-- Synthetic own-account aggregation, never used against application databases.
CREATE TABLE logs (
    id bigint PRIMARY KEY, created_at bigint NOT NULL, type integer NOT NULL,
    user_id bigint NOT NULL, username text NOT NULL, "group" text,
    token_id bigint, token_name text, model_name text,
    quota bigint, prompt_tokens bigint, completion_tokens bigint, other text
);
INSERT INTO logs VALUES
    (1,extract(epoch from timestamptz '2026-10-01 01:00:00+08'),2,12,'member','default',7,'own','gpt-fixture',100000,100,20,'{"cache_tokens":30,"cache_creation_tokens":10}'),
    (2,extract(epoch from timestamptz '2026-10-01 02:00:00+08'),2,12,'member','default',7,'own','claude-fixture',50000,40,10,'{"cache_tokens":20,"cache_creation_tokens":5}'),
    (3,extract(epoch from timestamptz '2026-10-01 03:00:00+08'),2,99,'member','other',99,'foreign','gpt-foreign',9000000,999,999,'{}'),
    (4,extract(epoch from timestamptz '2026-09-30 23:59:59+08'),2,12,'member','default',7,'own','too-early',9000000,999,999,'{}'),
    (5,extract(epoch from timestamptz '2026-10-02 00:00:00+08'),2,12,'member','default',7,'own','at-end',9000000,999,999,'{}'),
    (6,extract(epoch from timestamptz '2026-10-01 04:00:00+08'),5,12,'member','default',7,'own','failed',9000000,999,999,'{}');
