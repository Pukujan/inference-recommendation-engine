-- Platform status per rail ('*' = overall), one row per distinct status computation.
SELECT status_updated_at AS ts, rail, rail_slug, family, state, availability_now_pct,
       availability_24h_pct, availability_7d_pct, p50_duration_ms, p95_duration_ms,
       avg_ttft_ms_1h, avg_tps_1h, fetched_at
FROM src_status ORDER BY rail, status_updated_at
