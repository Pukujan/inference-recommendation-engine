-- gaps >= var('idle_gap_min_s') between consecutive liveness events per run (plus the longest gap of
-- every run, gap_rank = 1). Evidence = the trace/span ids of the events on both sides of the gap.
SELECT {{ skey(['run_id', 'gap_start_ns', 'gap_end_ns']) }} AS gap_id, run_id, gap_rank,
       {{ ts_utc('gap_start_ns') }} AS gap_start_utc, {{ ts_utc('gap_end_ns') }} AS gap_end_utc, gap_s,
       CASE WHEN is_last_event THEN 'run_end' ELSE 'event' END AS ended_by,
       before_event, after_event, before_trace_id, before_span_id, after_trace_id, after_span_id,
       NULL::BOOLEAN AS process_cpu_active, {{ snap() }}
FROM {{ ref('int_gaps') }}
WHERE gap_rank = 1 OR gap_s >= {{ var('idle_gap_min_s') }}
