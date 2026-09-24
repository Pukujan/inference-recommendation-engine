SELECT fetched_at AS ts, balance_usdc::DECIMAL(18,6) AS balance_usdc,
       balance_updated_at::TIMESTAMPTZ::TIMESTAMP AS balance_updated_at,
       window_tz, window_since::TIMESTAMPTZ::TIMESTAMP AS day_start_utc,
       window_requests_ok AS today_requests_ok, window_prompt_tokens AS today_prompt_tokens,
       window_completion_tokens AS today_completion_tokens,
       window_spend_usdc::DECIMAL(18,6) AS today_spend_usdc,
       all_time_spend_usdc::DECIMAL(18,6) AS all_time_spend_usdc_lagging
FROM src_balance ORDER BY fetched_at
