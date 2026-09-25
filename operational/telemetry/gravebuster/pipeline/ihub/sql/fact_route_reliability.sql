-- Rolling reliability per route (<rail>/<model>) and window (1h / 24h / 7d), ending at the request
-- log coverage end (window_end = last successful log fetch from state/logs_state.json, else max
-- fetched_at of the billing rows), so a window is never cut short by the 15-min log cadence.
-- One row per route x window with >= 1 request.
-- error_type / error_class come from fact_request_outcome (rules in ihub/errclass.py): 11133 and
-- uncoded 400s on cb/cbcn are upstream_reject (class upstream, #40 M0.6); client_errors counts
-- class client + account (excluded from service_* like IRE's metrics exclude client-caused
-- attempts); raw rates keep everything.
WITH w(win, hours) AS (VALUES ('1h', 1), ('24h', 24), ('7d', 168)),
cov AS (SELECT greatest(max(fetched_at), coalesce({LOGS_FETCHED_AT}, max(fetched_at))) AS window_end
         FROM fact_request_billing),
b AS (
  SELECT f.*, o.error_code, o.error_type, o.error_class, o.class_basis
  FROM fact_request_billing f JOIN fact_request_outcome o USING (request_id)
),
bw AS (
  SELECT w.win, w.hours, cov.window_end, cov.window_end - to_hours(w.hours) AS window_start, b.*,
         coalesce(b.error_class IN ('client', 'account'), false) AS client_attributable
  FROM b CROSS JOIN cov JOIN w ON b.ts > cov.window_end - to_hours(w.hours) AND b.ts <= cov.window_end
),
agg AS (
  SELECT route, any_value(rail) AS rail, win, any_value(hours) AS window_hours,
    any_value(window_start) AS window_start, any_value(window_end) AS window_end,
    count(*) AS requests,
    count(*) FILTER (WHERE NOT is_error) AS ok,
    count(*) FILTER (WHERE is_error) AS errors,
    count(*) FILTER (WHERE client_attributable) AS client_errors,
    count(*) FILTER (WHERE NOT client_attributable) AS service_attempts,
    count(*) FILTER (WHERE error_class = 'upstream') AS upstream_errors,
    count(*) FILTER (WHERE error_class = 'account') AS account_errors,
    count(*) FILTER (WHERE error_class = 'unknown') AS unknown_errors,
    count(*) FILTER (WHERE error_type = 'upstream_reject') AS err_upstream_reject,
    count(*) FILTER (WHERE error_type = 'upstream_reject' AND class_basis = 'rail_rule:cb_400_presumed_11133')
      AS err_upstream_reject_presumed,
    count(*) FILTER (WHERE error_type = 'upstream_unavailable') AS err_upstream_unavailable,
    count(*) FILTER (WHERE error_type = 'timeout') AS err_timeout,
    count(*) FILTER (WHERE error_type = 'rate_limited') AS err_rate_limited,
    count(*) FILTER (WHERE error_type = 'server_error') AS err_server_error,
    count(*) FILTER (WHERE error_type = 'client_request_error') AS err_client_request_error,
    count(*) FILTER (WHERE error_type = 'client_cancelled') AS err_client_cancelled,
    count(*) FILTER (WHERE error_type = 'auth') AS err_auth,
    count(*) FILTER (WHERE error_type = 'payment_required') AS err_payment_required,
    count(*) FILTER (WHERE error_type IN ('failed_http_200', 'unknown')) AS err_other,
    round(quantile_cont(ttft_ms, 0.5) FILTER (WHERE NOT is_error), 1) AS ttft_ms_p50,
    round(quantile_cont(ttft_ms, 0.95) FILTER (WHERE NOT is_error), 1) AS ttft_ms_p95,
    round(quantile_cont(duration_ms, 0.5) FILTER (WHERE NOT is_error), 1) AS duration_ms_p50,
    round(quantile_cont(duration_ms, 0.95) FILTER (WHERE NOT is_error), 1) AS duration_ms_p95,
    median(served_ask_in) FILTER (WHERE NOT is_error) AS served_ask_in_median,
    median(served_ask_out) FILTER (WHERE NOT is_error) AS served_ask_out_median,
    min(served_ask_in) FILTER (WHERE NOT is_error) AS served_ask_in_min,
    max(served_ask_in) FILTER (WHERE NOT is_error) AS served_ask_in_max,
    median(served_tier_rank_in) FILTER (WHERE NOT is_error) AS served_tier_rank_in_median,
    count(*) FILTER (WHERE NOT is_error AND served_tier_rank_in IS NOT NULL) AS served_tier_known,
    count(*) FILTER (WHERE NOT is_error AND served_tier_rank_in = 1) AS served_at_min_tier,
    count(*) FILTER (WHERE NOT is_error AND served_ask_in IS NOT NULL) AS served_price_known,
    count(*) FILTER (WHERE NOT is_error AND served_under_policy_threshold) AS served_under_policy,
    sum(billed_cost_usdc) AS billed_cost_usdc,
    sum(tokens_in) AS tokens_in, sum(tokens_out) AS tokens_out, sum(tokens_cached) AS tokens_cached,
    min(ts) AS first_request_at, max(ts) AS last_request_at,
    max(ts) FILTER (WHERE NOT is_error) AS last_ok_at,
    max(ts) FILTER (WHERE is_error AND NOT client_attributable) AS last_service_error_at
  FROM bw GROUP BY route, win
),
price AS (
  SELECT * FROM fact_route_price
  QUALIFY row_number() OVER (PARTITION BY route ORDER BY ts DESC) = 1
),
st AS (
  SELECT * FROM fact_route_status
  QUALIFY row_number() OVER (PARTITION BY rail ORDER BY ts DESC) = 1
)
SELECT a.route, a.rail, substr(a.route, length(a.rail) + 2) AS model_id,
  a.win AS "window", a.window_hours, a.window_start, a.window_end,
  a.requests, a.ok, a.errors, a.client_errors, a.service_attempts,
  round(a.ok / a.requests, 6) AS success_rate,
  round(a.ok / nullif(a.service_attempts, 0), 6) AS service_success_rate,
  a.* EXCLUDE (route, rail, win, window_hours, window_start, window_end, requests, ok, errors,
               client_errors, service_attempts),
  round(a.served_at_min_tier / nullif(a.served_tier_known, 0), 6) AS share_served_at_min_tier,
  round(a.served_under_policy / nullif(a.served_price_known, 0), 6) AS share_served_under_policy,
  p.ts AS live_price_ts, p.min_ask_in AS live_min_ask_in, p.min_ask_out AS live_min_ask_out,
  p.min_tier_avail_in AS live_min_tier_avail_in, p.cw_median_ask_in AS live_cw_median_ask_in,
  p.cw_median_ask_out AS live_cw_median_ask_out, p.official_in, p.official_out,
  p.avail_total_in AS live_listings_in, p.enabled AS live_enabled, p.book_hash AS live_book_hash,
  p.min_ask_in < {POLICY_PER_MTOK} AS live_min_ask_in_under_policy,
  s.state AS platform_rail_state, s.availability_now_pct AS platform_availability_now_pct,
  s.availability_24h_pct AS platform_availability_24h_pct, s.ts AS platform_status_ts
FROM agg a LEFT JOIN price p USING (route) LEFT JOIN st s ON s.rail = a.rail
ORDER BY a.route, a.window_hours
