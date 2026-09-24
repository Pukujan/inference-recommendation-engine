#!/usr/bin/env python3
"""agent-telemetry SQLite loader.

Tails the otelcol `file` exporter output (OTLP JSON lines, rotated by lumberjack)
in /srv/agent-telemetry/data/otel and appends rows to an append-only SQLite db.
Data tables (spans, logs, metrics) have triggers that ABORT any UPDATE/DELETE.
Progress (inode + byte offset per signal) lives in loader_state and is committed in
the same transaction as the rows, so restarts neither lose nor duplicate lines.
Stdlib only.
"""

import datetime
import glob
import json
import os
import signal
import sqlite3
import sys
import time

OTEL_DIR = os.environ.get("AT_OTEL_DIR", "/srv/agent-telemetry/data/otel")
DB_PATH = os.environ.get("AT_DB", "/srv/agent-telemetry/data/sqlite/traces.sqlite3")
POLL = float(os.environ.get("AT_POLL_SECONDS", "2"))
SIGNALS = ("traces", "logs", "metrics")

SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS spans(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ingested_at TEXT NOT NULL,
  trace_id TEXT, span_id TEXT, parent_span_id TEXT,
  name TEXT, kind INTEGER,
  service_name TEXT, harness TEXT, topology TEXT, task_id TEXT, correlation_id TEXT, ire_issue TEXT,
  start_time TEXT, end_time TEXT, start_unix_nano INTEGER, end_unix_nano INTEGER, duration_ms REAL,
  status_code INTEGER, status_message TEXT,
  scope_name TEXT,
  attributes TEXT, resource_attributes TEXT, events TEXT,
  source_file TEXT, source_offset INTEGER
);
CREATE TABLE IF NOT EXISTS logs(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ingested_at TEXT NOT NULL,
  trace_id TEXT, span_id TEXT,
  service_name TEXT, harness TEXT, topology TEXT, task_id TEXT, correlation_id TEXT, ire_issue TEXT,
  time TEXT, time_unix_nano INTEGER, observed_time TEXT,
  severity_number INTEGER, severity_text TEXT, event_name TEXT,
  body TEXT, scope_name TEXT,
  attributes TEXT, resource_attributes TEXT,
  source_file TEXT, source_offset INTEGER
);
CREATE TABLE IF NOT EXISTS metrics(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ingested_at TEXT NOT NULL,
  name TEXT, unit TEXT, type TEXT,
  service_name TEXT, harness TEXT, topology TEXT, task_id TEXT, correlation_id TEXT, ire_issue TEXT,
  time TEXT, time_unix_nano INTEGER, start_time TEXT,
  value REAL, point TEXT, scope_name TEXT,
  attributes TEXT, resource_attributes TEXT,
  source_file TEXT, source_offset INTEGER
);
CREATE TABLE IF NOT EXISTS loader_state(
  signal TEXT PRIMARY KEY, inode INTEGER, offset INTEGER, updated_at TEXT
);
CREATE INDEX IF NOT EXISTS spans_trace ON spans(trace_id);
CREATE INDEX IF NOT EXISTS spans_task ON spans(task_id);
CREATE INDEX IF NOT EXISTS spans_start ON spans(start_unix_nano);
CREATE INDEX IF NOT EXISTS logs_trace ON logs(trace_id);
CREATE INDEX IF NOT EXISTS logs_time ON logs(time_unix_nano);
CREATE INDEX IF NOT EXISTS metrics_name_time ON metrics(name, time_unix_nano);
"""
for _t in ("spans", "logs", "metrics"):
    SCHEMA += f"""
