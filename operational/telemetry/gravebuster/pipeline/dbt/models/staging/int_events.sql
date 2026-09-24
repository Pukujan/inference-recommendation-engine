-- every timestamped telemetry event (span start, span end, log record) with its process run
WITH spans AS (SELECT * FROM {{ ref('stg_spans') }})
SELECT run_key, service_name, start_unix_nano AS t_ns, 'span_start:' || name AS event, trace_id, span_id,
       counts_for_liveness, is_noise FROM spans WHERE start_unix_nano IS NOT NULL
UNION ALL
SELECT run_key, service_name, end_unix_nano, 'span_end:' || name, trace_id, span_id,
       counts_for_liveness, is_noise FROM spans WHERE end_unix_nano IS NOT NULL
UNION ALL
SELECT run_key, service_name, time_unix_nano, 'log:' || coalesce(event, event_name_raw, 'log'), trace_id, span_id,
       counts_for_liveness, is_noise FROM {{ ref('stg_logs') }} WHERE time_unix_nano IS NOT NULL
