SELECT fetched_at AS ts, rail, rail_slug, rail_label, rail_status, rail_enabled,
       rail_upstream_disabled, active_providers, model_count
FROM src_rails ORDER BY rail, fetched_at
