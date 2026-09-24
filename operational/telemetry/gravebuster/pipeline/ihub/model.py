"""Build the InferHub modeled tables with DuckDB into data/inferhub/modeled/snap-<run>/ and swap
the ``current`` symlink atomically. Kept outside the shared dbt project on purpose: the collector
runs every 5 min and must not trigger a full dbt snapshot rebuild of the agent-run marts; the
tables read (never write) data/lake/modeled/current/fact_model_requests.parquet for the join."""

from __future__ import annotations

import glob
import json
import os
import shutil
import time
from typing import Any

from . import lake
from .collect import IH, PQ, ROOT

SQL_DIR = os.path.join(os.path.dirname(__file__), "sql")
MODELED = os.path.join(IH, "modeled")
LAKE_MODELED = os.environ.get("AT_LAKE_MODELED", os.path.join(ROOT, "data", "lake", "modeled"))
POLICY_PER_MTOK = os.environ.get("IHUB_POLICY_PER_MTOK", "0.10")
KEEP = 3
# build order matters (later marts read earlier ones)
MARTS = [
    "inferhub_dim_route",
    "fact_price_snapshot",
    "fact_route_price",
    "fact_rail_snapshot",
    "fact_market_quote",
    "fact_route_status",
    "fact_route_status_history",
    "fact_balance",
    "fact_request_billing",
    "inferhub_request_match",
    "inferhub_match_summary",
]
NEEDS = {
    "inferhub_dim_route": ["routes"],
    "fact_price_snapshot": ["tiers", "routes"],
    "fact_route_price": ["routes", "tiers"],
    "fact_rail_snapshot": ["rails"],
    "fact_market_quote": ["market"],
    "fact_route_status": ["status"],
    "fact_route_status_history": ["status_history"],
    "fact_balance": ["balance"],
    "fact_request_billing": ["billing", "tiers", "routes"],
    "inferhub_request_match": ["billing", "tiers", "routes", "@fmr"],
    "inferhub_match_summary": ["billing", "tiers", "routes", "@fmr"],
}


def _sql(name: str) -> str:
    with open(os.path.join(SQL_DIR, name + ".sql"), encoding="utf-8") as fh:
        return fh.read().replace("{POLICY_PER_MTOK}", POLICY_PER_MTOK)


def _has(table: str) -> bool:
    return bool(glob.glob(os.path.join(PQ, table, "day=*", "*.parquet")))


def build(run_id: str) -> dict[str, Any]:
    t0 = time.time()
    con = lake.connect()
    con.execute("SET TimeZone='UTC'")
    src = _sql("inferhub_src").replace("{PQ}", PQ)
    rev = (
        "UNION ALL BY NAME SELECT *, true AS is_revision FROM "
        f"read_parquet('{PQ}/billing_revisions/day=*/*.parquet', union_by_name=true)"
        if _has("billing_revisions")
        else ""
    )
    src = src.replace("{BILLING_REVISIONS}", rev)
    for stmt in src.split(";"):
        body = "\n".join(ln for ln in stmt.splitlines() if not ln.strip().startswith("--"))
        if not body.strip():
            continue
        view = body.split("VIEW", 1)[1].split()[0]
        table = view.replace("src_", "").replace("_all", "")
        if _has(table):
            con.execute(body)
    fmr = os.path.join(LAKE_MODELED, "current", "fact_model_requests.parquet")
    have_fmr = os.path.exists(fmr)
    if have_fmr:
        con.execute(f"CREATE VIEW fmr AS SELECT * FROM read_parquet('{fmr}')")
    tmp = os.path.join(MODELED, f".tmp-{run_id}")
    os.makedirs(tmp, exist_ok=True)
    counts: dict[str, int] = {}
    skipped: list[str] = []
    for mart in MARTS:
        needs = NEEDS[mart]
        if any((n == "@fmr" and not have_fmr) or (n != "@fmr" and not _has(n)) for n in needs):
            skipped.append(mart)
            continue
        con.execute(f"CREATE OR REPLACE TABLE {mart} AS {_sql(mart)}")
        counts[mart] = con.execute(f"SELECT count(*) FROM {mart}").fetchone()[0]
        con.execute(f"COPY {mart} TO '{tmp}/{mart}.parquet' (FORMAT parquet, COMPRESSION zstd)")
    info = {
        "run_id": run_id,
        "built_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "counts": counts,
        "skipped": skipped,
        "fact_model_requests": fmr if have_fmr else None,
        "policy_per_mtok": POLICY_PER_MTOK,
        "seconds": round(time.time() - t0, 2),
    }
    with open(os.path.join(tmp, "_build.json"), "w", encoding="utf-8") as fh:
        json.dump(info, fh, indent=1)
    snap = os.path.join(MODELED, f"snap-{run_id}")
    os.replace(tmp, snap)
    link = os.path.join(MODELED, "current")
    os.symlink(os.path.basename(snap), link + ".new")
    os.replace(link + ".new", link)
    snaps = sorted(glob.glob(os.path.join(MODELED, "snap-*")))
    for old in snaps[:-KEEP]:
        shutil.rmtree(old, ignore_errors=True)
    return info
