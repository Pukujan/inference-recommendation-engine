-- Join telemetry model requests (lake fact_model_requests) to InferHub billing rows.
-- exact_tokens: same route, tokens_in = prompt_tokens and tokens_out = completion_tokens, and
--   |billing.ts - started_at| <= 300 s; one-to-one (closest in time on both sides).
-- time_error: failed telemetry request without tokens -> failed billing row on the same route
--   within 30 s (one-to-one, closest).
WITH t AS (
  SELECT request_id, run_id, trace_id, span_id, route_id AS route, started_at_utc, tokens_in,
         tokens_out, tokens_cached, success, is_error, http_status, provider_error_code
  FROM fmr WHERE started_at_utc IS NOT NULL AND is_primary
    AND route_id IN (SELECT DISTINCT route FROM fact_request_billing)
), c1 AS (
  SELECT t.request_id, b.request_id AS billing_id, 'exact_tokens' AS match_type,
         epoch(b.ts) - epoch(t.started_at_utc) AS dt_s
  FROM t JOIN fact_request_billing b
    ON b.route = t.route AND b.tokens_in = t.tokens_in AND b.tokens_out = t.tokens_out
   AND abs(epoch(b.ts) - epoch(t.started_at_utc)) <= 300
  WHERE t.tokens_in IS NOT NULL
), c2 AS (
  SELECT t.request_id, b.request_id AS billing_id, 'time_error' AS match_type,
         epoch(b.ts) - epoch(t.started_at_utc) AS dt_s
  FROM t JOIN fact_request_billing b
    ON b.route = t.route AND b.is_error AND abs(epoch(b.ts) - epoch(t.started_at_utc)) <= 30
  WHERE t.tokens_in IS NULL AND (t.is_error OR NOT coalesce(t.success, true))
), c AS (
  SELECT * FROM c1 UNION ALL SELECT * FROM c2
), r AS (
  SELECT *, row_number() OVER (PARTITION BY request_id ORDER BY match_type, abs(dt_s)) rt,
            row_number() OVER (PARTITION BY billing_id ORDER BY match_type, abs(dt_s)) rb
  FROM c
), m AS (SELECT * FROM r WHERE rt = 1 AND rb = 1)
SELECT t.*, m.billing_id, coalesce(m.match_type, 'unmatched') AS match_type, m.dt_s,
       b.billed_cost_usdc, b.served_ask_in, b.served_ask_out, b.served_tier_rank_in,
       b.served_tier_avail_in, b.status AS billing_status, b.http_status AS billing_http_status,
       b.tokens_cached AS billing_tokens_cached, b.duration_ms AS billing_duration_ms
FROM t LEFT JOIN m USING (request_id) LEFT JOIN fact_request_billing b ON b.request_id = m.billing_id
ORDER BY t.started_at_utc
