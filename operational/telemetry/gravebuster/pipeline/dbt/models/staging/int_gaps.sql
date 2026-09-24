{{ config(materialized="table") }}
-- gaps between consecutive liveness events. Scope = one process run (children) or the whole
-- run_key for launcher-rooted runs (launcher + all child processes, ending at the launcher span end).
-- Codex housekeeping spans that fire while idle (noise rules with counts_for_liveness=false) are excluded.
WITH ev AS (
  SELECT e.*, r.run_id, r.role FROM {{ ref('int_events') }} e
  JOIN {{ ref('int_run_ids') }} r USING (run_key, service_name)
  WHERE e.counts_for_liveness),
scoped AS (
  SELECT run_id AS scope_run_id, t_ns, event, trace_id, span_id FROM ev WHERE role <> 'launcher'
  UNION ALL
  SELECT r.run_id, ev.t_ns, ev.event, ev.trace_id, ev.span_id
  FROM ev JOIN {{ ref('int_run_ids') }} r ON r.run_key = ev.run_key AND r.role = 'launcher'),
ordered AS (
  SELECT *, lag(t_ns) OVER w AS prev_ns, lag(event) OVER w AS prev_event,
         lag(trace_id) OVER w AS prev_trace_id, lag(span_id) OVER w AS prev_span_id,
         row_number() OVER w AS n, count(*) OVER (PARTITION BY scope_run_id) AS n_events
  FROM scoped WINDOW w AS (PARTITION BY scope_run_id ORDER BY t_ns, event, span_id))
SELECT scope_run_id AS run_id, prev_ns AS gap_start_ns, t_ns AS gap_end_ns, (t_ns - prev_ns) / 1e9 AS gap_s,
       prev_event AS before_event, event AS after_event, prev_trace_id AS before_trace_id, prev_span_id AS before_span_id,
       trace_id AS after_trace_id, span_id AS after_span_id, (n = n_events) AS is_last_event,
       row_number() OVER (PARTITION BY scope_run_id ORDER BY t_ns - prev_ns DESC, t_ns) AS gap_rank
FROM ordered WHERE prev_ns IS NOT NULL
