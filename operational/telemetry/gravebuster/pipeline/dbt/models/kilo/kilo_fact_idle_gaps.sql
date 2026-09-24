{{ config(materialized='external', format='parquet', options={'compression': 'zstd'}) }}
-- gaps between consecutive Kilo events (message created/completed, step start/finish, tool start/end,
-- other parts) per session: the longest gap of every run (gap_rank = 1) plus every gap >= idle_gap_min_s.
WITH e AS (SELECT DISTINCT run_id, t_ms, event, evidence_id FROM read_parquet('{{ var("clean_root") }}/kilo/kilo_events.parquet')),
o AS (SELECT *, lag(t_ms) OVER w AS prev_ms, lag(event) OVER w AS prev_event, lag(evidence_id) OVER w AS prev_evidence_id,
             row_number() OVER w AS n, count(*) OVER (PARTITION BY run_id) AS n_events
      FROM e WINDOW w AS (PARTITION BY run_id ORDER BY t_ms, event, evidence_id)),
g AS (SELECT run_id, prev_ms, t_ms, (t_ms - prev_ms) / 1000.0 AS gap_s, prev_event, event, prev_evidence_id, evidence_id,
             (n = n_events) AS is_last_event,
             row_number() OVER (PARTITION BY run_id ORDER BY t_ms - prev_ms DESC, t_ms) AS gap_rank
      FROM o WHERE prev_ms IS NOT NULL)
SELECT {{ skey(['run_id', 'prev_ms', 't_ms']) }} AS gap_id, run_id, gap_rank,
       make_timestamp(prev_ms * 1000) AS gap_start_utc, make_timestamp(t_ms * 1000) AS gap_end_utc, gap_s,
       CASE WHEN is_last_event THEN 'run_end' ELSE 'event' END AS ended_by,
       prev_event AS before_event, event AS after_event, prev_evidence_id AS before_evidence_id,
       evidence_id AS after_evidence_id, {{ snap() }}
FROM g WHERE gap_rank = 1 OR gap_s >= {{ var('idle_gap_min_s') }}
