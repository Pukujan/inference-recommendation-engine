-- route = (route id, provider, base URL host, api style). SCD2 columns kept for the design's shape;
-- valid_from/valid_to are first/last observation (no change tracking yet).
WITH obs AS (
  SELECT coalesce(p.llm_model_route, rr.model, p.observed_model) AS route_id,
         coalesce(p.llm_provider, rr.provider) AS provider,
         coalesce(p.llm_base_url_host, CASE WHEN coalesce(p.llm_provider, rr.provider) = 'inferhub' THEN 'api.inferhub.dev' END) AS base_url_host,
         p.wire_api AS api_style, {{ ts_utc('p.first_ns') }} AS seen_at
  FROM {{ ref('int_process_runs') }} p
  LEFT JOIN {{ ref('stg_receipt_runs') }} rr ON rr.run_id = p.run_key
  UNION ALL
  SELECT model, provider, CASE WHEN provider = 'inferhub' THEN 'api.inferhub.dev' END, NULL, started_at_utc
  FROM {{ ref('stg_receipt_runs') }})
SELECT {{ skey(['route_id', 'provider']) }} AS route_key, route_id, provider,
       max(base_url_host) AS base_url_host, max(api_style) AS api_style,
       min(seen_at) AS valid_from, max(seen_at) AS valid_to, {{ snap() }}
FROM obs GROUP BY route_id, provider
