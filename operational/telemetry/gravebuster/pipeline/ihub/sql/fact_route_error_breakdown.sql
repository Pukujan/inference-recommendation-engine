-- Long form of the error mix behind fact_route_reliability: route x window x (status, http_status).
-- Same windows (ending at the request-log coverage end) and error_type mapping.
WITH w(win, hours) AS (VALUES ('1h', 1), ('24h', 24), ('7d', 168)),
cov AS (SELECT max(fetched_at) AS window_end FROM fact_request_billing),
b AS (
  SELECT route, rail, ts, status, http_status, is_error,
    CASE
      WHEN NOT is_error THEN 'ok'
      WHEN http_status IN (502, 503) THEN 'upstream_unavailable'
      WHEN http_status IN (408, 504) THEN 'timeout'
      WHEN http_status = 429 THEN 'rate_limited'
      WHEN http_status BETWEEN 500 AND 599 THEN 'server_error'
      WHEN http_status = 499 THEN 'client_cancelled'
      WHEN http_status IN (401, 403) THEN 'auth'
      WHEN http_status = 402 THEN 'payment_required'
      WHEN http_status BETWEEN 400 AND 499 THEN 'client_request_error'
      WHEN http_status BETWEEN 200 AND 299 THEN 'failed_http_200'
      ELSE 'unknown' END AS error_type
  FROM fact_request_billing
)
SELECT b.route, any_value(b.rail) AS rail, w.win AS "window", any_value(w.hours) AS window_hours,
  any_value(cov.window_end) AS window_end, b.status, b.http_status, b.error_type,
  count(*) AS requests, min(b.ts) AS first_at, max(b.ts) AS last_at
FROM b CROSS JOIN cov JOIN w ON b.ts > cov.window_end - to_hours(w.hours) AND b.ts <= cov.window_end
GROUP BY b.route, w.win, b.status, b.http_status, b.error_type
ORDER BY b.route, window_hours, requests DESC
