WITH m AS (
  SELECT coalesce(observed_model, llm_model_route) AS model_id FROM {{ ref('int_process_runs') }}
  UNION SELECT model FROM {{ ref('stg_receipt_runs') }}
  UNION SELECT model FROM {{ ref('stg_spans') }} WHERE model IS NOT NULL)
SELECT DISTINCT {{ skey(['model_id']) }} AS model_key, model_id,
       CASE WHEN contains(model_id, '/') THEN split_part(model_id, '/', 1) END AS route_prefix,
       regexp_extract(CASE WHEN contains(model_id, '/') THEN split_part(model_id, '/', 2) ELSE model_id END,
                      '^([A-Za-z]+-?[0-9.]*)', 1) AS family,
       NULL::VARCHAR AS catalog_version, NULL::BIGINT AS context_window, NULL::VARCHAR AS param_constraints_hash,
       {{ snap() }}
FROM m
