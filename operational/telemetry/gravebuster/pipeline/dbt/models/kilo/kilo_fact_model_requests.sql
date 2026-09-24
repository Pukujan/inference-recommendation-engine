{{ config(materialized='external', format='parquet', options={'compression': 'zstd'}) }}
-- one row per Kilo model call: each step-start/step-finish pair of an assistant message, plus one row
-- per assistant message that ended in an error (APIError with HTTP status / provider code, or abort).
SELECT request_id, run_id, task_id AS kilo_task_id, msg_index, step_index, message_id,
       provider_id || '/' || model_id AS route_id, provider_id AS provider, model_id, agent,
       make_timestamp(started_ms * 1000) AS started_at_utc, make_timestamp(ended_ms * 1000) AS ended_at_utc,
       (ended_ms - started_ms) AS duration_ms, finish_reason,
       tokens_in, tokens_out, tokens_reasoning, tokens_cache_read AS tokens_cached, tokens_cache_write, cost,
       is_error, error_name, http_status, provider_error_code, error_message, is_retryable,
       true AS is_primary, 'kilo' AS source, evidence_id, {{ snap() }}
FROM read_parquet('{{ var("clean_root") }}/kilo/kilo_model_requests.parquet')
