WITH source AS MATERIALIZED (
    SELECT id,created_at,type,COALESCE(username,'') AS username,
           COALESCE(token_id,0) AS token_id,COALESCE(token_name,'') AS token_name,
           COALESCE(model_name,'') AS model_name,COALESCE(channel_id,0) AS channel_id,
           COALESCE("group",'') AS log_group,quota,
           /* optional_columns */
           COALESCE(NULLIF(btrim(other),''),'{}')::jsonb AS meta
    FROM logs
    WHERE type IN (2,5) AND created_at >= %(start_ts)s AND created_at < %(end_ts)s
      AND (%(models)s::text[] IS NULL OR COALESCE(model_name,'')=ANY(%(models)s))
      AND (%(users)s::text[] IS NULL OR COALESCE(username,'')=ANY(%(users)s))
      AND (%(tokens)s::bigint[] IS NULL OR COALESCE(token_id,0)=ANY(%(tokens)s))
), catalog AS (
    SELECT * FROM jsonb_to_recordset(%(catalog)s::jsonb)
        AS c(channel_id bigint,tag_value text,channel_name text,channel_status int,is_deleted boolean)
), decoded AS MATERIALIZED (
    SELECT *, CASE WHEN NULLIF(request_id,'') IS NOT NULL THEN 'request:'||request_id ELSE 'log:'||id END AS request_key,
        CASE WHEN jsonb_typeof(meta#>'{admin_info,request_policy}')='array' THEN meta#>'{admin_info,request_policy}'
             WHEN jsonb_typeof(meta->'request_policy')='array' THEN meta->'request_policy' ELSE '[]'::jsonb END AS policy
    FROM source
    WHERE COALESCE(meta->>'ws','false')<>'true'
      AND (COALESCE(meta->>'request_path','')='' OR meta->>'request_path' IN
           ('/v1/chat/completions','/v1/completions','/v1/messages','/v1/responses','/v1/responses/compact')
           OR meta->>'request_path' ~ '^/v1(beta)?/models/[^/]+:(streamGenerateContent|generateContent)$')
), requests AS MATERIALIZED (
    SELECT DISTINCT ON (request_key) * FROM decoded
    ORDER BY request_key,jsonb_array_length(policy) DESC,created_at DESC,id DESC
), events AS MATERIALIZED (
    SELECT r.*,e.value AS event,e.ordinality AS ordinal,
        CASE WHEN e.value->>'attempt' ~ '^[0-9]{1,9}$' THEN (e.value->>'attempt')::int END AS attempt,
        CASE WHEN e.value->>'channel_id' ~ '^[0-9]{1,18}$' THEN (e.value->>'channel_id')::bigint END AS event_channel,
        CASE WHEN e.value->>'elapsed_ms' ~ '^[0-9]{1,15}$' THEN (e.value->>'elapsed_ms')::bigint END AS elapsed_ms
    FROM requests r CROSS JOIN LATERAL jsonb_array_elements(r.policy) WITH ORDINALITY e
), valid_events AS MATERIALIZED (
    SELECT * FROM events WHERE attempt>0 AND event_channel>0
), starts AS (
    SELECT request_key,attempt,event_channel,MIN(elapsed_ms) FILTER (WHERE event#>>'{decision,action}'='attempt') AS began_ms
    FROM valid_events GROUP BY request_key,attempt,event_channel
), terminals AS (
    SELECT DISTINCT ON (request_key,attempt,event_channel) * FROM valid_events
    ORDER BY request_key,attempt,event_channel,
        (event#>>'{decision,action}' IN ('success','failure') OR event#>>'{decision,reason}'='stream_not_successful') DESC NULLS LAST,
        ordinal DESC
), attempt_counts AS (
    SELECT request_key,event_channel,COUNT(DISTINCT attempt) AS attempts,MIN(attempt) AS first_attempt,
        MAX(MAX(attempt)) OVER(PARTITION BY request_key) AS last_attempt
    FROM valid_events GROUP BY request_key,event_channel
), error_records AS (
    /* Earlier error rows can contain status_code missing from the final route snapshot. */
    SELECT d.*,COALESCE(p.attempt,CASE WHEN a.attempts=1 THEN a.first_attempt END) AS error_attempt
    FROM decoded d
    LEFT JOIN attempt_counts a ON a.request_key=d.request_key AND a.event_channel=d.channel_id
    CROSS JOIN LATERAL (
        SELECT MAX(CASE WHEN e->>'attempt' ~ '^[0-9]{1,9}$' THEN (e->>'attempt')::int END) AS attempt
        FROM jsonb_array_elements(d.policy) e
        WHERE CASE WHEN e->>'channel_id' ~ '^[0-9]{1,18}$' THEN (e->>'channel_id')::bigint END=d.channel_id
    ) p
    WHERE d.type=5
), faults AS MATERIALIZED (
    SELECT DISTINCT ON (request_key,channel_id,error_attempt) request_key,channel_id,error_attempt,meta
    FROM error_records WHERE error_attempt>0
    ORDER BY request_key,channel_id,error_attempt,
        (btrim(meta->>'status_code') ~ '^[45][0-9]{2}$') DESC NULLS LAST,created_at DESC,id DESC
), policy_calls AS (
    SELECT t.id,t.created_at,t.request_key,t.model_name,t.event_channel AS channel_id,
        CASE WHEN t.event_channel=t.channel_id AND t.attempt=a.last_attempt THEN t.meta
             ELSE COALESCE(f.meta,'{}'::jsonb) END AS meta,t.is_stream,
        false AS legacy,
        CASE WHEN t.event#>>'{decision,action}'='success' THEN 'success'
             WHEN t.event#>>'{decision,action}'='failure' OR t.event#>>'{decision,reason}'='stream_not_successful' THEN 'failure'
             WHEN f.request_key IS NOT NULL THEN 'failure'
             ELSE 'unknown' END AS initial_outcome,
        (SELECT btrim(code) FROM (VALUES
            (1,t.event->>'status_code'),(2,t.event->>'status'),(3,f.meta->>'status_code'),
            (4,CASE WHEN t.event_channel=t.channel_id AND t.attempt=a.last_attempt THEN t.meta->>'status_code' END)
        ) candidates(priority,code) WHERE btrim(code) ~ '^[45][0-9]{2}$' ORDER BY priority LIMIT 1) AS status_code,
        COALESCE(NULLIF(btrim(t.event->>'error_code'),''),NULLIF(btrim(f.meta->>'error_code'),''),
            CASE WHEN t.event_channel=t.channel_id AND t.attempt=a.last_attempt THEN NULLIF(btrim(t.meta->>'error_code'),'') END) AS error_code,
        COALESCE(t.event->>'error_source',t.event#>>'{decision,source}',f.meta->>'error_source') AS error_source,
        lower(COALESCE(t.event->>'error_type',f.meta->>'error_type',
            CASE WHEN t.event_channel=t.channel_id AND t.attempt=a.last_attempt THEN t.meta->>'error_type' END)) AS error_type,
        CASE WHEN t.elapsed_ms>=s.began_ms THEN (t.elapsed_ms-s.began_ms)::double precision END AS duration_ms,
        /* Completion tokens belong only to the billed final attempt, never to an earlier retry. */
        CASE WHEN t.type=2 AND t.event_channel=t.channel_id AND t.attempt=a.last_attempt
             THEN t.completion_tokens END AS output_tokens,
        a.last_attempt=1 AS direct
    FROM terminals t JOIN starts s USING(request_key,attempt,event_channel)
    JOIN attempt_counts a ON a.request_key=t.request_key AND a.event_channel=t.event_channel
    LEFT JOIN faults f ON f.request_key=t.request_key AND f.channel_id=t.event_channel AND f.error_attempt=t.attempt
), legacy_calls AS (
    SELECT d.id,d.created_at,d.request_key,d.model_name,d.channel_id,d.meta,d.is_stream,
        true AS legacy,
        CASE WHEN jsonb_typeof(d.meta)<>'object' OR d.meta->>'cockpit_metadata_invalid'='true' THEN 'unknown'
             WHEN d.type=5 OR d.meta#>>'{stream_status,status}'='error' THEN 'failure'
             WHEN d.type=2 AND (d.is_stream=false OR d.meta#>>'{stream_status,status}'='ok') THEN 'success'
             ELSE 'unknown' END AS initial_outcome,
        CASE WHEN btrim(d.meta->>'status_code') ~ '^[45][0-9]{2}$' THEN btrim(d.meta->>'status_code') END AS status_code,
        NULLIF(btrim(d.meta->>'error_code'),'') AS error_code,
        d.meta->>'error_source' AS error_source,lower(d.meta->>'error_type') AS error_type,
        CASE WHEN d.use_time>0 THEN d.use_time::double precision*1000 END AS duration_ms,
        CASE WHEN d.type=2 THEN d.completion_tokens END AS output_tokens,
        CASE WHEN jsonb_typeof(d.meta#>'{admin_info,use_channel}')='array' THEN jsonb_array_length(d.meta#>'{admin_info,use_channel}')<=1
             WHEN jsonb_typeof(d.meta->'use_channel')='array' THEN jsonb_array_length(d.meta->'use_channel')<=1
             ELSE false END AS direct
    FROM decoded d WHERE NOT EXISTS(SELECT 1 FROM valid_events v WHERE v.request_key=d.request_key)
), classified AS MATERIALIZED (
    SELECT *,CASE
        WHEN (initial_outcome='success' OR error_source='system' OR legacy) AND meta#>>'{stream_status,response_status}'='failed' THEN 'failure'
        WHEN (initial_outcome='success' OR error_source='system' OR legacy) AND (meta#>>'{stream_status,response_status}'='cancelled'
          OR meta#>>'{stream_status,end_reason}' IN ('client_gone','ping_failed','ping_fail')) THEN 'ignored'
        WHEN (initial_outcome='success' OR error_source='system' OR legacy) AND meta#>>'{stream_status,response_status}'='incomplete' THEN 'unknown'
        WHEN lower(error_code) LIKE 'violation_fee.%%' THEN 'ignored'
        ELSE initial_outcome END AS outcome,
        CASE WHEN direct AND is_stream AND meta->>'frt' ~ '^[0-9]{1,15}(\.[0-9]{1,6})?$'
                  AND (meta->>'frt')::double precision>0 THEN (meta->>'frt')::double precision END AS frt_ms
    FROM (SELECT * FROM policy_calls UNION ALL SELECT * FROM legacy_calls) calls
), scoped_calls AS MATERIALIZED (
    -- Filter current channel status only after reconstructing the complete retry chain.
    SELECT q.*,COALESCE(c.tag_value,'') AS tag_value,
        CASE WHEN %(mode)s='upstream' THEN COALESCE(c.tag_value,'') ELSE COALESCE(q.channel_id,0)::text END AS target,
        q.outcome='success' AND q.output_tokens>0 AND q.duration_ms>0
            AND (%(latency_scope)s='all' OR q.direct) AND (NOT q.legacy OR q.direct) AS tps_eligible
    FROM classified q LEFT JOIN catalog c USING(channel_id)
    WHERE (%(tag)s::text IS NULL OR COALESCE(c.tag_value,'')=%(tag)s)
      AND (%(channel_status)s='all' OR (c.channel_status=1 AND NOT c.is_deleted))
      AND (%(channels)s::bigint[] IS NULL OR COALESCE(q.channel_id,0)=ANY(%(channels)s))
      AND (%(stream)s='all' OR q.is_stream=(%(stream)s='stream'))
), metric_rows AS (
    SELECT model_name,target,
        GROUPING(model_name,target) AS grouping,
        COUNT(*) AS request_count,COUNT(*) FILTER(WHERE outcome='success') AS success_count,
        COUNT(*) FILTER(WHERE outcome='failure') AS failure_count,COUNT(*) FILTER(WHERE outcome='ignored') AS ignored_count,
        COUNT(*) FILTER(WHERE outcome='unknown') AS unknown_count,COUNT(*) FILTER(WHERE legacy) AS legacy_count,
        COUNT(*) FILTER(WHERE NOT direct) AS retry_or_unknown_count,
        COUNT(duration_ms) FILTER(WHERE outcome='success' AND (%(latency_scope)s='all' OR direct)) AS duration_samples,
        COALESCE(SUM(duration_ms) FILTER(WHERE outcome='success' AND (%(latency_scope)s='all' OR direct)),0) AS duration_total_ms,
        COUNT(frt_ms) FILTER(WHERE outcome='success') AS frt_samples,
        COUNT(*) FILTER(WHERE tps_eligible) AS tps_samples,
        COALESCE(SUM(output_tokens) FILTER(WHERE tps_eligible),0) AS tps_output_tokens,
        COALESCE(SUM(duration_ms) FILTER(WHERE tps_eligible),0) AS tps_duration_ms,
        percentile_cont(ARRAY[0.5,0.95]) WITHIN GROUP(ORDER BY duration_ms)
            FILTER(WHERE outcome='success' AND (%(latency_scope)s='all' OR direct)) AS duration_percentiles,
        percentile_cont(ARRAY[0.5,0.95]) WITHIN GROUP(ORDER BY frt_ms) FILTER(WHERE outcome='success') AS frt_percentiles,
        COUNT(DISTINCT request_key) AS unique_requests
    FROM scoped_calls GROUP BY GROUPING SETS ((model_name,target),())
), error_counts AS (
    SELECT model_name,target,COALESCE(NULLIF(status_code,''),NULLIF(error_code,''),
        CASE WHEN is_stream AND (meta#>>'{stream_status,status}'='error'
            OR meta#>>'{stream_status,response_status}'='failed') THEN '流式失败（未记录错误码）' ELSE '未知' END) AS code,COUNT(*) AS count
    FROM scoped_calls WHERE outcome='failure' GROUP BY model_name,target,code
), errors AS (
    SELECT model_name,target,jsonb_object_agg(code,count) AS failure_codes
    FROM error_counts GROUP BY model_name,target
), billed AS MATERIALIZED (
    SELECT d.*,COALESCE(c.tag_value,'') AS tag_value,
        CASE WHEN %(mode)s='upstream' THEN COALESCE(c.tag_value,'') ELSE COALESCE(d.channel_id,0)::text END AS target,
        CASE WHEN %(with_trends)s THEN jsonb_build_object('expr_b64',meta->'expr_b64','matched_tier',meta->'matched_tier',
            'model_price',CASE WHEN meta->>'use_price'='true' OR meta->>'billing_unit' IN ('request','image') THEN meta->'model_price' END,'model_ratio',meta->'model_ratio','completion_ratio',meta->'completion_ratio',
            'cache_ratio',meta->'cache_ratio','cache_creation_ratio',meta->'cache_creation_ratio',
            'cache_creation_ratio_5m',meta->'cache_creation_ratio_5m') END AS prices
    FROM decoded d LEFT JOIN catalog c USING(channel_id)
    WHERE type=2 AND (%(tag)s::text IS NULL OR COALESCE(c.tag_value,'')=%(tag)s)
      AND (%(channel_status)s='all' OR (c.channel_status=1 AND NOT c.is_deleted))
      AND (%(channels)s::bigint[] IS NULL OR COALESCE(d.channel_id,0)=ANY(%(channels)s))
      AND (%(stream)s='all' OR d.is_stream=(%(stream)s='stream'))
), amounts AS (
    SELECT model_name,target,GROUPING(model_name,target) AS grouping,SUM(quota)::numeric/500000 AS amount,
        COUNT(*) AS billed_records
    FROM billed GROUP BY GROUPING SETS ((model_name,target),())
), tariffs AS (
    SELECT model_name,target,log_group,meta->>'group_ratio' AS group_ratio,prices,
        COUNT(*) AS request_count,SUM(quota)::numeric/500000 AS amount
    FROM billed WHERE %(with_trends)s GROUP BY model_name,target,log_group,meta->>'group_ratio',prices
), ordered_tariffs AS (
    SELECT *,ROW_NUMBER() OVER(PARTITION BY model_name,target ORDER BY request_count DESC,prices::text,log_group,group_ratio) AS rn
    FROM tariffs
), price_lists AS (
    SELECT model_name,target,COUNT(*) AS price_bucket_count,
        jsonb_agg(jsonb_build_object('snapshot',prices,'group',log_group,'group_ratio',group_ratio,
            'request_count',request_count,'amount',amount::text) ORDER BY rn) FILTER(WHERE rn<=100) AS pricing_buckets
    FROM ordered_tariffs GROUP BY model_name,target
), sample_requests AS (
    SELECT model_name,target,request_key,MAX(created_at) AS latest_at
    FROM scoped_calls WHERE %(with_trends)s AND request_key LIKE 'request:%%'
    GROUP BY model_name,target,request_key
), ordered_requests AS (
    SELECT *,ROW_NUMBER() OVER(PARTITION BY model_name,target ORDER BY latest_at DESC,request_key) AS rn FROM sample_requests
), trace_samples AS (
    SELECT model_name,target,jsonb_agg(substring(request_key FROM 9) ORDER BY rn) AS sample_request_ids
    FROM ordered_requests WHERE rn<=5 GROUP BY model_name,target
), result_rows AS (
    SELECT COALESCE(m.model_name,a.model_name) AS model_name,COALESCE(m.target,a.target) AS target,
        COALESCE(m.grouping,a.grouping) AS grouping,
        COALESCE(m.request_count,0) AS request_count,
        COALESCE(m.success_count,0) AS success_count,
        COALESCE(m.failure_count,0) AS failure_count,
        COALESCE(m.ignored_count,0) AS ignored_count,
        COALESCE(m.unknown_count,0) AS unknown_count,
        COALESCE(m.legacy_count,0) AS legacy_count,
        COALESCE(m.retry_or_unknown_count,0) AS retry_or_unknown_count,
        COALESCE(m.duration_samples,0) AS duration_samples,
        COALESCE(m.duration_total_ms,0) AS duration_total_ms,
        COALESCE(m.frt_samples,0) AS frt_samples,
        COALESCE(m.tps_samples,0) AS tps_samples,
        COALESCE(m.tps_output_tokens,0) AS tps_output_tokens,
        COALESCE(m.tps_duration_ms,0) AS tps_duration_ms,
        COALESCE(m.unique_requests,0) AS unique_requests,
        m.duration_percentiles,m.frt_percentiles,
        COALESCE(a.amount,0)::text AS amount,COALESCE(a.billed_records,0) AS billed_records,
        COALESCE(e.failure_codes,'{}'::jsonb) AS failure_codes,COALESCE(p.pricing_buckets,'[]'::jsonb) AS pricing_buckets,
        COALESCE(p.price_bucket_count,0) AS price_bucket_count,
        COALESCE(trace.sample_request_ids,'[]'::jsonb) AS sample_request_ids
    FROM metric_rows m FULL JOIN amounts a ON a.grouping=m.grouping
        AND (m.grouping=3 OR a.model_name=m.model_name AND a.target=m.target)
    LEFT JOIN errors e ON e.model_name=COALESCE(m.model_name,a.model_name) AND e.target=COALESCE(m.target,a.target)
    LEFT JOIN price_lists p ON p.model_name=COALESCE(m.model_name,a.model_name) AND p.target=COALESCE(m.target,a.target)
    LEFT JOIN trace_samples trace ON trace.model_name=COALESCE(m.model_name,a.model_name) AND trace.target=COALESCE(m.target,a.target)
), hourly AS (
    SELECT to_timestamp(created_at-created_at%%3600) AS hour,COUNT(*) AS request_count,
        COUNT(*) FILTER(WHERE outcome='success') AS success_count,COUNT(*) FILTER(WHERE outcome='failure') AS failure_count,
        COUNT(*) FILTER(WHERE outcome='ignored') AS ignored_count,COUNT(*) FILTER(WHERE outcome='unknown') AS unknown_count,
        COUNT(duration_ms) FILTER(WHERE outcome='success' AND (%(latency_scope)s='all' OR direct)) AS duration_samples,
        COALESCE(SUM(duration_ms) FILTER(WHERE outcome='success' AND (%(latency_scope)s='all' OR direct)),0) AS duration_total_ms,
        COUNT(frt_ms) FILTER(WHERE outcome='success') AS frt_samples,
        COUNT(*) FILTER(WHERE tps_eligible) AS tps_samples,
        COALESCE(SUM(output_tokens) FILTER(WHERE tps_eligible),0) AS tps_output_tokens,
        COALESCE(SUM(duration_ms) FILTER(WHERE tps_eligible),0) AS tps_duration_ms,
        percentile_cont(ARRAY[0.5,0.95]) WITHIN GROUP(ORDER BY duration_ms)
            FILTER(WHERE outcome='success' AND (%(latency_scope)s='all' OR direct)) AS duration_percentiles,
        percentile_cont(ARRAY[0.5,0.95]) WITHIN GROUP(ORDER BY frt_ms) FILTER(WHERE outcome='success') AS frt_percentiles
    FROM scoped_calls WHERE %(with_trends)s GROUP BY hour
), options AS (
    SELECT jsonb_build_object(
      'models',COALESCE(jsonb_agg(DISTINCT model_name),'[]'::jsonb),
      'users',COALESCE(jsonb_agg(DISTINCT username),'[]'::jsonb),
      'tokens',COALESCE(jsonb_agg(DISTINCT jsonb_build_object('id',token_id,'name',token_name)),'[]'::jsonb)) AS value FROM decoded d
    WHERE EXISTS(SELECT 1 FROM scoped_calls q WHERE q.id=d.id) OR EXISTS(SELECT 1 FROM billed b WHERE b.id=d.id)
)
SELECT jsonb_build_object(
    'rows',COALESCE((SELECT jsonb_agg(to_jsonb(r) ORDER BY request_count DESC,model_name,target)
                        FROM (SELECT * FROM result_rows WHERE grouping=0 LIMIT 1001) r),'[]'::jsonb),
    'totals',(SELECT to_jsonb(r) FROM result_rows r WHERE grouping=3),
    'trends',COALESCE((SELECT jsonb_agg(to_jsonb(h) ORDER BY hour) FROM hourly h),'[]'::jsonb),
    'options',(SELECT value FROM options),
    'source_records',(SELECT COUNT(*) FROM source),
    'unsupported_records',(SELECT COUNT(*) FROM source)-(SELECT COUNT(*) FROM decoded)) AS result
