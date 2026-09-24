-- task ids come from resource task.id; receipt-only runs are all from the pcm-astra-owner launcher
-- directory, so they get task_id 'pcm-astra-owner' with task_source = 'inferred_receipt_dir'.
WITH t AS (
  SELECT task_id, 'telemetry' AS task_source FROM {{ ref('int_process_runs') }}
  UNION ALL SELECT 'pcm-astra-owner', 'inferred_receipt_dir' FROM {{ ref('stg_receipt_runs') }})
SELECT {{ skey(['task_id']) }} AS task_key, task_id, max(task_source) AS task_source,
       NULL::VARCHAR AS task_file_sha256, NULL::INTEGER AS spec_checklist_items, NULL::VARCHAR AS fixture_id,
       NULL::VARCHAR AS workload_class, {{ snap() }}
FROM t GROUP BY task_id
