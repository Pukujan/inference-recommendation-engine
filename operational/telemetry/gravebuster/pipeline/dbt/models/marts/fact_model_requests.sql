-- one row per LLM HTTP call.
-- telemetry: Codex span model_client.stream_responses_api -> child responses.stream_request -> child
--   endpoint_session.stream_encoded_json_with, which carries the codex.api_request log (http status, error).
--   Token usage: the handle_responses span with gen_ai.usage.* that falls in [request start, next request start)
--   of the same run (ASOF join; Codex does not link usage to the request span).
-- receipts: one row per codex exec turn.failed (InferHub error code e.g. 11133 + request-shape features).
-- is_primary = false for receipt rows of runs that also have telemetry requests (avoid double counting).
WITH s AS (SELECT * FROM {{ ref('stg_spans') }}),
rid AS (SELECT * FROM {{ ref('int_run_ids') }}),
req AS (
  SELECT m.run_key, m.service_name, m.trace_id, m.span_id, m.start_unix_nano, m.end_unix_nano, m.duration_ms,
         m.model, m.wire_api, m.api_path, m.llm_model_route, m.llm_provider, e.span_id AS endpoint_span_id,
         lead(m.start_unix_nano) OVER (PARTITION BY m.run_key ORDER BY m.start_unix_nano) AS next_start_ns
  FROM s m
  LEFT JOIN s r ON r.parent_span_id = m.span_id AND r.trace_id = m.trace_id AND r.name = 'responses.stream_request'
  LEFT JOIN s e ON e.parent_span_id = r.span_id AND e.trace_id = r.trace_id AND e.name = 'endpoint_session.stream_encoded_json_with'
  WHERE m.name = 'model_client.stream_responses_api'),
api AS (
  SELECT span_id, trace_id, arg_max(http_status, time_unix_nano) AS http_status, bool_or(success) AS success,
         max(error_message) AS error_message, max(duration_ms) AS api_duration_ms, max(attempt) AS attempt
  FROM {{ ref('stg_logs') }} WHERE event = 'codex.api_request' AND endpoint = '/responses' GROUP BY 1, 2),
usage AS (
  SELECT r.span_id AS req_span_id, sum(u.tokens_in) AS tokens_in, sum(u.tokens_out) AS tokens_out,
         sum(u.tokens_cache_read) AS tokens_cached, sum(u.tokens_reasoning) AS tokens_reasoning
  FROM (SELECT * FROM s WHERE name = 'handle_responses' AND tokens_in IS NOT NULL) u
  ASOF JOIN req r ON u.run_key = r.run_key AND u.start_unix_nano >= r.start_unix_nano
  WHERE r.next_start_ns IS NULL OR u.start_unix_nano < r.next_start_ns
  GROUP BY 1),
tel AS (
  SELECT 'tel:' || req.trace_id || ':' || req.span_id AS request_id, rid.run_id, 'telemetry' AS source, true AS is_primary,
         req.trace_id, req.span_id, {{ ts_utc('req.start_unix_nano') }} AS started_at_utc, req.duration_ms,
         coalesce(req.llm_model_route, req.model) AS route_id, req.llm_provider AS provider, req.model AS model_id,
         req.wire_api AS api_style, api.http_status, api.success,
         CASE WHEN api.http_status >= 400 OR api.error_message IS NOT NULL THEN true ELSE false END AS is_error,
         NULL::INTEGER AS provider_error_code,
         CASE WHEN api.http_status = 400 THEN 'http_400' WHEN api.error_message IS NOT NULL THEN 'transport' END AS error_type,
         api.error_message, api.attempt, NULL::DOUBLE AS ttft_ms,
         u.tokens_in, u.tokens_out, u.tokens_cached, u.tokens_reasoning,
         NULL::INTEGER AS n_messages, NULL::INTEGER AS n_tools, NULL::INTEGER AS n_empty_assistant_text_with_tool_calls,
         NULL::INTEGER AS n_tool_schema_additional_properties, NULL::INTEGER AS max_tool_description_bytes,
         NULL::INTEGER AS max_message_bytes, NULL::VARCHAR AS request_shape_hash, NULL::VARCHAR AS provider_request_id,
         req.endpoint_span_id AS evidence_endpoint_span_id, NULL::VARCHAR AS receipt_stamp
  FROM req JOIN rid ON rid.run_key = req.run_key AND rid.service_name = req.service_name
  LEFT JOIN api ON api.span_id = req.endpoint_span_id AND api.trace_id = req.trace_id
  LEFT JOIN usage u ON u.req_span_id = req.span_id),
