-- Order-book tiers per route and side, stored as change-data-capture: a route's full book is
-- written only when any tier price / avail count / official price / enabled flag changes.
-- valid_from = first fetch showing this book, valid_to = first later fetch showing a different
-- book (NULL = still current), last_seen_at = last fetch that still showed it.
WITH books AS (
  SELECT DISTINCT route, observed_at AS valid_from, book_hash FROM src_tiers
), changes AS (
  SELECT b.route, b.valid_from, b.book_hash,
         (SELECT min(r.fetched_at) FROM src_routes r
           WHERE r.route = b.route AND r.fetched_at > b.valid_from AND r.book_hash <> b.book_hash
         ) AS valid_to
  FROM books b
), spans AS (
  SELECT c.*,
         (SELECT max(r.fetched_at) FROM src_routes r
           WHERE r.route = c.route AND r.book_hash = c.book_hash AND r.fetched_at >= c.valid_from
             AND (c.valid_to IS NULL OR r.fetched_at < c.valid_to)) AS last_seen_at
  FROM changes c
)
SELECT t.route, t.rail, t.side, t.tier_rank, t.tier_price, t.avail_count, t.cum_avail,
       t.official_price, t.discount_pct,
       t.observed_at AS ts, s.valid_from, s.valid_to, s.last_seen_at,
       s.valid_to IS NULL AS is_current,
       t.tier_price < {POLICY_PER_MTOK} AS under_policy_threshold,
       t.book_hash
FROM src_tiers t JOIN spans s ON s.route = t.route AND s.valid_from = t.observed_at
ORDER BY t.route, t.observed_at, t.side, t.tier_rank
