-- 2-hour availability windows per rail; latest value published for each window.
SELECT rail, rail_slug, window_start, availability_pct, state, status_updated_at
FROM (SELECT *, row_number() OVER (PARTITION BY rail, window_start
                                   ORDER BY status_updated_at DESC) rn FROM src_status_history)
WHERE rn = 1 ORDER BY rail, window_start
