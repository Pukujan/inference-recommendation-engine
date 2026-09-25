-- One row per InferHub request (fact_request_billing id): error_code (from telemetry, if any),
-- error_type, error_class (upstream|client|account|unknown, NULL = ok) and class_basis.
-- The CASE rules are rendered from ihub/errclass.py ({ERROR_TYPE_CASE}, {ERROR_CLASS_CASE},
-- {CLASS_BASIS_CASE}); see that module for why an uncoded 400 on cb/cbcn is presumed 11133.
-- Code sources: inferhub_request_match (telemetry request joined to this billing row) first, then
-- an incident in fact_incidents (view fi) on the same route with a provider error code and a
-- timestamp (occurred_at_utc, else the astra-<stamp>Z in its summary) within 60 s of the request.
WITH mcode AS (
  SELECT billing_id, CAST(any_value(provider_error_code) AS VARCHAR) AS code
  FROM inferhub_request_match
  WHERE billing_id IS NOT NULL AND provider_error_code IS NOT NULL
  GROUP BY billing_id
), inc AS (
  SELECT route_id AS route,
    coalesce(occurred_at_utc,
             try_strptime(regexp_extract(summary, 'astra-(\d{8}T\d{6})Z', 1), '%Y%m%dT%H%M%S'))
      AS at,
    nullif(regexp_extract(metrics_json, '"provider_error_code":\s*(\d+)', 1), '') AS code
  FROM fi
), icode AS (
  SELECT b.request_id, arg_min(i.code, abs(epoch(i.at) - epoch(b.ts))) AS code
  FROM fact_request_billing b JOIN inc i
    ON i.route = b.route AND i.code IS NOT NULL AND i.at IS NOT NULL
   AND i.at BETWEEN b.ts - INTERVAL 60 SECOND AND b.ts + INTERVAL 60 SECOND
  WHERE b.is_error
  GROUP BY b.request_id
), o AS (
  SELECT b.request_id, b.ts, b.day_et, b.route, b.rail, b.status, b.http_status, b.is_error,
    coalesce(m.code, ic.code) AS error_code,
    CASE WHEN m.code IS NOT NULL THEN 'error_code:telemetry_request_match'
         WHEN ic.code IS NOT NULL THEN 'error_code:telemetry_incident_60s' END AS error_code_source
  FROM fact_request_billing b
  LEFT JOIN mcode m ON m.billing_id = b.request_id
  LEFT JOIN icode ic ON ic.request_id = b.request_id
), t AS (
  SELECT *, {ERROR_TYPE_CASE} AS error_type FROM o
)
SELECT request_id, ts, day_et, route, rail, status, http_status, is_error, error_code,
  error_type, {ERROR_CLASS_CASE} AS error_class, {CLASS_BASIS_CASE} AS class_basis
FROM t ORDER BY ts