orphan_api AS (   -- /responses calls whose request span never reached the collector (e.g. the failing call)
  SELECT 'tellog:' || l.log_key AS request_id, rid.run_id, 'telemetry_log' AS source, true AS is_primary,
         l.trace_id, l.span_id, {{ ts_utc('l.time_unix_nano') }} AS started_at_utc, l.duration_ms,
         coalesce(l.llm_model_route, l.model) AS route_id, l.llm_provider AS provider, l.model AS model_id,
         NULL AS api_style, l.http_status, l.success,
         (l.http_status >= 400 OR l.error_message IS NOT NULL) AS is_error, NULL::INTEGER AS provider_error_code,
         CASE WHEN l.http_status = 400 THEN 'http_400' WHEN l.error_message IS NOT NULL THEN 'transport' END AS error_type,
         l.error_message, l.attempt, NULL::DOUBLE AS ttft_ms,
         l.span_id AS evidence_endpoint_span_id
  FROM {{ ref('stg_logs') }} l JOIN rid ON rid.run_key = l.run_key AND rid.service_name = l.service_name
  WHERE l.event = 'codex.api_request' AND l.endpoint = '/responses'
    AND NOT EXISTS (SELECT 1 FROM req WHERE req.endpoint_span_id = l.span_id AND req.trace_id = l.trace_id)),
telall AS (SELECT * FROM tel UNION ALL BY NAME SELECT * FROM orphan_api),
rec AS (
  SELECT 'rcpt:' || e.stamp || ':' || e.seq AS request_id, 'astra-' || e.stamp AS run_id, 'receipt' AS source,
         -- receipt rows are secondary only if telemetry already recorded a failed call for that run
         NOT EXISTS (SELECT 1 FROM telall t WHERE t.run_id LIKE 'astra-' || e.stamp || '%' AND t.http_status >= 400) AS is_primary,
         NULL AS trace_id, NULL AS span_id, NULL::TIMESTAMP AS started_at_utc, NULL::DOUBLE AS duration_ms,
         rr.model AS route_id, rr.provider, rr.model AS model_id, NULL AS api_style,
         e.http_status, false AS success, true AS is_error, e.provider_error_code, e.error_type,
         NULL AS error_message, NULL::INTEGER AS attempt, NULL::DOUBLE AS ttft_ms,
         NULL::BIGINT AS tokens_in, NULL::BIGINT AS tokens_out, NULL::BIGINT AS tokens_cached, NULL::BIGINT AS tokens_reasoning,
         e.msgs AS n_messages, e.tools AS n_tools, e.empty_content AS n_empty_assistant_text_with_tool_calls,
         e.additional_properties AS n_tool_schema_additional_properties, e.max_tool_desc_bytes AS max_tool_description_bytes,
         e.max_msg_bytes AS max_message_bytes, e.request_shape_hash, e.request_id AS provider_request_id,
         NULL AS evidence_endpoint_span_id, e.stamp AS receipt_stamp
  FROM {{ ref('stg_receipt_events') }} e JOIN {{ ref('stg_receipt_runs') }} rr USING (stamp)
  WHERE e.type = 'turn.failed')
SELECT {{ skey(['route_id', 'provider']) }} AS route_key, {{ skey(['model_id']) }} AS model_key, *, {{ snap() }}
FROM (SELECT * FROM telall UNION ALL BY NAME SELECT * FROM rec)
