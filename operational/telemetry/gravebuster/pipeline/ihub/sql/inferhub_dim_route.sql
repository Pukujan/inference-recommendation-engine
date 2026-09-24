-- One row per route (<rail>/<upstream model id>) ever seen in the catalog, latest attributes.
SELECT route, rail, model_label, upstream_model_id, official_in, official_out, enabled,
       supports_cache, context_window, max_output_tokens, modalities, output_modality,
       reasoning_levels,
       first_seen_at, fetched_at AS last_seen_at
FROM (
  SELECT *, min(fetched_at) OVER (PARTITION BY route) AS first_seen_at,
         row_number() OVER (PARTITION BY route ORDER BY fetched_at DESC) AS rn
  FROM src_routes)
WHERE rn = 1
ORDER BY route
