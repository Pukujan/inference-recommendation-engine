-- one row describing this modeled snapshot (input-addressed id; see pipeline README).
SELECT '{{ var("snapshot_id") }}' AS snapshot_id, '{{ var("inputs_hash") }}' AS inputs_hash,
       now()::TIMESTAMP AS built_at_utc, 'ire-telemetry-model/v1' AS model_catalog_version
