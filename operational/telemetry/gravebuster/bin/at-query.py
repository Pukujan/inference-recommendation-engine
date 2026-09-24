#!/usr/bin/env python3
"""Read-only helper: at-query.py trace <trace_id> | corr <correlation_id> | stats
Queries the loader SQLite and Phoenix's own sqlite DB (needs sudo for the docker volume)."""

import glob
import json
import sqlite3
import sys

print(
    "WARNING: the SQLite loader was retired 2026-09-24 ~21:55 UTC; traces.sqlite3 is a frozen archive. Use bin/at-duck.py (Parquet lake; trace <tid> --live for new data).",
    file=sys.stderr,
)
DB = "file:/srv/agent-telemetry/data/sqlite/traces.sqlite3?mode=ro"
PX = glob.glob("/var/lib/docker/volumes/agent-telemetry_phoenix_data/_data/phoenix.db")
d = sqlite3.connect(DB, uri=True)
cmd = sys.argv[1] if len(sys.argv) > 1 else "stats"


def phoenix_count(tid):
    if not PX:
        return "phoenix.db not found"
    try:
        p = sqlite3.connect(f"file:{PX[0]}?mode=ro", uri=True)
        return p.execute(
            "select count(*) from spans s join traces t on s.trace_rowid=t.id where t.trace_id=?",
            (tid,),
        ).fetchone()[0]
    except Exception as e:
        return f"err {e}"


if cmd == "trace":
    tid = sys.argv[2]
    rows = d.execute(
        "select service_name,count(*) from spans where trace_id=? group by 1", (tid,)
    ).fetchall()
    print("sqlite spans by service:", rows)
    for r in d.execute(
        "select name,span_id,parent_span_id,status_code,status_message,attributes from spans where trace_id=? and service_name='astra-launcher'",
        (tid,),
    ):
        a = json.loads(r[5])
        a.pop("run.stderr_tail", None)
        print("launcher span:", r[0], r[1], "parent=", r[2], "status=", r[3], r[4], a)
    kids = d.execute(
        "select count(*) from spans where trace_id=? and service_name!='astra-launcher' and parent_span_id=(select span_id from spans where trace_id=? and service_name='astra-launcher' and parent_span_id is null limit 1)",
        (tid, tid),
    ).fetchone()[0]
    print("codex spans whose parent is the launcher root:", kids)
    print(
        "logs for trace:",
        d.execute(
            "select service_name,count(*) from logs where trace_id=? group by 1", (tid,)
        ).fetchall(),
    )
    print("phoenix spans for trace:", phoenix_count(tid))
elif cmd == "corr":
    c = sys.argv[2]
    print(
        d.execute(
            "select trace_id,service_name,count(*),min(start_time),max(end_time) from spans where correlation_id=? group by 1,2",
            (c,),
        ).fetchall()
    )
    print(
        "logs:",
        d.execute(
            "select service_name,event_name,count(*) from logs where correlation_id=? group by 1,2 order by 3 desc limit 15",
            (c,),
        ).fetchall(),
    )
else:
    for t in ("spans", "logs", "metrics"):
        print(
            t,
            d.execute(f"select count(*) from {t}").fetchone()[0],
            d.execute(
                f"select service_name,harness,count(*) from {t} group by 1,2 order by 3 desc limit 10"
            ).fetchall(),
        )
    print("state:", d.execute("select * from loader_state").fetchall())
