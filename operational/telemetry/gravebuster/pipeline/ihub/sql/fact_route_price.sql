-- Every catalog fetch (5 min): per-route min ask, tier and avail totals, capacity-weighted median.
WITH med AS (
  SELECT r.route, r.fetched_at,
    (SELECT t.tier_price FROM fact_price_snapshot t
      WHERE t.route = r.route AND t.side = 'in' AND t.valid_from <= r.fetched_at
        AND (t.valid_to IS NULL OR t.valid_to > r.fetched_at)
        AND t.cum_avail * 2 >= r.avail_total_in ORDER BY t.tier_rank LIMIT 1) AS cw_median_ask_in,
    (SELECT t.tier_price FROM fact_price_snapshot t
      WHERE t.route = r.route AND t.side = 'out' AND t.valid_from <= r.fetched_at
        AND (t.valid_to IS NULL OR t.valid_to > r.fetched_at)
        AND t.cum_avail * 2 >= r.avail_total_out ORDER BY t.tier_rank LIMIT 1) AS cw_median_ask_out
  FROM src_routes r
)
SELECT r.fetched_at AS ts, r.route, r.rail, r.official_in, r.official_out,
       r.min_ask_in, r.min_ask_out, r.min_tier_avail_in, r.min_tier_avail_out,
       m.cw_median_ask_in, m.cw_median_ask_out,
       r.tiers_in, r.tiers_out, r.avail_total_in, r.avail_total_out, r.enabled, r.book_hash
FROM src_routes r LEFT JOIN med m USING (route, fetched_at)
ORDER BY r.route, r.fetched_at
