-- one row per tool execution.
-- telemetry: codex.tool_result log (call_id, tool, duration, success) + codex.tool_decision (approval)
--   + exec_command span (aborted). Evidence = the log's trace/span (dispatch_tool_call_with_terminal_outcome).
-- receipts: codex exec item.completed/command_execution (exit code, status); is_primary=false when the
--   same run also has telemetry tool rows.
WITH l AS (SELECT * FROM {{ ref('stg_logs') }}),
rid AS (SELECT * FROM {{ ref('int_run_ids') }}),
res AS (
  SELECT * FROM l WHERE event = 'codex.tool_result'
  QUALIFY row_number() OVER (PARTITION BY run_key, call_id ORDER BY time_unix_nano) = 1),
dec AS (
  SELECT run_key, call_id, max(tool_decision) AS decision, max(decision_source) AS decision_source
  FROM l WHERE event = 'codex.tool_decision' GROUP BY 1, 2),
ex AS (
  SELECT run_key, call_id, bool_or(tool_aborted) AS aborted, max(outcome_attr) AS exec_outcome
  FROM {{ ref('stg_spans') }} WHERE call_id IS NOT NULL GROUP BY 1, 2),
tel AS (
  SELECT 'tel:' || res.run_key || ':' || res.call_id AS tool_call_id, rid.run_id, 'telemetry' AS source, true AS is_primary,
         res.call_id, res.tool_name, res.tool_namespace AS tool_type,
         {{ ts_utc('res.time_unix_nano') }} AS finished_at_utc, res.duration_ms,
         CASE WHEN res.success THEN 'success' WHEN res.success = false THEN 'failure' ELSE 'unknown' END AS status,
         res.success, NULL::INTEGER AS exit_code, dec.decision, (dec.decision IS NOT NULL AND dec.decision <> 'approved') AS permission_denied,
         ex.aborted AS killed_by_timeout_or_abort, ex.exec_outcome, res.output_truncated, res.output_bytes, res.arguments_sha256,
         NULL::INTEGER AS timeout_ms_configured, res.trace_id, res.span_id, NULL AS receipt_stamp
  FROM res JOIN rid ON rid.run_key = res.run_key AND rid.service_name = res.service_name
  LEFT JOIN dec ON dec.run_key = res.run_key AND dec.call_id = res.call_id
  LEFT JOIN ex ON ex.run_key = res.run_key AND ex.call_id = res.call_id),
rec AS (
  SELECT 'rcpt:' || e.stamp || ':' || coalesce(e.item_id, CAST(e.seq AS VARCHAR)) AS tool_call_id,
         'astra-' || e.stamp AS run_id, 'receipt' AS source,
         NOT EXISTS (SELECT 1 FROM tel WHERE tel.run_id LIKE 'astra-' || e.stamp || '%') AS is_primary,
         e.item_id AS call_id, 'command_execution' AS tool_name, 'shell' AS tool_type, NULL::TIMESTAMP AS finished_at_utc,
         NULL::DOUBLE AS duration_ms, e.status, e.exit_code = 0 AS success, e.exit_code, NULL AS decision,
         NULL::BOOLEAN AS permission_denied, NULL::BOOLEAN AS killed_by_timeout_or_abort, NULL AS exec_outcome,
         NULL::BOOLEAN AS output_truncated, e.text_bytes AS output_bytes, e.command_sha256 AS arguments_sha256,
         NULL::INTEGER AS timeout_ms_configured, NULL AS trace_id, NULL AS span_id, e.stamp AS receipt_stamp
  FROM {{ ref('stg_receipt_events') }} e
  WHERE e.type = 'item.completed' AND e.item_type = 'command_execution')
SELECT *, {{ snap() }} FROM (SELECT * FROM tel UNION ALL BY NAME SELECT * FROM rec)
