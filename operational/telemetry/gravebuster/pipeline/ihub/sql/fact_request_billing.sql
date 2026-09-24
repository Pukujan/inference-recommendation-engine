-- One row per InferHub request_logs id (latest revision). Served tier = the order-book tier whose
-- price equals the served ask (ask_*_per_mtok) in the book valid at the request time; InferHub
-- exposes no seller id, so the served price/tier is the best available seller proxy.
WITH b AS (
  SELECT * FROM (
    SELECT *, row_number() OVER (PARTITION BY id ORDER BY fetched_at DESC, is_revision DESC) rn
    FROM src_billing_all) WHERE rn = 1
), bb AS (
  SELECT id AS request_id, ts::TIMESTAMPTZ::TIMESTAMP AS ts,
         (ts::TIMESTAMPTZ AT TIME ZONE 'America/New_York')::DATE AS day_et,
         model AS route, split_part(model, '/', 1) AS rail, upstream_label AS rail_label,
         status, http_status, status <> 'ok' AS is_error,
         prompt_tokens AS tokens_in, completion_tokens AS tokens_out,
         cached_tokens AS tokens_cached, cache_write_tokens AS tokens_cache_write,
         cost_consumer_usdc::DECIMAL(18,6) AS billed_cost_usdc,
         ask_input_per_mtok::DOUBLE AS served_ask_in, ask_output_per_mtok::DOUBLE AS served_ask_out,
         region, ttft_ms, duration_ms, routing_ms, extras_json, content_hash,
         fetched_at::TIMESTAMPTZ::TIMESTAMP AS fetched_at
  FROM b
)
SELECT bb.*,
  (SELECT t.tier_rank FROM fact_price_snapshot t WHERE t.route = bb.route AND t.side = 'in'
     AND t.tier_price = bb.served_ask_in AND t.valid_from <= bb.ts
   ORDER BY t.valid_from DESC LIMIT 1) AS served_tier_rank_in,
  (SELECT t.avail_count FROM fact_price_snapshot t WHERE t.route = bb.route AND t.side = 'in'
     AND t.tier_price = bb.served_ask_in AND t.valid_from <= bb.ts
   ORDER BY t.valid_from DESC LIMIT 1) AS served_tier_avail_in,
  (SELECT r.min_ask_in FROM src_routes r WHERE r.route = bb.route AND r.fetched_at <= bb.ts
   ORDER BY r.fetched_at DESC LIMIT 1) AS min_ask_in_at_ts,
  bb.served_ask_in < {POLICY_PER_MTOK} AS served_under_policy_threshold
FROM bb ORDER BY ts
