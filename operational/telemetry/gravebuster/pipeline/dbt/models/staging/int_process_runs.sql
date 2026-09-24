{{ config(materialized="table") }}
-- one row per (run_key, service) = one telemetry-emitting process
WITH sp AS (
  SELECT run_key, service_name,
         min(start_unix_nano) AS first_ns, max(end_unix_nano) AS last_ns,
         count(*) AS span_count, count(*) FILTER (WHERE is_noise) AS noise_span_count,
         count(DISTINCT trace_id) AS trace_count,
         list(DISTINCT trace_id ORDER BY trace_id) FILTER (WHERE NOT is_noise) AS signal_trace_ids,
         mode(harness) AS harness, max(service_version) AS service_version, max(topology) AS topology,
         max(task_id) AS task_id, max(ire_issue) AS ire_issue, max(llm_provider) AS llm_provider,
         max(llm_model_route) AS llm_model_route, max(llm_base_url_host) AS llm_base_url_host,
         max(correlation_id) AS correlation_id, mode(model) FILTER (WHERE model IS NOT NULL) AS span_model,
         max(wire_api) AS wire_api, min(conversation_id) AS conversation_id,
         max(host_name) AS host_name, max(deployment_env) AS deployment_env,
         sum(tokens_in) AS tokens_in, sum(tokens_out) AS tokens_out, sum(tokens_cache_read) AS tokens_cached,
         sum(tokens_reasoning) AS tokens_reasoning
  FROM {{ ref('stg_spans') }} GROUP BY 1, 2),
lg AS (
  SELECT run_key, service_name, count(*) AS log_count, min(time_unix_nano) AS first_log_ns,
         max(time_unix_nano) AS last_log_ns, mode(model) FILTER (WHERE model IS NOT NULL) AS log_model,
         max(llm_model_route) AS llm_model_route, max(llm_provider) AS llm_provider,
         max(harness) AS harness, max(task_id) AS task_id, max(service_version) AS service_version,
         min(conversation_id) AS conversation_id,
         bool_or(event = 'codex.sse_event') AS streaming_observed
  FROM {{ ref('stg_logs') }} GROUP BY 1, 2),
launcher AS (   -- launcher root span (astra.<action>, not the .start marker)
  SELECT run_key, service_name,
         arg_min(span_id, start_unix_nano) AS root_span_id, arg_min(trace_id, start_unix_nano) AS root_trace_id,
         min(start_unix_nano) AS l_start, max(end_unix_nano) AS l_end,
         max(run_exit_code) AS exit_code, bool_or(run_killed) AS killed, bool_or(run_timed_out) AS timed_out,
         max(run_signal) AS kill_signal, max(run_duration_ms) AS run_duration_ms, max(status_code) AS status_code,
         max(status_message) AS status_message, max(run_action) AS run_action,
         max(left(run_stderr_tail_text, 400)) AS stderr_tail_text
  FROM {{ ref('stg_spans') }}
  WHERE is_root AND run_exit_code IS NOT NULL OR (is_root AND run_action IS NOT NULL AND run_phase IS NULL)
  GROUP BY 1, 2),
starts AS (
  SELECT run_key, service_name, arg_min(span_id, start_unix_nano) AS start_marker_span_id
  FROM {{ ref('stg_spans') }} WHERE run_phase = 'start' GROUP BY 1, 2)
SELECT
  coalesce(sp.run_key, lg.run_key) AS run_key,
  coalesce(sp.service_name, lg.service_name) AS service_name,
  least(sp.first_ns, lg.first_log_ns) AS first_ns,
  greatest(sp.last_ns, lg.last_log_ns) AS last_ns,
  coalesce(sp.span_count, 0) AS span_count, coalesce(sp.noise_span_count, 0) AS noise_span_count,
  coalesce(lg.log_count, 0) AS log_count, coalesce(sp.trace_count, 0) AS trace_count, sp.signal_trace_ids,
  coalesce(sp.harness, lg.harness) AS harness, coalesce(sp.service_version, lg.service_version) AS service_version,
  sp.topology, coalesce(sp.task_id, lg.task_id) AS task_id, sp.ire_issue,
  coalesce(sp.llm_provider, lg.llm_provider) AS llm_provider,
  coalesce(sp.llm_model_route, lg.llm_model_route) AS llm_model_route, sp.llm_base_url_host,
  sp.correlation_id, coalesce(sp.span_model, lg.log_model) AS observed_model, sp.wire_api,
  coalesce(sp.conversation_id, lg.conversation_id) AS conversation_id, sp.host_name, sp.deployment_env,
  sp.tokens_in, sp.tokens_out, sp.tokens_cached, sp.tokens_reasoning,
  coalesce(lg.streaming_observed, false) AS streaming_observed,
  l.root_span_id, l.root_trace_id, l.l_start, l.l_end, l.exit_code, l.killed, l.timed_out, l.kill_signal,
  l.run_duration_ms, l.status_code, l.status_message, l.run_action, l.stderr_tail_text,
  st.start_marker_span_id,
  (coalesce(sp.service_name, lg.service_name) = 'astra-launcher') AS is_launcher
FROM sp FULL OUTER JOIN lg ON sp.run_key = lg.run_key AND sp.service_name = lg.service_name
LEFT JOIN launcher l ON l.run_key = coalesce(sp.run_key, lg.run_key) AND l.service_name = coalesce(sp.service_name, lg.service_name)
LEFT JOIN starts st ON st.run_key = coalesce(sp.run_key, lg.run_key) AND st.service_name = coalesce(sp.service_name, lg.service_name)
