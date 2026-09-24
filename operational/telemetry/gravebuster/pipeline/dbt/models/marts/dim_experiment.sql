-- Schema only. Rows are written by atpipe/experiments.py (one per experiment cell) from trial
-- manifests (#40 M0.5). Keep in sync with experiments.DIM_COLUMNS.
SELECT NULL::VARCHAR AS experiment_key, NULL::VARCHAR AS experiment_id, NULL::VARCHAR AS hypothesis_id,
       NULL::VARCHAR AS cell, NULL::VARCHAR AS varied_factor, NULL::VARCHAR AS fixed_factors_hash,
       NULL::TIMESTAMP AS preregistered_at, NULL::VARCHAR AS decision_rule_hash, {{ snap() }},
       NULL::VARCHAR AS cell_value, NULL::VARCHAR AS manifest_file, NULL::VARCHAR AS manifest_sha256
LIMIT 0
