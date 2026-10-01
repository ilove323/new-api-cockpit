-- Aggregate in PostgreSQL. Parameters use an inclusive start, exclusive end.
WITH source AS MATERIALIZED (
    SELECT id, created_at, user_id, username, COALESCE("group", '') AS group_name,
           token_id AS source_token_id, token_name AS source_token_name,
           model_name, quota::numeric,
           COALESCE(prompt_tokens, 0)::bigint AS p,
           COALESCE(completion_tokens, 0)::bigint AS c,
           COALESCE(NULLIF(btrim(other), ''), '{}')::jsonb AS o
    FROM logs
    WHERE type = 2 AND created_at >= %(start)s AND created_at < %(end)s
      /* scope_channels */
      AND (%(token_ids)s::bigint[] IS NULL OR token_id = ANY(%(token_ids)s::bigint[]))
      AND (%(groups)s::text[] IS NULL OR COALESCE("group", '') = ANY(%(groups)s::text[]))
), parts AS MATERIALIZED (
    SELECT *,
        COALESCE(NULLIF(o->>'cache_tokens', '')::bigint, 0) AS cr,
        COALESCE(NULLIF(o->>'cache_write_tokens', '')::bigint,
                 NULLIF(o->>'cache_creation_tokens', '')::bigint, 0) AS cw,
        COALESCE(NULLIF(o->>'group_ratio', '')::numeric, 1) AS group_ratio,
        COALESCE(NULLIF(o->>'matched_tier', ''), '') AS matched_tier,
        jsonb_build_object(
            'expr_b64', o->>'expr_b64',
            'matched_tier', o->>'matched_tier',
            'model_price', o->>'model_price',
            'model_ratio', o->>'model_ratio',
            'completion_ratio', o->>'completion_ratio',
            'cache_ratio', o->>'cache_ratio',
            'cache_creation_ratio_5m', o->>'cache_creation_ratio_5m',
            'cache_creation_ratio', o->>'cache_creation_ratio'
        ) AS price_snapshot
    FROM source
), semantic_parts AS MATERIALIZED (
    -- Application model convention: only Claude excludes caches.
    SELECT *,
        left(lower(model_name), 6) = 'claude' AS separate_cache
    FROM parts
), normalized AS MATERIALIZED (
    SELECT *, CASE WHEN separate_cache THEN p ELSE GREATEST(p - cr - cw, 0) END AS noncache_input
    FROM semantic_parts
), dimensioned AS MATERIALIZED (
    SELECT *,
        CASE WHEN %(by_token)s THEN source_token_id ELSE 0 END AS token_id,
        CASE WHEN %(by_token)s
             THEN COALESCE(NULLIF(btrim(source_token_name), ''), '未知令牌') ELSE '' END AS token_name
    FROM normalized
), tier_buckets AS (
    SELECT user_id, username, token_id, token_name, model_name,
        matched_tier, group_name, group_ratio, price_snapshot,
        COUNT(*) AS request_count,
        SUM(p) AS raw_input_tokens,
        SUM(noncache_input) AS input_tokens,
        SUM(c) AS output_tokens,
        SUM(cw) AS cache_write_tokens,
        SUM(cr) AS cache_read_tokens,
        SUM(noncache_input) AS pricing_input_tokens,
        SUM(CASE WHEN separate_cache
                 THEN p + c + cr + cw ELSE p + c END) AS total_tokens,
        SUM(quota) / 500000 AS amount,
        MAX(created_at) AS latest_at,
        (array_agg(id ORDER BY created_at DESC, id DESC))[1] AS latest_id
    FROM dimensioned
    GROUP BY user_id, username, token_id, token_name, model_name,
             matched_tier, group_name, group_ratio, price_snapshot
), totals AS (
    SELECT user_id, username, token_id, token_name, model_name,
        SUM(request_count) AS request_count,
        COUNT(DISTINCT group_ratio) AS ratio_count,
        SUM(raw_input_tokens) AS raw_input_tokens,
        SUM(input_tokens) AS input_tokens,
        SUM(output_tokens) AS output_tokens,
        SUM(cache_write_tokens) AS cache_write_tokens,
        SUM(cache_read_tokens) AS cache_read_tokens,
        SUM(pricing_input_tokens) AS pricing_input_tokens,
        SUM(total_tokens) AS total_tokens,
        SUM(amount) AS amount,
        jsonb_agg(jsonb_build_object(
            'matched_tier', matched_tier,
            'group_name', group_name,
            'price_snapshot', price_snapshot,
            'group_ratio', group_ratio::text,
            'request_count', request_count,
            'raw_input_tokens', raw_input_tokens,
            'input_tokens', input_tokens,
            'output_tokens', output_tokens,
            'cache_read_tokens', cache_read_tokens,
            'cache_write_tokens', cache_write_tokens,
            'pricing_input_tokens', pricing_input_tokens,
            'total_tokens', total_tokens,
            'amount', amount::text,
            'latest_at', latest_at,
            'latest_id', latest_id
        ) ORDER BY matched_tier, group_name, group_ratio) AS tier_usage
    FROM tier_buckets
    GROUP BY user_id, username, token_id, token_name, model_name
), latest AS (
    SELECT *, ROW_NUMBER() OVER (
        PARTITION BY user_id, username, token_id, token_name, model_name
        ORDER BY created_at DESC, id DESC
    ) AS rank FROM dimensioned
)
SELECT t.*, m.group_ratio
FROM totals t JOIN latest m
    USING (user_id, username, token_id, token_name, model_name)
WHERE m.rank = 1
ORDER BY t.username, t.user_id, t.token_name, t.token_id, t.total_tokens DESC, t.model_name
