SELECT market_ts AS ts, route, rail, family, min_ask_in, min_ask_out, max_ask_in, max_ask_out,
       last_rate_blended, fetched_at
FROM src_market ORDER BY route, market_ts
