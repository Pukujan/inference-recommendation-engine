-- one row per agent/launcher process run. Sources:
--   telemetry  : processes seen in clean spans/logs (launcher root run = correlation id; children = <id>/<service>)
--   receipt    : Astra launcher receipts (data/receipts/<stamp>) -> run_id astra-<stamp>
--   telemetry+receipt : launcher run seen in both (receipt fields fill exit code / session / errors)
-- outcome: completed | failed | killed | launcher_error | no_terminal_event | unknown (no spec_complete
--   claim is possible from telemetry alone). Children inherit the launcher outcome.
WITH p AS (SELECT * FROM {{ ref('int_process_runs') }}), rid AS (SELECT * FROM {{ ref('int_run_ids') }}),
rr AS (SELECT * FROM {{ ref('stg_receipt_runs') }}),
g AS (   -- longest gap between liveness events, and between ANY events (incl. housekeeping)
  SELECT run_id, max(gap_s) AS max_idle_gap_s FROM {{ ref('int_gaps') }} GROUP BY 1),
mr AS (SELECT run_id, count(*) AS n_model_requests, count(*) FILTER (WHERE is_error) AS n_model_errors,
              max(provider_error_code) AS provider_error_code, max(http_status) FILTER (WHERE is_error) AS error_http_status
       FROM {{ ref('fact_model_requests') }} WHERE is_primary GROUP BY 1),
tc AS (SELECT run_id, count(*) AS n_tool_calls, count(*) FILTER (WHERE success = false) AS n_tool_failures
       FROM {{ ref('fact_tool_calls') }} WHERE is_primary GROUP BY 1),
tel AS (
  SELECT rid.run_id, rid.parent_run_id, rid.root_run_id, rid.role, p.run_key, p.service_name,
         CASE WHEN rr.stamp IS NOT NULL AND p.is_launcher THEN 'telemetry+receipt' ELSE 'telemetry' END AS source,
         coalesce(p.llm_model_route, rr.model, p.observed_model) AS route_id, coalesce(p.llm_provider, rr.provider) AS provider,
         coalesce(p.observed_model, p.llm_model_route, rr.model) AS model_id,
         CASE WHEN p.is_launcher THEN 'astra-launcher' ELSE coalesce(p.harness, p.service_name) END AS harness,
         p.service_version AS harness_version,
         CASE WHEN p.is_launcher THEN coalesce(rr.launcher, 'launcher')
              WHEN rid.role = 'agent_child' THEN 'child_of_launcher' ELSE coalesce(p.topology, 'standalone') END AS launch_mode,
         p.task_id, p.ire_issue,
         {{ ts_utc('coalesce(p.l_start, p.first_ns)') }} AS started_at_utc,
         {{ ts_utc('coalesce(p.l_end, p.last_ns)') }} AS ended_at_utc,
         coalesce(p.run_duration_ms / 1000.0, (coalesce(p.l_end, p.last_ns) - coalesce(p.l_start, p.first_ns)) / 1e9) AS wall_s,
         coalesce(p.exit_code, rr.exit_code) AS exit_code, coalesce(p.killed, rr.killed) AS killed, p.timed_out,
         p.kill_signal, p.status_message, p.stderr_tail_text,
         p.span_count, p.noise_span_count, p.log_count, p.trace_count,
         coalesce(p.tokens_in, rr.tokens_in) AS tokens_in, coalesce(p.tokens_out, rr.tokens_out) AS tokens_out,
         coalesce(p.tokens_cached, rr.tokens_cached) AS tokens_cached, coalesce(p.tokens_reasoning, rr.tokens_reasoning) AS tokens_reasoning,
         coalesce(p.conversation_id, rr.session_id) AS session_id, p.streaming_observed,
         rr.stamp AS receipt_stamp, rr.receipt_sha256, rr.outcome AS receipt_outcome, rr.provider_error_code AS receipt_provider_error_code,
         p.root_trace_id, p.signal_trace_ids AS trace_ids,
         list_filter([p.root_span_id, p.start_marker_span_id], x -> x IS NOT NULL) AS evidence_span_ids
  FROM p JOIN rid USING (run_key, service_name)
  LEFT JOIN rr ON rr.run_id = p.run_key AND p.is_launcher),
