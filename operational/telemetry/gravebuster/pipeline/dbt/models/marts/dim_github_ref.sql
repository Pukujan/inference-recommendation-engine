SELECT DISTINCT {{ skey(["'Pukujan/inference-recommendation-engine'", 'ire_issue']) }} AS gh_key,
       'Pukujan/inference-recommendation-engine' AS repo, 'issue' AS kind, TRY_CAST(ire_issue AS INTEGER) AS number,
       NULL::VARCHAR AS sha, 'https://github.com/Pukujan/inference-recommendation-engine/issues/' || ire_issue AS url,
       {{ snap() }}
FROM {{ ref('int_process_runs') }} WHERE ire_issue IS NOT NULL
