{{ config(materialized='external', format='parquet', options={'compression': 'zstd'}) }}
-- one row per parent Kilo session -> sub-agent session launched by the `task` tool.
-- Children also carry parent_run_id in kilo_fact_runs; a child whose `task` part was not found still
-- gets a hop from its session parent_id (launch_evidence_id NULL).
WITH h AS (SELECT * FROM read_parquet('{{ var("clean_root") }}/kilo/kilo_hops.parquet')),
r AS (SELECT * FROM read_parquet('{{ var("clean_root") }}/kilo/kilo_runs.parquet')),
edges AS (
  SELECT parent_run_id, child_run_id, launch_evidence_id, launch_ms, subagent_type, background FROM h
  UNION ALL
  SELECT 'kilo:' || c.parent_task_id, c.run_id, NULL, NULL, NULL, NULL FROM r c
  WHERE c.parent_task_id IS NOT NULL AND c.run_id NOT IN (SELECT child_run_id FROM h)),
e AS (SELECT * FROM edges QUALIFY row_number() OVER (PARTITION BY parent_run_id, child_run_id ORDER BY launch_evidence_id NULLS LAST) = 1),
g AS (SELECT run_id, max(gap_s) AS max_gap_s FROM {{ ref('kilo_fact_idle_gaps') }} GROUP BY 1)
SELECT {{ skey(['e.parent_run_id', 'e.child_run_id']) }} AS hop_id, e.parent_run_id, e.child_run_id, 1 AS hop_depth,
       pa.provider_id || '/' || pa.model_id AS requested_route, ch.provider_id || '/' || ch.model_id AS verified_route,
       (pa.provider_id || '/' || pa.model_id) = (ch.provider_id || '/' || ch.model_id) AS route_match,
       e.subagent_type, e.background,
       (ch.started_ms - e.launch_ms) / 1000.0 AS launch_to_first_event_s,
       (pa.ended_ms - ch.ended_ms) / 1000.0 AS child_last_event_to_parent_end_s,
       ch.outcome AS child_outcome, g.max_gap_s AS child_max_idle_gap_s,
       e.launch_evidence_id AS launch_event_id, 'kilo' AS source, {{ snap() }}
FROM e LEFT JOIN r pa ON pa.run_id = e.parent_run_id LEFT JOIN r ch ON ch.run_id = e.child_run_id
LEFT JOIN g ON g.run_id = e.child_run_id