CREATE TRIGGER IF NOT EXISTS {_t}_no_update BEFORE UPDATE ON {_t}
BEGIN SELECT RAISE(ABORT, '{_t} is append-only'); END;
CREATE TRIGGER IF NOT EXISTS {_t}_no_delete BEFORE DELETE ON {_t}
BEGIN SELECT RAISE(ABORT, '{_t} is append-only'); END;
"""


def now_iso():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="milliseconds")


def ns_iso(ns):
    try:
        ns = int(ns)
    except (TypeError, ValueError):
        return None
    if ns <= 0:
        return None
    return datetime.datetime.fromtimestamp(ns / 1e9, datetime.timezone.utc).isoformat(
        timespec="microseconds"
    )


def anyval(v):
    if not isinstance(v, dict):
        return v
    if "stringValue" in v:
        return v["stringValue"]
    if "boolValue" in v:
        return v["boolValue"]
    if "intValue" in v:
        try:
            return int(v["intValue"])
        except Exception:
            return v["intValue"]
    if "doubleValue" in v:
        return v["doubleValue"]
    if "arrayValue" in v:
        return [anyval(x) for x in v["arrayValue"].get("values", [])]
    if "kvlistValue" in v:
        return attrs(v["kvlistValue"].get("values", []))
    if "bytesValue" in v:
        return v["bytesValue"]
    return None


def attrs(lst):
    return {kv.get("key"): anyval(kv.get("value", {})) for kv in (lst or [])}


def pick(keys, *dicts):
    for d in dicts:
        for k in keys:
            if d.get(k) not in (None, ""):
                return str(d[k])
    return None


def ctx(res, a):
    return dict(
        service_name=pick(["service.name"], res, a),
        harness=pick(["harness", "agent.harness"], a, res),
        topology=pick(["topology", "run.topology"], a, res),
        task_id=pick(["task.id", "task_id"], a, res),
        correlation_id=pick(["correlation_id", "correlation.id"], a, res),
        ire_issue=pick(["ire.issue"], a, res),
    )


def J(o):
    return json.dumps(o, separators=(",", ":"), ensure_ascii=False, default=str)


def rows_traces(doc):
    for rs in doc.get("resourceSpans", []):
        res = attrs(rs.get("resource", {}).get("attributes"))
        for ss in rs.get("scopeSpans", []):
            scope = (ss.get("scope") or {}).get("name")
            for sp in ss.get("spans", []):
                a = attrs(sp.get("attributes"))
                st, en = sp.get("startTimeUnixNano"), sp.get("endTimeUnixNano")
                try:
                    dur = (int(en) - int(st)) / 1e6
                except Exception:
                    dur = None
                status = sp.get("status") or {}
                events = [
                    {
                        "name": e.get("name"),
                        "time": ns_iso(e.get("timeUnixNano")),
                        "attributes": attrs(e.get("attributes")),
                    }
                    for e in sp.get("events", [])
                ]
                c = ctx(res, a)
                yield (
                    "spans",
                    dict(
                        trace_id=sp.get("traceId"),
                        span_id=sp.get("spanId"),
                        parent_span_id=sp.get("parentSpanId") or None,
                        name=sp.get("name"),
                        kind=sp.get("kind"),
                        start_time=ns_iso(st),
                        end_time=ns_iso(en),
                        start_unix_nano=int(st) if st else None,
                        end_unix_nano=int(en) if en else None,
                        duration_ms=dur,
                        status_code=status.get("code", 0),
                        status_message=status.get("message"),
                        scope_name=scope,
                        attributes=J(a),
                        resource_attributes=J(res),
                        events=J(events) if events else None,
                        **c,
                    ),
                )


def rows_logs(doc):
    for rl in doc.get("resourceLogs", []):
        res = attrs(rl.get("resource", {}).get("attributes"))
        for sl in rl.get("scopeLogs", []):
            scope = (sl.get("scope") or {}).get("name")
            for lr in sl.get("logRecords", []):
                a = attrs(lr.get("attributes"))
                body = anyval(lr.get("body", {}))
                t = lr.get("timeUnixNano") or lr.get("observedTimeUnixNano")
                yield (
                    "logs",
                    dict(
                        trace_id=lr.get("traceId") or None,
                        span_id=lr.get("spanId") or None,
                        time=ns_iso(t),
                        time_unix_nano=int(t) if t else None,
                        observed_time=ns_iso(lr.get("observedTimeUnixNano")),
                        severity_number=lr.get("severityNumber"),
                        severity_text=lr.get("severityText"),
                        event_name=lr.get("eventName") or a.get("event.name"),
                        body=body if isinstance(body, str) or body is None else J(body),
                        scope_name=scope,
                        attributes=J(a),
                        resource_attributes=J(res),
                        **ctx(res, a),
                    ),
                )


def rows_metrics(doc):
    for rm in doc.get("resourceMetrics", []):
        res = attrs(rm.get("resource", {}).get("attributes"))
        for sm in rm.get("scopeMetrics", []):
            scope = (sm.get("scope") or {}).get("name")
            for m in sm.get("metrics", []):
                for mtype in ("sum", "gauge", "histogram", "exponentialHistogram", "summary"):
                    if mtype not in m:
                        continue
                    for dp in m[mtype].get("dataPoints", []):
                        a = attrs(dp.get("attributes"))
                        if "asDouble" in dp:
                            val = dp["asDouble"]
                        elif "asInt" in dp:
                            val = float(dp["asInt"])
                        elif "sum" in dp:
                            val = dp.get("sum")
                        else:
                            val = None
                        point = {k: v for k, v in dp.items() if k != "attributes"}
                        t = dp.get("timeUnixNano")
                        yield (
                            "metrics",
                            dict(
                                name=m.get("name"),
                                unit=m.get("unit"),
                                type=mtype,
                                time=ns_iso(t),
                                time_unix_nano=int(t) if t else None,
                                start_time=ns_iso(dp.get("startTimeUnixNano")),
                                value=val,
                                point=J(point),
                                scope_name=scope,
                                attributes=J(a),
                                resource_attributes=J(res),
                                **ctx(res, a),
                            ),
                        )


PARSERS = {"traces": rows_traces, "logs": rows_logs, "metrics": rows_metrics}


def insert(db, table, row, src, off):
    row = dict(row, ingested_at=now_iso(), source_file=src, source_offset=off)
    cols = ",".join(row)
    db.execute(
        f"INSERT INTO {table}({cols}) VALUES({','.join('?' * len(row))})", list(row.values())
    )


def read_from(db, sig, path, offset):
    """Read complete lines from path starting at offset; returns new offset."""
    n = 0
    with open(path, "rb") as f:
        f.seek(offset)
        while True:
            line = f.readline()
            if not line or not line.endswith(b"\n"):
                break  # partial line: wait for the writer
            pos = offset
            offset += len(line)
            s = line.strip()
            if not s:
                continue
            try:
                doc = json.loads(s)
            except Exception as e:
                print(f"[loader] bad json in {path}@{pos}: {e}", file=sys.stderr)
                continue
            for table, row in PARSERS[sig](doc):
                insert(db, table, row, os.path.basename(path), pos)
                n += 1
    return offset, n


def rotated_with_inode(sig, inode):
    for p in glob.glob(os.path.join(OTEL_DIR, f"{sig}-*.jsonl")):
        try:
            if os.stat(p).st_ino == inode:
                return p
        except FileNotFoundError:
            pass
    return None


def step(db, sig):
    cur = os.path.join(OTEL_DIR, f"{sig}.jsonl")
    r = db.execute("SELECT inode, offset FROM loader_state WHERE signal=?", (sig,)).fetchone()
    inode, offset = r if r else (None, 0)
    if not os.path.exists(cur):
        return 0
    st = os.stat(cur)
    total = 0
    with db:
        if inode is not None and st.st_ino != inode:
            old = rotated_with_inode(sig, inode)
            if old:
                _, n = read_from(db, sig, old, offset)
                total += n
            inode, offset = st.st_ino, 0
        if inode is None:
            inode = st.st_ino
        if st.st_size < offset:  # truncated in place
            offset = 0
        offset, n = read_from(db, sig, cur, offset)
        total += n
        db.execute(
            "INSERT INTO loader_state(signal,inode,offset,updated_at) VALUES(?,?,?,?) "
            "ON CONFLICT(signal) DO UPDATE SET inode=excluded.inode, offset=excluded.offset, "
            "updated_at=excluded.updated_at",
            (sig, inode, offset, now_iso()),
        )
    return total


def main():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    db = sqlite3.connect(DB_PATH, timeout=30)
    db.executescript(SCHEMA)
    stop = {"v": False}
    signal.signal(signal.SIGTERM, lambda *_: stop.__setitem__("v", True))
    print(f"[loader] watching {OTEL_DIR} -> {DB_PATH}", flush=True)
    while not stop["v"]:
        for sig in SIGNALS:
            try:
                n = step(db, sig)
                if n:
                    print(f"[loader] {sig}: +{n} rows", flush=True)
            except Exception as e:
                print(f"[loader] {sig} error: {e!r}", file=sys.stderr, flush=True)
        time.sleep(POLL)
    db.close()


if __name__ == "__main__":
    main()
