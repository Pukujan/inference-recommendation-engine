-- one row per parent->child launch observed in telemetry (Astra launcher -> codex exec).
WITH p AS (SELECT * FROM {{ ref('int_process_runs') }}), rid AS (SELECT * FROM {{ ref('int_run_ids') }}),
parent AS (SELECT p.*, rid.run_id FROM p JOIN rid USING (run_key, service_name) WHERE rid.role = 'launcher'),
child AS (SELECT p.*, rid.run_id, rid.parent_run_id FROM p JOIN rid USING (run_key, service_name) WHERE rid.role = 'agent_child'),
linked AS (   -- did the child continue the launcher's trace (TRACEPARENT propagation)?
  SELECT c.run_key, c.service_name, count(*) AS spans_in_launcher_trace
  FROM {{ ref('stg_spans') }} c JOIN parent pa ON pa.run_key = c.run_key AND c.trace_id = pa.root_trace_id
  WHERE c.service_name <> 'astra-launcher' GROUP BY 1, 2),
g AS (SELECT run_id, max(gap_s) AS max_gap_s FROM {{ ref('int_gaps') }} GROUP BY 1)
SELECT {{ skey(['pa.run_id', 'ch.run_id']) }} AS hop_id, pa.run_id AS parent_run_id, ch.run_id AS child_run_id, 1 AS hop_depth,
       pa.llm_model_route AS requested_route, coalesce(ch.observed_model, ch.llm_model_route) AS verified_route,
       (pa.llm_model_route IS NOT NULL AND pa.llm_model_route = coalesce(ch.observed_model, ch.llm_model_route)) AS route_match,
       NULL::VARCHAR AS permission_mode_requested, NULL::VARCHAR AS permission_mode_verified,
       (ch.llm_provider IS NOT NULL) AS env_injected,
       coalesce(l.spans_in_launcher_trace, 0) > 0 AS traceparent_linked, coalesce(l.spans_in_launcher_trace, 0) AS spans_in_launcher_trace,
       (ch.first_ns - pa.l_start) / 1e9 AS launch_to_first_event_s,
       (pa.l_end - ch.last_ns) / 1e9 AS child_last_event_to_parent_end_s,
       ch.streaming_observed, g.max_gap_s AS child_max_idle_gap_s,
       pa.start_marker_span_id AS launch_event_id, pa.root_trace_id AS parent_trace_id, pa.root_span_id AS parent_span_id,
       {{ snap() }}
FROM parent pa JOIN child ch ON ch.run_key = pa.run_key
LEFT JOIN linked l ON l.run_key = ch.run_key AND l.service_name = ch.service_name
LEFT JOIN g ON g.run_id = ch.run_id
