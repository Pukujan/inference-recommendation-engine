"""Curated daily snapshot for GitHub (small, dated, hash-pointed at the local raw files).

For an ET calendar day D (default: today) writes data/inferhub/exports/D/:
  pricing.csv              exact column set of the 2026-09-22 snapshot data/pricing.csv (order-book
                           tiers as [[price, avail_count], ...]) from the day's LAST catalog fetch
  providers.csv            rails, same columns as the 2026-09-22 providers.csv
  route-daily-summary.csv  per route over the whole ET day: min/max of the min ask, capacity-weighted
                           median, snapshots seen, share of snapshots under the policy threshold
  route-catalogue.json/.csv  reliability-adjusted route catalogue (IRE #46 M4, schema
                           schemas/route-catalogue.v1.schema.json), regenerated at export time;
                           only for the current ET day (a past day has no catalogue of its own)
  route-reliability.csv    fact_route_reliability (route x 1h/24h/7d rollups) at export time, same
                           current-day rule
  manifest.json            snapshot time, raw source files (relative path + body sha256 + zst sha256),
                           sha256 of every file, collector git sha, schema
``publish`` additionally commits these files to the data branch (default
``data/inferhub-price-snapshots``, an orphan branch with no CI and no branch protection, so daily
commits do not create PRs on protected main) under inferhub/price-snapshots/<YYYY>/<D>/ and
appends to inferhub/price-snapshots/index.csv. Push auth: the host's existing ``gh`` login (git
credential helper ``gh auth git-credential``); no token is copied or stored by this code.
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import json
import os
import subprocess
from typing import Any
from zoneinfo import ZoneInfo

from . import transform
from .collect import IH, RAW, ROOT
from .rawstore import RawStore

ET = ZoneInfo("America/New_York")
EXPORTS = os.path.join(IH, "exports")
PUBLISH_DIR = os.path.join(IH, "publish", "repo")
REPO_URL = os.environ.get(
    "IHUB_PUBLISH_REPO", "https://github.com/Pukujan/inference-recommendation-engine.git"
)
BRANCH = os.environ.get("IHUB_PUBLISH_BRANCH", "data/inferhub-price-snapshots")
SUBDIR = "inferhub/price-snapshots"


def _manifest_rows() -> list[dict[str, Any]]:
    rows = []
    with open(os.path.join(RAW, "manifest.jsonl"), encoding="utf-8") as fh:
        for ln in fh:
            try:
                rows.append(json.loads(ln))
            except ValueError:
                continue
    return rows


def _et_bounds(day: str) -> tuple[dt.datetime, dt.datetime]:
    d = dt.date.fromisoformat(day)
    start = dt.datetime(d.year, d.month, d.day, tzinfo=ET).astimezone(dt.timezone.utc)
    return start, start + dt.timedelta(days=1)


def _latest(rows: list[dict[str, Any]], endpoint: str, end: dt.datetime) -> dict[str, Any] | None:
    best = None
    for r in rows:
        if r.get("endpoint") != endpoint or not r.get("path") or r.get("http_status") != 200:
            continue
        t = transform.parse_ts(r.get("fetched_at"))
        if t and t < end and (best is None or r["fetched_at"] > best["fetched_at"]):
            best = r
    return best


def _csv(header: tuple[str, ...] | list[str], rows: list[list[Any]]) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf, quoting=csv.QUOTE_ALL, lineterminator="\n")
    w.writerow(header)
    w.writerows(rows)
    return buf.getvalue().encode("utf-8")


def _git_sha() -> str | None:
    try:
        with open(os.path.join(ROOT, "DEPLOYED"), encoding="utf-8") as fh:
            for ln in fh:
                if ln.startswith("sha="):
                    return ln.strip()[4:]
    except OSError:
        pass
    return None


def _summary(day: str) -> tuple[list[str], list[list[Any]]]:
    path = os.path.join(IH, "modeled", "current", "fact_route_price.parquet")
    header = [
        "day_et",
        "route",
        "rail",
        "official_in",
        "official_out",
        "snapshots",
        "min_ask_in_low",
        "min_ask_in_high",
        "min_ask_out_low",
        "min_ask_out_high",
        "cw_median_ask_in_avg",
        "cw_median_ask_out_avg",
        "min_tier_avail_in_last",
        "avail_total_in_last",
        "pct_snapshots_min_ask_in_under_policy",
    ]
    if not os.path.exists(path):
        return header, []
    from . import lake

    policy = float(os.environ.get("IHUB_POLICY_PER_MTOK", "0.10"))
    con = lake.connect()
    con.execute("SET TimeZone='UTC'")
    start, end = _et_bounds(day)
    q = f"""
      SELECT '{day}', route, any_value(rail), arg_max(official_in, ts), arg_max(official_out, ts),
             count(*), min(min_ask_in), max(min_ask_in), min(min_ask_out), max(min_ask_out),
             round(avg(cw_median_ask_in), 6), round(avg(cw_median_ask_out), 6),
             arg_max(min_tier_avail_in, ts), arg_max(avail_total_in, ts),
             round(100.0 * count(*) FILTER (WHERE min_ask_in < {policy}) / count(*), 2)
      FROM read_parquet('{path}')
      WHERE ts >= '{start:%Y-%m-%d %H:%M:%S}' AND ts < '{end:%Y-%m-%d %H:%M:%S}'
      GROUP BY route ORDER BY route"""
    rows = [[transform._fmt(v) for v in r] for r in con.execute(q).fetchall()]
    return header, rows


def _catalogue_files() -> tuple[dict[str, Any] | None, dict[str, bytes]]:
    """Fresh route catalogue + reliability rollup for the export (never fails the export)."""
    from . import catalogue, lake

    out: dict[str, bytes] = {}
    try:
        res = catalogue.run(IH, _git_sha())
        for n in ("route-catalogue.json", "route-catalogue.csv"):
            with open(os.path.join(res["dir"], n), "rb") as fh:
                out[n] = fh.read()
        rel = os.path.join(IH, "modeled", "current", "fact_route_reliability.parquet")
        if os.path.exists(rel):
            con = lake.connect()
            con.execute("SET TimeZone='UTC'")
            q = con.execute(f"SELECT * FROM read_parquet('{rel}') ORDER BY route, window_hours")
            cols = [d[0] for d in q.description]
            rows = [[transform._fmt(v) for v in r] for r in q.fetchall()]
            out["route-reliability.csv"] = _csv(cols, rows)
        with open(os.path.join(os.path.dirname(__file__), catalogue.SCHEMA_FILE), "rb") as fh:
            schema = fh.read()
        info = {
            "schema": catalogue.SCHEMA_ID,
            "schema_file": "operational/telemetry/gravebuster/pipeline/ihub/"
            + catalogue.SCHEMA_FILE,
            "schema_sha256": transform.sha256_hex(schema),
            "generated_at": res["generated_at"],
            "status_counts": res["status_counts"],
            "routes": res["routes"],
        }
        return info, out
    except Exception as e:  # price snapshot still publishes
        return {"error": f"{type(e).__name__}: {e}"}, out


def export(day: str | None = None) -> dict[str, Any]:
    day = day or dt.datetime.now(ET).date().isoformat()
    start, end = _et_bounds(day)
    mrows = _manifest_rows()
    cat_rec = _latest(mrows, "catalog", end)
    if cat_rec is None or transform.parse_ts(cat_rec["fetched_at"]) < start:  # type: ignore[operator]
        raise RuntimeError(f"no catalog fetch inside ET day {day}")
    mod_rec = _latest(mrows, "models", end)
    raw = RawStore(RAW)
    catalog = json.loads(raw.read(cat_rec["path"]))
    models = json.loads(raw.read(mod_rec["path"])) if mod_rec else None
    files = {
        "pricing.csv": _csv(
            transform.PRICING_CSV_COLUMNS, transform.pricing_csv_rows(models, catalog)
        ),
        "providers.csv": _csv(
            transform.PROVIDERS_CSV_COLUMNS, transform.providers_csv_rows(catalog)
        ),
    }
    sh, srows = _summary(day)
    files["route-daily-summary.csv"] = _csv(sh, srows)
    cat_info = None
    if day == dt.datetime.now(ET).date().isoformat():
        cat_info, extra = _catalogue_files()
        files.update(extra)
    out = os.path.join(EXPORTS, day)
    os.makedirs(out, exist_ok=True)
    for name, data in files.items():
        with open(os.path.join(out, name), "wb") as fh:
            fh.write(data)

    def src(r: dict[str, Any] | None) -> dict[str, Any] | None:
        if not r:
            return None
        keep = ("endpoint", "fetched_at", "path", "sha256", "bytes", "zst_sha256", "zst_bytes")
        return {k: r.get(k) for k in keep} | {"url": r.get("url")}

    manifest = {
        "schema": "ihub-daily-snapshot/v1",
        "day_et": day,
        "snapshot_utc": cat_rec["fetched_at"],
        "generated_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "units": "USD per 1M tokens; avail_count = seller accounts listed at that price tier",
        "note": "Asks are listed prices, not guaranteed; InferHub may route to a higher tier "
        "when cheaper sellers are at their concurrency limit. Served prices are in request logs.",
        "raw_host": "gravebuster:/srv/agent-telemetry/data/inferhub/raw",
        "sources": {"catalog": src(cat_rec), "models": src(mod_rec)},
        "files": {
            n: {"sha256": transform.sha256_hex(d), "bytes": len(d)}
            | ({"rows": d.count(b"\n") - 1} if n.endswith(".csv") else {})
            for n, d in files.items()
        },
        "catalogue": cat_info,
        "collector_git_sha": _git_sha(),
    }
    with open(os.path.join(out, "manifest.json"), "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=1, sort_keys=True)
        fh.write("\n")
    return {"day": day, "dir": out, "manifest": manifest}


def _git(*args: str, cwd: str = PUBLISH_DIR, check: bool = True) -> str:
    helper = ["-c", "credential.helper=", "-c", "credential.helper=!gh auth git-credential"]
    p = subprocess.run(["git", *helper, *args], cwd=cwd, capture_output=True, text=True)
    if check and p.returncode != 0:
        raise RuntimeError(f"git {args[0]} failed: {p.stderr.strip()[-400:]}")
    return p.stdout.strip()


README = """# InferHub daily price snapshots (data branch)

