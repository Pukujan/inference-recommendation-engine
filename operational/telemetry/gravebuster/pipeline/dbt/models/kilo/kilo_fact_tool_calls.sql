{{ config(materialized='external', format='parquet', options={'compression': 'zstd'}) }}
-- one row per Kilo tool part. Arguments/output are never copied: only *_bytes and *_sha256.
SELECT tool_call_id, run_id, task_id AS kilo_task_id, msg_index, part_index, call_id, tool_name, status, outcome,
       (outcome = 'success') AS success, permission_denied, denial_kind,
       make_timestamp(started_ms * 1000) AS started_at_utc, make_timestamp(ended_ms * 1000) AS ended_at_utc,
       duration_ms, error_message, arguments_bytes, arguments_sha256, output_bytes, output_sha256,
       CASE WHEN child_task_id IS NOT NULL THEN 'kilo:' || child_task_id END AS child_run_id,
       true AS is_primary, 'kilo' AS source, evidence_id, {{ snap() }}
FROM read_parquet('{{ var("clean_root") }}/kilo/kilo_tool_calls.parquet')
