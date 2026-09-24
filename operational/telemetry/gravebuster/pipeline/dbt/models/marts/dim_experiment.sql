-- STUB (P4/P5 scope): schema only, no rows until the #40 experiment matrix is registered.
SELECT NULL::VARCHAR AS experiment_key, NULL::VARCHAR AS experiment_id, NULL::VARCHAR AS hypothesis_id,
       NULL::VARCHAR AS cell, NULL::VARCHAR AS varied_factor, NULL::VARCHAR AS fixed_factors_hash,
       NULL::TIMESTAMP AS preregistered_at, NULL::VARCHAR AS decision_rule_hash, {{ snap() }}
LIMIT 0