Orphan data branch of Pukujan/inference-recommendation-engine written once a day by the
GET-only collector on the telemetry host (IRE issue #46). Not merged into `main`.

- `inferhub/price-snapshots/<YYYY>/<YYYY-MM-DD>/pricing.csv`: order book per route from the last
  catalog fetch of that ET day. Same columns as the 2026-09-22 `data/pricing.csv` snapshot;
  `*_price_points_json` = `[[price_usd_per_1M, avail_count], ...]`, `*_discount_points_json` adds the
  discount % vs the official price.
- `providers.csv`: rails (cb, cx, ...) with active provider counts.
- `route-daily-summary.csv`: per route over the ET day (min ask range, capacity-weighted median,
  snapshots, % of snapshots under the $0.10/1M policy threshold).
- `manifest.json`: sha256 of each CSV and of the raw API responses they came from (raw stays on the
  telemetry host under data/inferhub/raw, append-only, zstd).
- `route-catalogue.json` / `.csv`, `route-reliability.csv` (from 2026-09-24): reliability-adjusted
  route catalogue (static Top 20 / daily shortlist joined with live price and rolling 1h/24h/7d
  request-log reliability; statuses are evidence-backed hypotheses) as of the publish time.
  Latest copy: `inferhub/route-catalogue/latest/`; JSON Schema: `inferhub/schemas/`.
- `index.csv`: one line per day.

Prices are listed asks, not guarantees. Code, schemas and docs live on `main` under
`operational/telemetry/gravebuster/pipeline/ihub/`.
"""


def publish_snapshot(res: dict[str, Any]) -> dict[str, Any]:
    day = res["day"]
    os.makedirs(os.path.dirname(PUBLISH_DIR), exist_ok=True)
    if not os.path.isdir(os.path.join(PUBLISH_DIR, ".git")):
        subprocess.run(["git", "init", "-q", PUBLISH_DIR], check=True)
        _git("remote", "add", "origin", REPO_URL)
    remote_has = bool(_git("ls-remote", "--heads", "origin", BRANCH))
    if remote_has:
        _git("fetch", "-q", "--depth", "50", "origin", f"{BRANCH}:refs/remotes/origin/{BRANCH}")
        _git("checkout", "-q", "-B", BRANCH, f"origin/{BRANCH}")
    elif _git("rev-parse", "--verify", "-q", "HEAD", check=False) == "":
        _git("checkout", "-q", "--orphan", BRANCH)
    dest = os.path.join(PUBLISH_DIR, SUBDIR, day[:4], day)
    os.makedirs(dest, exist_ok=True)
    names = [*res["manifest"]["files"], "manifest.json"]
    for name in names:
        with (
            open(os.path.join(res["dir"], name), "rb") as src,
            open(os.path.join(dest, name), "wb") as d,
        ):
            d.write(src.read())
    if "route-catalogue.json" in names:
        # stable paths for agents: latest published catalogue + its schema
        from . import catalogue

        latest = os.path.join(PUBLISH_DIR, "inferhub", "route-catalogue", "latest")
        schemas = os.path.join(PUBLISH_DIR, "inferhub", "schemas")
        os.makedirs(latest, exist_ok=True)
        os.makedirs(schemas, exist_ok=True)
        for name in ("route-catalogue.json", "route-catalogue.csv", "route-reliability.csv"):
            if name in names:
                with open(os.path.join(res["dir"], name), "rb") as src:
                    data = src.read()
                with open(os.path.join(latest, name), "wb") as d:
                    d.write(data)
        with open(os.path.join(os.path.dirname(__file__), catalogue.SCHEMA_FILE), "rb") as src:
            data = src.read()
        with open(os.path.join(schemas, os.path.basename(catalogue.SCHEMA_FILE)), "wb") as d:
            d.write(data)
    with open(os.path.join(PUBLISH_DIR, "README.md"), "w", encoding="utf-8") as fh:
        fh.write(README)
    idx = os.path.join(PUBLISH_DIR, SUBDIR, "index.csv")
    m = res["manifest"]
    lines: list[str] = []
    if os.path.exists(idx):
        with open(idx, encoding="utf-8") as fh:
            lines = [ln for ln in fh.read().splitlines() if ln and not ln.startswith(day + ",")]
    if not lines or not lines[0].startswith("day_et,"):
        lines.insert(0, "day_et,snapshot_utc,routes,pricing_sha256,catalog_raw_sha256")
    lines.append(
        ",".join(
            [
                day,
                m["snapshot_utc"],
                str(m["files"]["pricing.csv"]["rows"]),
                m["files"]["pricing.csv"]["sha256"],
                (m["sources"]["catalog"] or {}).get("sha256") or "",
            ]
        )
    )
    lines = [lines[0], *sorted(lines[1:])]
    with open(idx, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    _git("add", "-A")
    if not _git("status", "--porcelain"):
        return {"published": False, "reason": "no changes"}
    ident = [
        "-c",
        "user.name=agent-telemetry ihub (gravebuster)",
        "-c",
        "user.email=agent-telemetry@gravebuster.invalid",
    ]
    _git(
        *ident,
        "commit",
        "-q",
        "-m",
        f"data: InferHub price snapshot + route catalogue {day} (ET)\n\n"
        f"snapshot {m['snapshot_utc']}, pricing.csv sha256 {m['files']['pricing.csv']['sha256']}\n"
        "Part of #46",
    )
    _git("push", "-q", "origin", f"HEAD:refs/heads/{BRANCH}")
    return {"published": True, "branch": BRANCH, "commit": _git("rev-parse", "HEAD")}


def run(day: str | None = None, publish: bool = False) -> dict[str, Any]:
    res = export(day)
    out = {
        "day": res["day"],
        "dir": res["dir"],
        "files": res["manifest"]["files"],
        "snapshot_utc": res["manifest"]["snapshot_utc"],
    }
    if publish:
        out["publish"] = publish_snapshot(res)
    return out