rec_only AS (
  SELECT rr.run_id, NULL AS parent_run_id, rr.run_id AS root_run_id, 'launcher' AS role, rr.run_id AS run_key,
         'astra-launcher' AS service_name, 'receipt' AS source, rr.model AS route_id, rr.provider, rr.model AS model_id,
         'astra-launcher' AS harness, NULL AS harness_version, coalesce(rr.launcher, 'launcher') AS launch_mode,
         'pcm-astra-owner' AS task_id, NULL AS ire_issue, rr.started_at_utc, rr.ended_at_utc, rr.duration_s AS wall_s,
         rr.exit_code, rr.killed, NULL::BOOLEAN AS timed_out, rr.exit_code_raw AS kill_signal, rr.result AS status_message,
         rr.stderr_first_line AS stderr_tail_text, 0 AS span_count, 0 AS noise_span_count, 0 AS log_count, 0 AS trace_count,
         rr.tokens_in, rr.tokens_out, rr.tokens_cached, rr.tokens_reasoning, rr.session_id, NULL::BOOLEAN AS streaming_observed,
         rr.stamp AS receipt_stamp, rr.receipt_sha256, rr.outcome AS receipt_outcome, rr.provider_error_code AS receipt_provider_error_code,
         rr.trace_id AS root_trace_id, CASE WHEN rr.trace_id IS NOT NULL THEN [rr.trace_id] END AS trace_ids,
         []::VARCHAR[] AS evidence_span_ids
  FROM rr WHERE rr.run_id NOT IN (SELECT run_id FROM tel)),
u AS (SELECT * FROM tel UNION ALL BY NAME SELECT * FROM rec_only),
o AS (
  SELECT u.*,
         CASE WHEN receipt_outcome IS NOT NULL AND receipt_outcome NOT IN ('unknown') THEN receipt_outcome
              WHEN killed THEN 'killed'
              WHEN exit_code = 0 THEN 'completed'
              WHEN exit_code IS NOT NULL THEN 'failed'
              ELSE NULL END AS own_outcome
  FROM u)
SELECT o.run_id, o.parent_run_id, o.root_run_id, o.role, o.source, o.run_key, o.service_name,
       {{ skey(['o.route_id', 'o.provider']) }} AS route_key, {{ skey(['o.model_id']) }} AS model_key,
       {{ skey(['o.harness', 'o.harness_version', 'o.launch_mode']) }} AS harness_key, {{ skey(['o.task_id']) }} AS task_key,
       CASE WHEN o.ire_issue IS NOT NULL THEN {{ skey(["'Pukujan/inference-recommendation-engine'", 'o.ire_issue']) }} END AS gh_key,
       NULL::VARCHAR AS policy_key, NULL::VARCHAR AS experiment_key,
       o.route_id, o.model_id, o.harness, o.harness_version, o.launch_mode, o.task_id,
       o.started_at_utc, o.ended_at_utc, o.wall_s,
       coalesce(o.own_outcome, par.own_outcome, 'unknown') AS outcome,
       o.exit_code, o.killed, o.timed_out, o.kill_signal, o.status_message, o.stderr_tail_text,
       coalesce(mr.n_model_requests, 0) AS n_model_requests, coalesce(mr.n_model_errors, 0) AS n_model_errors,
       coalesce(mr.provider_error_code, o.receipt_provider_error_code) AS provider_error_code, mr.error_http_status,
       coalesce(tc.n_tool_calls, 0) AS n_tool_calls, coalesce(tc.n_tool_failures, 0) AS n_tool_failures,
       o.tokens_in, o.tokens_out, o.tokens_cached, o.tokens_reasoning, NULL::DOUBLE AS cost,
       o.span_count, o.noise_span_count, o.log_count, o.trace_count, g.max_idle_gap_s,
       NULL::INTEGER AS resumes, NULL::INTEGER AS retries, NULL::BOOLEAN AS completion_claimed,
       o.session_id, o.streaming_observed, o.receipt_stamp, o.receipt_sha256,
       o.root_trace_id, o.trace_ids, o.evidence_span_ids, {{ snap() }}
FROM o
LEFT JOIN o par ON par.run_id = o.parent_run_id
LEFT JOIN g ON g.run_id = o.run_id
LEFT JOIN mr ON mr.run_id = o.run_id
LEFT JOIN tc ON tc.run_id = o.run_id
