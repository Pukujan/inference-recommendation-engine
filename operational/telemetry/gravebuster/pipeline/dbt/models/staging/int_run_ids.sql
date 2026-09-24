{{ config(materialized="table") }}
-- run id assignment: the launcher process owns the run_key (root run); other processes are children
WITH p AS (SELECT * FROM {{ ref('int_process_runs') }}),
has_launcher AS (SELECT DISTINCT run_key FROM p WHERE is_launcher)
SELECT p.run_key, p.service_name,
       CASE WHEN p.is_launcher THEN p.run_key ELSE p.run_key || '/' || p.service_name END AS run_id,
       CASE WHEN NOT p.is_launcher AND h.run_key IS NOT NULL THEN p.run_key END AS parent_run_id,
       CASE WHEN h.run_key IS NOT NULL THEN p.run_key
            ELSE p.run_key || '/' || p.service_name END AS root_run_id,
       CASE WHEN p.is_launcher THEN 'launcher' WHEN h.run_key IS NOT NULL THEN 'agent_child' ELSE 'agent' END AS role
FROM p LEFT JOIN has_launcher h USING (run_key)
