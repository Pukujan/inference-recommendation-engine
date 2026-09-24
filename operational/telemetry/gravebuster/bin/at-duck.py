#!/srv/agent-telemetry/pipeline/venv/bin/python
"""Read-only DuckDB query helper over the agent-telemetry lake (raw / clean / modeled Parquet).

  at-duck.py tables                      every view with row counts
  at-duck.py runs-by-harness             runs, outcomes, wall time per harness/launch mode
  at-duck.py killed                      killed runs (telemetry + receipts)
  at-duck.py idle-gaps [N]               longest idle gaps between liveness events
  at-duck.py errors                      model-request errors (11133 etc.) with request-shape features
  at-duck.py run <run_id>                one run: fact row, hops, gaps, requests, tool calls
  at-duck.py trace <trace_id> [--live]   spans/logs of one trace in clean; --live also scans the not-yet-ingested
                                         collector files (data/otel/*.jsonl*) so a trace is visible within ~1s
  at-duck.py stats                       bytes on disk per layer, compression ratio, bytes per run
  at-duck.py etl [N]                     last N pipeline runs (etl_runs.jsonl)
  at-duck.py sql "<query>"               anything else (views listed by `tables`)
  at-duck.py examples                    print example SQL
Options: --snapshot snap-<id> (default: modeled/current). Memory capped at 1GB, 2 threads.
"""

import glob
import json
import os
import sys

import duckdb

ROOT = os.environ.get("AT_ROOT", "/srv/agent-telemetry")
LAKE = os.environ.get("AT_LAKE", os.path.join(ROOT, "data", "lake"))

EXAMPLES = """
-- runs by harness
SELECT harness, launch_mode, count(*) runs, count(*) FILTER (WHERE outcome='completed') completed,
       count(*) FILTER (WHERE killed) killed, round(median(wall_s)) median_wall_s
FROM fact_runs GROUP BY ALL ORDER BY runs DESC;
-- killed runs with evidence
SELECT run_id, source, started_at_utc, round(wall_s) wall_s, exit_code, kill_signal, max_idle_gap_s, receipt_stamp, root_trace_id
FROM fact_runs WHERE killed ORDER BY started_at_utc;
-- longest idle gaps (liveness events only; Codex housekeeping excluded)
SELECT run_id, round(gap_s,1) gap_s, gap_start_utc, before_event, after_event, ended_by, before_span_id, after_span_id
FROM fact_idle_gaps WHERE gap_rank = 1 ORDER BY gap_s DESC LIMIT 20;
-- provider parameter rejections (InferHub 11133) and request shape
SELECT run_id, source, http_status, provider_error_code, n_messages, n_tools, n_empty_assistant_text_with_tool_calls,
       n_tool_schema_additional_properties, max_tool_description_bytes, provider_request_id
FROM fact_model_requests WHERE is_error ORDER BY run_id;
-- main agent vs child (same route): wall time and tool calls
SELECT r.role, d.route_id, count(*) runs, round(avg(r.wall_s)) avg_wall_s, sum(r.n_tool_calls) tool_calls
FROM fact_runs r JOIN dim_route d USING (route_key) GROUP BY ALL;
-- noise share per run (clean layer)
SELECT run_key, count(*) spans, count(*) FILTER (WHERE is_noise) noise, round(100.0*count(*) FILTER (WHERE is_noise)/count(*),1) pct
FROM clean_spans GROUP BY 1 ORDER BY 2 DESC;
"""


def connect(snapshot=None):
    con = duckdb.connect()
    con.execute("SET memory_limit='1GB'; SET threads=2; SET enable_progress_bar=false")
    views = {}

    def pq(name, pattern):
        if glob.glob(pattern):
            con.execute(
                f"CREATE VIEW {name} AS SELECT * FROM read_parquet('{pattern}', hive_partitioning=false, union_by_name=true)"
            )
            views[name] = pattern

    pq("raw_spans", f"{LAKE}/raw/spans/day=*/*.parquet")
    pq("raw_logs", f"{LAKE}/raw/logs/day=*/*.parquet")
    pq("raw_metrics", f"{LAKE}/raw/metrics/day=*/*.parquet")
    for t in ("spans", "logs", "metrics"):
        pq(f"raw_conflicts_{t}", f"{LAKE}/raw/conflicts/{t}/*.parquet")
    pq("clean_spans", f"{LAKE}/clean/spans/day=*/*.parquet")
    pq("clean_logs", f"{LAKE}/clean/logs/day=*/*.parquet")
    for t in ("receipt_runs", "receipt_events", "receipt_files"):
        pq(t, f"{LAKE}/clean/receipts/{t}.parquet")
    snapdir = os.path.join(LAKE, "modeled", snapshot or "current")
    for f in sorted(glob.glob(os.path.join(snapdir, "*.parquet"))):
        pq(os.path.basename(f)[:-8], f)
    for name, path in (
        ("etl_runs", f"{LAKE}/meta/etl_runs.jsonl"),
        ("raw_manifest", f"{LAKE}/raw/manifest.jsonl"),
        ("snapshots", f"{LAKE}/modeled/snapshots.jsonl"),
    ):
        if os.path.exists(path):
            con.execute(
                f"CREATE VIEW {name} AS SELECT * FROM read_json_auto('{path}', format='newline_delimited', union_by_name=true)"
            )
            views[name] = path
    return con, views, snapdir


