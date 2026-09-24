#!/srv/agent-telemetry/pipeline/venv/bin/python
"""Read-only query helper over the InferHub modeled tables (data/inferhub/modeled/current).

  ih-query.py prices [route-substr ...]   latest book per route: min ask in/out + its avail count,
                                          capacity-weighted median, official, tiers, providers
  ih-query.py book <route> [--side in|out] [--top N]   current order-book tiers of one route
  ih-query.py drift [--policy 0.10] [--hours 24]       min ask vs the $0.10/1M policy threshold:
                                          current, low/high over the window, % of snapshots under
  ih-query.py balance                     balance, today's spend (ET), burn rate + runway
  ih-query.py spend [--day YYYY-MM-DD]    billed spend per route for an ET day (request logs)
  ih-query.py match                       telemetry <-> billing join match rate per route
  ih-query.py status                      latest status per rail
  ih-query.py runs [N]                    last N collector runs (meta/runs.jsonl)
  ih-query.py tables                      modeled tables with row counts
  ih-query.py sql "<query>"               anything else (tables are views by file name)
Memory capped at 256MB, 1 thread. Prices are USD per 1M tokens.
"""

import datetime as dt
import glob
import json
import os
import sys

import duckdb

ROOT = os.environ.get("AT_ROOT", "/srv/agent-telemetry")
IH = os.environ.get("IHUB_DATA", os.path.join(ROOT, "data", "inferhub"))
MOD = os.path.join(IH, "modeled", "current")


def con():
    c = duckdb.connect()
    c.execute("SET memory_limit='256MB'")
    c.execute("SET threads=1")
    c.execute("SET TimeZone='UTC'")
    for f in sorted(glob.glob(os.path.join(MOD, "*.parquet"))):
        name = os.path.basename(f)[:-8]
        c.execute(f"CREATE VIEW {name} AS SELECT * FROM read_parquet('{f}')")
    return c


def show(c, q, params=None):
    rel = c.execute(q, params or [])
    cols = [d[0] for d in rel.description]
    rows = rel.fetchall()
    w = [max(len(str(x)) for x in [col] + [r[i] for r in rows]) for i, col in enumerate(cols)]
    print("  ".join(col.ljust(w[i]) for i, col in enumerate(cols)))
    for r in rows:
        print("  ".join(("" if v is None else str(v)).ljust(w[i]) for i, v in enumerate(r)))
    print(f"({len(rows)} rows)")


def arg(name, default=None):
    if name in sys.argv:
        i = sys.argv.index(name)
        v = sys.argv[i + 1]
        del sys.argv[i : i + 2]
        return v
    return default


