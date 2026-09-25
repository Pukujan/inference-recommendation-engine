-- Long form of the error mix behind fact_route_reliability: route x window x (status, http_status).
-- Same windows (ending at the request-log coverage end); error_code/type/class/basis from
-- fact_request_outcome (rules in ihub/errclass.py).
WITH w(win, hours) AS (VALUES ('1h', 1), ('24h', 24), ('7d', 168)),
cov AS (SELECT greatest(max(fetched_at), coalesce({LOGS_FETCHED_AT}, max(fetched_at))) AS window_end
         FROM fact_request_billing),
b AS (
  SELECT route, rail, ts, status, http_status, is_error, error_code, error_type, error_class,
         class_basis
  FROM fact_request_outcome
)
SELECT b.route, any_value(b.rail) AS rail, w.win AS "window", any_value(w.hours) AS window_hours,
  any_value(cov.window_end) AS window_end, b.status, b.http_status, b.error_code, b.error_type,
  b.error_class, b.class_basis,
  count(*) AS requests, min(b.ts) AS first_at, max(b.ts) AS last_at
FROM b CROSS JOIN cov JOIN w ON b.ts > cov.window_end - to_hours(w.hours) AND b.ts <= cov.window_end
GROUP BY b.route, w.win, b.status, b.http_status, b.error_code, b.error_type, b.error_class,
  b.class_basis
ORDER BY b.route, window_hours, requests DESC
