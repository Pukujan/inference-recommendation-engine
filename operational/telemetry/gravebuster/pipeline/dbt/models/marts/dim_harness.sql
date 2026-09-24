-- harness x version x launch mode. Telemetry processes plus receipt-only launcher runs.
WITH h AS (
  SELECT CASE WHEN p.is_launcher THEN 'astra-launcher' ELSE coalesce(p.harness, p.service_name) END AS harness,
         p.service_version AS version,
         CASE WHEN p.is_launcher THEN coalesce(rr.launcher, 'launcher')
              WHEN r.role = 'agent_child' THEN 'child_of_launcher' ELSE coalesce(p.topology, 'standalone') END AS launch_mode,
         p.topology
  FROM {{ ref('int_process_runs') }} p JOIN {{ ref('int_run_ids') }} r USING (run_key, service_name)
  LEFT JOIN {{ ref('stg_receipt_runs') }} rr ON rr.run_id = p.run_key AND p.is_launcher
  UNION ALL
  SELECT 'astra-launcher', NULL, coalesce(launcher, 'launcher'), 'main' FROM {{ ref('stg_receipt_runs') }})
SELECT DISTINCT {{ skey(['harness', 'version', 'launch_mode']) }} AS harness_key, harness, version, launch_mode,
       topology, NULL::BOOLEAN AS tty, NULL::VARCHAR AS permission_mode, NULL::VARCHAR AS streaming_mode,
       NULL::VARCHAR AS config_hash, {{ snap() }}
FROM h
QUALIFY row_number() OVER (PARTITION BY harness, version, launch_mode ORDER BY topology NULLS LAST) = 1