def show(con, sql, n=100):
    con.sql(sql).show(max_rows=n, max_width=220)


def du(path):
    tot = 0
    for dp, _, fs in os.walk(path):
        for f in fs:
            try:
                tot += os.path.getsize(os.path.join(dp, f))
            except OSError:
                pass
    return tot


def live_trace(tid):
    """Scan the collector's file-exporter output (active + rotated) for one trace; identity-scrubbed output."""
    sys.path.insert(0, os.path.join(ROOT, "pipeline"))
    from atpipe.common import attrs, mask_emails, scrub

    tid = "".join(c for c in tid.lower() if c in "0123456789abcdef")
    needle = tid.encode()
    spans, logs = [], []
    for f in sorted(glob.glob(os.path.join(ROOT, "data", "otel", "*.jsonl*"))):
        with open(f, "rb") as fh:
            for line in fh:
                if needle not in line:
                    continue
                try:
                    doc = json.loads(line)
                except ValueError:
                    continue
                for rs in doc.get("resourceSpans", []):
                    svc = attrs(rs.get("resource", {}).get("attributes", [])).get("service.name")
                    for ss in rs.get("scopeSpans", []):
                        for sp in ss.get("spans", []):
                            if sp.get("traceId") == tid:
                                a = mask_emails(scrub(attrs(sp.get("attributes", []))))
                                a.pop("run.stderr_tail", None)
                                spans.append(
                                    (
                                        svc,
                                        sp.get("name"),
                                        sp.get("spanId"),
                                        sp.get("parentSpanId") or None,
                                        (sp.get("status") or {}).get("code"),
                                        a,
                                    )
                                )
                for rl in doc.get("resourceLogs", []):
                    svc = attrs(rl.get("resource", {}).get("attributes", [])).get("service.name")
                    for sl in rl.get("scopeLogs", []):
                        for lr in sl.get("logRecords", []):
                            if lr.get("traceId") == tid:
                                a = scrub(attrs(lr.get("attributes", [])))
                                logs.append((svc, a.get("event.name") or lr.get("eventName")))
    print(f"== live collector files: spans={len(spans)} logs={len(logs)}")
    from collections import Counter

    print("spans by service:", dict(Counter(s[0] for s in spans)))
    for s in spans[:60]:
        print(
            " ",
            s[0],
            s[1],
            s[2],
            "parent=",
            s[3],
            "status=",
            s[4],
            json.dumps(s[5], default=str)[:400],
        )
    print("logs by service/event:", dict(Counter(logs).most_common(15)))


