-- STUB (#40 M0.5 experiment matrix not registered yet). Schema per design doc 5.3; no rows yet.
SELECT NULL::VARCHAR AS trial_id, NULL::VARCHAR AS experiment_key, NULL::VARCHAR AS run_id, NULL::VARCHAR AS cell,
       NULL::VARCHAR AS block, NULL::BIGINT AS randomization_seed, NULL::BOOLEAN AS success,
       NULL::BOOLEAN AS early_stop, NULL::INTEGER AS kills_at_cap, {{ snap() }}
LIMIT 0
