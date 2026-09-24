{{ config(materialized='external', format='parquet', options={'compression': 'zstd'}) }}
-- one row per Kilo session (task). Sub-agent sessions (Kilo `task` tool children) are separate runs
-- linked by parent_run_id / root_run_id. Source: clean/kilo (atpipe/ingest_kilo.py), InferHub scope only.
WITH r AS (SELECT * FROM read_parquet('{{ var("clean_root") }}/kilo/kilo_runs.parquet')),
tree AS (
  WITH RECURSIVE t(run_id, root_run_id, depth) AS (
    SELECT run_id, run_id, 0 FROM r WHERE parent_task_id IS NULL OR 'kilo:' || parent_task_id NOT IN (SELECT run_id FROM r)
    UNION ALL
    SELECT c.run_id, t.root_run_id, t.depth + 1 FROM r c JOIN t ON 'kilo:' || c.parent_task_id = t.run_id)
  SELECT * FROM t),
mr AS (SELECT run_id, count(*) AS n_model_requests, count(*) FILTER (WHERE is_error) AS n_model_errors,
              max(provider_error_code) AS provider_error_code, max(http_status) FILTER (WHERE is_error) AS error_http_status
       FROM {{ ref('kilo_fact_model_requests') }} GROUP BY 1),
tc AS (SELECT run_id, count(*) AS n_tool_calls, count(*) FILTER (WHERE outcome IN ('error', 'denied')) AS n_tool_failures,
              count(*) FILTER (WHERE permission_denied) AS n_permission_denials
       FROM {{ ref('kilo_fact_tool_calls') }} GROUP BY 1),
g AS (SELECT run_id, max(gap_s) AS max_idle_gap_s FROM {{ ref('kilo_fact_idle_gaps') }} GROUP BY 1)
SELECT r.run_id,
       CASE WHEN r.parent_task_id IS NOT NULL THEN 'kilo:' || r.parent_task_id END AS parent_run_id,
       coalesce(tree.root_run_id, r.run_id) AS root_run_id,
       CASE WHEN r.parent_task_id IS NULL THEN 'kilo_session' ELSE 'kilo_subagent' END AS role,
       coalesce(tree.depth, 0) AS depth, 'kilo' AS source, r.task_id AS run_key, 'kilo' AS service_name,
       {{ skey(["r.provider_id || '/' || r.model_id", 'r.provider_id']) }} AS route_key, {{ skey(['r.model_id']) }} AS model_key,
       {{ skey(["'kilo'", 'r.harness_version', 'r.agent']) }} AS harness_key,
       {{ skey(['r.project']) }} AS task_key,
       r.provider_id || '/' || r.model_id AS route_id, r.provider_id AS provider, r.model_id, r.model_variant,
       'kilo' AS harness, r.harness_version, r.agent AS launch_mode,
       r.project AS task_id, r.task_id AS kilo_task_id, r.parent_task_id AS kilo_parent_task_id,
       r.title, r.directory, r.scope, r.scope_reason,
       make_timestamp(r.started_ms * 1000) AS started_at_utc, make_timestamp(r.ended_ms * 1000) AS ended_at_utc,
       (r.ended_ms - r.started_ms) / 1000.0 AS wall_s, r.outcome, r.status_message,
       coalesce(mr.n_model_requests, 0) AS n_model_requests, coalesce(mr.n_model_errors, 0) AS n_model_errors,
       mr.provider_error_code, mr.error_http_status,
       coalesce(tc.n_tool_calls, 0) AS n_tool_calls, coalesce(tc.n_tool_failures, 0) AS n_tool_failures,
       coalesce(tc.n_permission_denials, 0) AS n_permission_denials,
       r.tokens_in, r.tokens_out, r.tokens_cached, r.tokens_reasoning, r.tokens_cache_write, r.cost,
       g.max_idle_gap_s, r.n_messages, r.task_id AS session_id, r.archive_sha256,
       [r.evidence_id] AS evidence_ids, {{ snap() }}
FROM r LEFT JOIN tree USING (run_id) LEFT JOIN mr USING (run_id) LEFT JOIN tc USING (run_id) LEFT JOIN g USING (run_id)