def main():
    args = sys.argv[1:]
    snap = None
    if "--snapshot" in args:
        i = args.index("--snapshot")
        snap = args[i + 1]
        del args[i : i + 2]
    cmd = args[0] if args else "tables"
    con, views, snapdir = connect(snap)
    if cmd == "tables":
        print(f"snapshot: {os.path.realpath(snapdir)}")
        for v in views:
            n = con.execute(f"SELECT count(*) FROM {v}").fetchone()[0]
            print(f"  {v:28s} {n:>10}")
    elif cmd == "runs-by-harness":
        show(
            con,
            """SELECT harness, launch_mode, role, count(*) runs,
                            count(*) FILTER (WHERE outcome='completed') completed, count(*) FILTER (WHERE outcome='failed') failed,
                            count(*) FILTER (WHERE killed) killed, round(median(wall_s)) median_wall_s, sum(n_tool_calls) tool_calls
                     FROM fact_runs GROUP BY ALL ORDER BY runs DESC""",
        )
    elif cmd == "killed":
        show(
            con,
            """SELECT run_id, source, started_at_utc, round(wall_s) wall_s, exit_code, kill_signal,
                            round(max_idle_gap_s,1) max_idle_gap_s, n_tool_calls, receipt_stamp, root_trace_id
                     FROM fact_runs WHERE killed ORDER BY started_at_utc""",
        )
    elif cmd == "idle-gaps":
        n = int(args[1]) if len(args) > 1 else 20
        show(
            con,
            f"""SELECT run_id, round(gap_s,1) gap_s, gap_start_utc, gap_end_utc, ended_by, before_event, after_event,
                             before_span_id, after_span_id FROM fact_idle_gaps WHERE gap_rank = 1 ORDER BY gap_s DESC LIMIT {n}""",
        )
    elif cmd == "errors":
        show(
            con,
            """SELECT run_id, source, is_primary, http_status, provider_error_code, error_type, n_messages, n_tools,
                            n_empty_assistant_text_with_tool_calls AS empty_txt_tc, n_tool_schema_additional_properties AS addl_props,
                            max_tool_description_bytes AS max_desc, provider_request_id, span_id
                     FROM fact_model_requests WHERE is_error ORDER BY run_id""",
        )
    elif cmd == "run":
        rid = args[1]
        show(con, f"SELECT * FROM fact_runs WHERE run_id = '{rid}' OR root_run_id = '{rid}'")
        show(
            con,
            f"SELECT * EXCLUDE (snapshot_id) FROM fact_hops WHERE parent_run_id = '{rid}' OR child_run_id = '{rid}'",
        )
        show(
            con,
            f"SELECT run_id, gap_rank, round(gap_s,1) gap_s, ended_by, before_event, after_event FROM fact_idle_gaps WHERE run_id LIKE '{rid}%' ORDER BY run_id, gap_rank",
        )
        show(
            con,
            f"SELECT source, is_primary, is_error, http_status, provider_error_code, count(*) n, sum(tokens_in) tin, sum(tokens_out) tout FROM fact_model_requests WHERE run_id LIKE '{rid}%' GROUP BY ALL",
        )
        show(
            con,
            f"SELECT source, is_primary, tool_name, status, count(*) n, round(avg(duration_ms)) avg_ms FROM fact_tool_calls WHERE run_id LIKE '{rid}%' GROUP BY ALL ORDER BY n DESC",
        )
    elif cmd == "trace":
        tid = args[1]
        show(
            con,
            f"SELECT service_name, is_noise, count(*) FROM clean_spans WHERE trace_id = '{tid}' GROUP BY ALL",
        )
        show(
            con,
            f"""SELECT name, span_id, parent_span_id, start_ts_utc, round(duration_ms) ms, status_code, run_exit_code, run_killed
                      FROM clean_spans WHERE trace_id = '{tid}' AND NOT is_noise ORDER BY start_unix_nano LIMIT 60""",
        )
        show(
            con,
            f"SELECT service_name, event, count(*) FROM clean_logs WHERE trace_id = '{tid}' GROUP BY ALL ORDER BY 3 DESC",
        )
        if "--live" in args:
            live_trace(tid)
    elif cmd == "stats":
        layers = {
            "raw segments (.jsonl.zst)": os.path.join(LAKE, "raw", "segments"),
            "raw parquet spans": os.path.join(LAKE, "raw", "spans"),
            "raw parquet logs": os.path.join(LAKE, "raw", "logs"),
            "raw conflicts": os.path.join(LAKE, "raw", "conflicts"),
            "clean": os.path.join(LAKE, "clean"),
            "modeled (all snapshots)": os.path.join(LAKE, "modeled"),
            "modeled (current)": os.path.realpath(snapdir),
        }
        for k, p in layers.items():
            print(f"  {k:28s} {du(p):>14,d} B")
        if "raw_manifest" in views:
            show(
                con,
                """SELECT signal, count(*) segments, sum(lines) lines, sum(bytes) raw_bytes, sum(zst_bytes) zst_bytes,
                                round(sum(bytes)/sum(zst_bytes),1) ratio FROM raw_manifest WHERE coalesce(kind,'segment')='segment' GROUP BY 1""",
            )
        tot_raw = (
            con.execute(
                "SELECT sum(bytes) FROM raw_manifest WHERE coalesce(kind,'segment')='segment'"
            ).fetchone()[0]
            if "raw_manifest" in views
            else 0
        )
        pq_bytes = du(os.path.join(LAKE, "raw", "spans")) + du(os.path.join(LAKE, "raw", "logs"))
        print(
            f"  raw JSON bytes {tot_raw:,} -> raw parquet {pq_bytes:,} B (ratio {tot_raw / max(pq_bytes, 1):.1f}x)"
        )
        if "clean_spans" in views:
            show(
                con,
                f"""WITH r AS (SELECT run_key, count(*) spans FROM clean_spans GROUP BY 1),
                         tot AS (SELECT sum(spans) s FROM r)
                         SELECT r.run_key, r.spans,
                                round({du(os.path.join(LAKE, "raw", "segments"))} * r.spans / tot.s) AS est_zst_bytes,
                                round({pq_bytes} * r.spans / tot.s) AS est_raw_parquet_bytes,
                                round({du(os.path.join(LAKE, "clean"))} * r.spans / tot.s) AS est_clean_bytes
                         FROM r, tot WHERE r.spans > 100 ORDER BY r.spans DESC""",
            )
    elif cmd == "etl":
        n = int(args[1]) if len(args) > 1 else 10
        show(
            con,
            f"""SELECT etl_run_id, status, duration_s, segments_sealed, segments_in, snapshot_built, left(snapshot_id,16) AS snap,
                             peak_rss_mb_self, ingest.spans.new AS spans_new, ingest.spans.dups_dropped AS spans_dups,
                             ingest.spans.conflicts AS spans_conflicts, ingest.logs.new AS logs_new
                      FROM etl_runs ORDER BY started_at DESC LIMIT {n}""",
        )
    elif cmd == "sql":
        show(con, args[1], 500)
    elif cmd == "examples":
        print(EXAMPLES)
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
