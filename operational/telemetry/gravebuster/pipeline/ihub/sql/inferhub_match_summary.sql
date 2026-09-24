SELECT route, count(*) AS telemetry_requests,
       count(*) FILTER (WHERE tokens_in IS NOT NULL) AS with_tokens,
       count(*) FILTER (WHERE match_type = 'exact_tokens') AS matched_exact_tokens,
       count(*) FILTER (WHERE match_type = 'time_error') AS matched_time_error,
       count(*) FILTER (WHERE match_type = 'unmatched') AS unmatched,
       round(100.0 * count(*) FILTER (WHERE match_type = 'exact_tokens')
             / nullif(count(*) FILTER (WHERE tokens_in IS NOT NULL), 0), 2) AS exact_match_pct,
       round(100.0 * count(*) FILTER (WHERE match_type <> 'unmatched') / count(*), 2) AS any_match_pct,
       round(median(dt_s), 3) AS median_dt_s, round(max(abs(dt_s)), 3) AS max_abs_dt_s,
       sum(billed_cost_usdc) AS matched_billed_usdc
FROM inferhub_request_match GROUP BY ALL ORDER BY route
