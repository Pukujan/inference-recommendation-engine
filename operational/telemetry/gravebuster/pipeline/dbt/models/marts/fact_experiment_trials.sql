-- Schema only. Rows are written by atpipe/experiments.py from trial manifests
-- (experiments/trial-manifest.schema.json, #40 M0.5). Keep in sync with experiments.TRIAL_COLUMNS.
SELECT NULL::VARCHAR AS trial_id, NULL::VARCHAR AS experiment_key, NULL::VARCHAR AS run_id, NULL::VARCHAR AS cell,
       NULL::VARCHAR AS block, NULL::BIGINT AS randomization_seed, NULL::BOOLEAN AS success,
       NULL::BOOLEAN AS early_stop, NULL::INTEGER AS kills_at_cap, {{ snap() }},
       NULL::VARCHAR AS experiment_id, NULL::VARCHAR AS receipt_stamp, NULL::VARCHAR AS run_outcome,
       NULL::BOOLEAN AS run_linked, NULL::BOOLEAN AS planned, NULL::INTEGER AS incident_count,
       NULL::VARCHAR AS manifest_sha256
LIMIT 0