def main():
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        print(__doc__)
        return
    cmd = sys.argv[1]
    if cmd == "runs":
        n = int(sys.argv[2]) if len(sys.argv) > 2 else 10
        with open(os.path.join(IH, "meta", "runs.jsonl"), encoding="utf-8") as fh:
            for ln in fh.readlines()[-n:]:
                r = json.loads(ln)
                new = {
                    k: v.get("new")
                    for k, v in (r.get("tables") or {}).items()
                    if isinstance(v, dict) and "new" in v
                }
                print(
                    r.get("finished_at"),
                    r.get("cmd"),
                    f"{r.get('seconds')}s",
                    f"rss={r.get('peak_rss_mb')}MB",
                    "new=" + json.dumps(new),
                    "errors=" + str(len(r.get("errors") or [])),
                )
        return
    c = con()
    if cmd == "tables":
        for (name,) in c.execute(
            "SELECT view_name FROM duckdb_views() WHERE NOT internal ORDER BY 1"
        ).fetchall():
            print(f"{name:32} {c.execute(f'SELECT count(*) FROM {name}').fetchone()[0]:>10}")
        b = os.path.join(MOD, "_build.json")
        if os.path.exists(b):
            print("snapshot", os.path.realpath(MOD), json.load(open(b)).get("built_at"))
    elif cmd == "prices":
        subs = sys.argv[2:] or [""]
        where = " OR ".join("p.route ILIKE ?" for _ in subs)
        show(
            c,
            f"""
          WITH last AS (SELECT * FROM fact_route_price
                        QUALIFY row_number() OVER (PARTITION BY route ORDER BY ts DESC) = 1)
          SELECT p.route, strftime(p.ts, '%m-%d %H:%M') AS ts_utc, p.min_ask_in,
                 p.min_tier_avail_in AS avail_at_min, p.min_ask_out, p.cw_median_ask_in,
                 p.cw_median_ask_out, p.official_in, p.official_out, p.tiers_in,
                 p.avail_total_in AS listings, r.active_providers AS rail_providers, p.enabled
          FROM last p LEFT JOIN (SELECT * FROM fact_rail_snapshot QUALIFY row_number()
               OVER (PARTITION BY rail ORDER BY ts DESC) = 1) r USING (rail)
          WHERE {where} ORDER BY p.route""",
            [f"%{s}%" for s in subs],
        )
    elif cmd == "book":
        side = arg("--side", "in")
        top = int(arg("--top", "15"))
        show(
            c,
            """SELECT side, tier_rank, tier_price, avail_count, cum_avail, official_price,
                          discount_pct, valid_from, last_seen_at
                   FROM fact_price_snapshot WHERE route = ? AND is_current AND side = ?
                   ORDER BY tier_rank LIMIT ?""",
            [sys.argv[2], side, top],
        )
    elif cmd == "drift":
        pol = float(arg("--policy", "0.10"))
        hrs = float(arg("--hours", "24"))
        subs = sys.argv[2:] or ["gpt-6-astra"]
        where = " OR ".join("route ILIKE ?" for _ in subs)
        show(
            c,
            f"""
          SELECT route, arg_max(min_ask_in, ts) AS min_ask_in_now,
                 arg_max(min_tier_avail_in, ts) AS avail_now,
                 arg_max(min_ask_out, ts) AS min_ask_out_now,
                 min(min_ask_in) AS low_in, max(min_ask_in) AS high_in,
                 round(arg_max(min_ask_in, ts) - {pol}, 6) AS in_vs_policy,
                 round(100.0 * count(*) FILTER (WHERE min_ask_in < {pol}) / count(*), 1)
                   AS pct_snaps_in_under_policy,
                 round(100.0 * count(*) FILTER (WHERE min_ask_out < {pol}) / count(*), 1)
                   AS pct_snaps_out_under_policy,
                 count(*) AS snapshots, max(ts) AS last_ts
          FROM fact_route_price
          WHERE ts >= now()::TIMESTAMP - INTERVAL {hrs} HOUR AND ({where})
          GROUP BY route ORDER BY route""",
            [f"%{s}%" for s in subs],
        )
        print(f"policy: 'effectively free' < ${pol}/1M tokens (docs/INFERHUB-API-SETUP.md)")
    elif cmd == "balance":
        show(
            c,
            """
          WITH b AS (SELECT * FROM fact_balance ORDER BY ts DESC LIMIT 1),
          s AS (SELECT sum(billed_cost_usdc) AS spend_24h, count(*) AS reqs_24h
                FROM fact_request_billing WHERE ts >= now()::TIMESTAMP - INTERVAL 24 HOUR),
          s6 AS (SELECT sum(billed_cost_usdc) AS spend_6h
                 FROM fact_request_billing WHERE ts >= now()::TIMESTAMP - INTERVAL 6 HOUR)
          SELECT b.ts, b.balance_usdc, b.today_spend_usdc, b.today_requests_ok, s.spend_24h,
                 s6.spend_6h,
                 round(b.balance_usdc / nullif(s.spend_24h, 0), 2) AS runway_days_at_24h_rate,
                 round(b.balance_usdc / nullif(s6.spend_6h * 4, 0), 2) AS runway_days_at_6h_rate
          FROM b, s, s6""",
        )
    elif cmd == "spend":
        day = arg("--day", dt.datetime.now().date().isoformat())
        show(
            c,
            """SELECT route, count(*) AS requests, count(*) FILTER (WHERE is_error) AS errors,
                          sum(tokens_in) AS tokens_in, sum(tokens_out) AS tokens_out,
                          sum(tokens_cached) AS tokens_cached, sum(billed_cost_usdc) AS billed_usdc
                   FROM fact_request_billing WHERE day_et = ?::DATE
                   GROUP BY ALL ORDER BY billed_usdc DESC NULLS LAST""",
            [day],
        )
        show(
            c,
            "SELECT sum(billed_cost_usdc) AS total_billed_usdc, count(*) AS requests "
            "FROM fact_request_billing WHERE day_et = ?::DATE",
            [day],
        )
    elif cmd == "match":
        show(c, "SELECT * FROM inferhub_match_summary")
    elif cmd == "status":
        show(
            c,
            """SELECT rail, family, state, availability_now_pct, availability_24h_pct,
                          p50_duration_ms, avg_ttft_ms_1h, ts FROM fact_route_status
                   QUALIFY row_number() OVER (PARTITION BY rail ORDER BY ts DESC) = 1
                   ORDER BY rail""",
        )
    elif cmd == "sql":
        show(c, sys.argv[2])
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
