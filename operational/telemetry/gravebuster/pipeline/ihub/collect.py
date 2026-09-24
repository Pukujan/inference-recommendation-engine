"""Collector tiers: ``fast`` (catalog order book, market, status, balance; every 5 min) and
``logs`` (per-request billing rows, every 15 min with an overlap window). GET-only."""

from __future__ import annotations

import datetime as dt
import json
import os
import uuid
from typing import Any

from . import lake, transform
from .api import Client
from .rawstore import RawStore

ROOT = os.environ.get("AT_ROOT", "/srv/agent-telemetry")
IH = os.environ.get("IHUB_DATA", os.path.join(ROOT, "data", "inferhub"))
RAW = os.path.join(IH, "raw")
PQ = os.path.join(IH, "parquet")
STATE = os.path.join(IH, "state")
META = os.path.join(IH, "meta")
MODELS_EVERY_S = int(os.environ.get("IHUB_MODELS_EVERY_S", "3300"))
LOG_OVERLAP_MIN = int(os.environ.get("IHUB_LOG_OVERLAP_MIN", "30"))
LOG_MAX_PAGES = int(os.environ.get("IHUB_LOG_MAX_PAGES", "40"))


def now_iso() -> str:
    return (
        dt.datetime.now(dt.timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    )


def run_id() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:6]


def _load(name: str, default: Any) -> Any:
    try:
        with open(os.path.join(STATE, name), encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default


def _save(name: str, obj: Any) -> None:
    os.makedirs(STATE, exist_ok=True)
    p = os.path.join(STATE, name)
    with open(p + ".tmp", "w", encoding="utf-8") as fh:
        json.dump(obj, fh, sort_keys=True)
    os.replace(p + ".tmp", p)


def _day(field: str) -> Any:
    return lambda r: transform.utc_day(r.get(field))


class Ctx:
    def __init__(self) -> None:
        for d in (RAW, PQ, STATE, META):
            os.makedirs(d, exist_ok=True)
        self.client = Client()
        self.raw = RawStore(RAW)
        self.run_id = run_id()
        self.con = lake.connect()
        self.stats: dict[str, Any] = {}
        self.errors: list[str] = []

    def fetch(
        self,
        endpoint: str,
        path: str,
        params: dict[str, Any] | None = None,
        auth: bool = True,
        dedup: bool = True,
        meta: dict[str, Any] | None = None,
    ) -> tuple[Any, dict[str, Any] | None, str]:
        fetched_at = now_iso()
        r = self.client.get(path, params=params, auth=auth)
        if r.status != 200:
            self.errors.append(f"{endpoint}: {r.error}")
            return None, None, fetched_at
        m = dict(
            meta or {},
            elapsed_ms=round(r.elapsed_ms, 1),
            attempts=r.attempts,
            ratelimit={k: v for k, v in r.headers.items() if k.startswith("x-ratelimit")},
        )
        rec = self.raw.put(endpoint, r.body, fetched_at, r.url, r.status, m, dedup)
        try:
            return json.loads(r.body), rec, fetched_at
        except ValueError as e:
            self.errors.append(f"{endpoint}: bad json {e}")
            return None, rec, fetched_at

    def append(
        self, table: str, rows: list[dict[str, Any]], key: Any, day: Any, with_hash: bool = False
    ) -> None:
        st = lake.append(self.con, PQ, table, rows, key, day, self.run_id, with_hash)
        agg = self.stats.setdefault(table, {"staged": 0, "new": 0, "dup": 0, "revised": 0})
        for k, v in st.items():
            agg[k] += v

    def new_rows(self) -> int:
        return sum(v.get("new", 0) + v.get("revised", 0) for v in self.stats.values())


def fast(ctx: Ctx) -> None:
    # 1. order book per route (GET /api/catalog, key) -> rails, routes (every fetch), tiers (CDC)
    cat, rec, at = ctx.fetch("catalog", "/catalog")
    if cat is not None and rec is not None:
        rails, routes, tiers = transform.catalog_rows(cat, at)
        last = _load("book_last.json", {})
        changed = transform.changed_routes(routes, last)
        tiers = [t for t in tiers if t["route"] in changed]
        ctx.append("rails", rails, lambda r: f"{r['fetched_at']}|{r['rail']}", _day("fetched_at"))
        ctx.append(
            "routes", routes, lambda r: f"{r['fetched_at']}|{r['route']}", _day("fetched_at")
        )
        ctx.append(
            "tiers",
            tiers,
            lambda r: f"{r['observed_at']}|{r['route']}|{r['side']}|{r['tier_rank']}",
            _day("observed_at"),
        )
        ctx.append(
            "catalog_fetches",
            [
                {
                    "fetched_at": at,
                    "raw_sha256": rec["sha256"],
                    "raw_path": rec.get("path"),
                    "n_rails": len(rails),
                    "n_routes": len(routes),
                    "n_routes_changed": len(changed),
                    "n_tier_rows_written": len(tiers),
                }
            ],
            lambda r: r["fetched_at"],
            _day("fetched_at"),
        )
        last.update({r["route"]: r["book_hash"] for r in routes})
        _save("book_last.json", last)
    # 2. public market (min/max ask + last traded blended rate)
    mk, rec, at = ctx.fetch("market", "/market", auth=False)
    if mk is not None and not rec.get("dup_of_previous"):  # type: ignore[union-attr]
        ctx.append(
            "market",
            transform.market_rows(mk, at),
            lambda r: f"{r['market_ts']}|{r['route']}",
            _day("market_ts"),
        )
    # 3. public platform status per rail (+ 2h history windows, stored only when changed)
    st, rec, at = ctx.fetch("status", "/status", auth=False)
    if st is not None and not rec.get("dup_of_previous"):  # type: ignore[union-attr]
        cur, hist = transform.status_rows(st, at)
        ctx.append(
            "status",
            cur,
            lambda r: f"{r['status_updated_at']}|{r['rail']}",
            _day("status_updated_at"),
        )
        ctx.append(
            "status_history",
            hist,
            lambda r: f"{r['rail']}|{r['window_start']}|{r['availability_pct']}|{r['state']}",
            _day("window_start"),
        )
    # 4. balance + today's (ET) spend
    us, rec, at = ctx.fetch("me_usage", "/v1/me/usage", {"window": "day", "tz": "America/New_York"})
    if us is not None:
        us.pop("session", None)
        ctx.append(
            "balance",
            [transform.balance_row(us, at)],
            lambda r: r["fetched_at"],
            _day("fetched_at"),
        )
    # 5. hourly, raw only (config/aggregate snapshots, read-only): /v1/models (ids incl. aliases,
    #    used by the daily CSV), budgets (+default, aliases), usage breakdown by rail, cache stats
    hourly = _load("hourly_last.json", {})
    for endpoint, path, params, auth in HOURLY:
        t_last = transform.parse_ts(hourly.get(endpoint, {}).get("fetched_at"))
        age = (dt.datetime.now(dt.timezone.utc) - t_last).total_seconds() if t_last else 1e9
        if age < MODELS_EVERY_S:
            continue
        body, rec, at = ctx.fetch(endpoint, path, params, auth=auth)
        if body is not None and rec is not None:
            hourly[endpoint] = {"fetched_at": at, "path": rec.get("path"), "sha256": rec["sha256"]}
            _save("hourly_last.json", hourly)


HOURLY: list[tuple[str, str, dict[str, Any] | None, bool]] = [
    ("models", "/v1/models", None, True),
    ("budgets", "/budgets", None, True),
    ("budgets_default", "/budgets/default", None, True),
    ("budgets_aliases", "/budgets/aliases", None, True),
    ("usage_breakdown", "/usage/breakdown", {"range": "24h"}, True),
    ("pricing_cache", "/pricing/cache", None, False),
    ("pricing_config", "/pricing/config", None, False),
]


def logs(ctx: Ctx, since: dt.datetime | None = None, until_pages: int | None = None) -> None:
    """Walk /api/usage/logs newest-first until rows are older than the watermark - overlap."""
    now = dt.datetime.now(dt.timezone.utc)
    stt = _load("logs_state.json", {})
    wm = transform.parse_ts(stt.get("max_ts"))
    if since is None:
        since = (wm - dt.timedelta(minutes=LOG_OVERLAP_MIN)) if wm else now - dt.timedelta(hours=24)
    rng = transform.pick_range(since, now)
    max_pages = until_pages or LOG_MAX_PAGES
    rows_all: list[dict[str, Any]] = []
    pages = 0
    totals: dict[str, Any] = {}
    for page in range(1, max_pages + 1):
        body, rec, at = ctx.fetch(
            "usage_logs",
            "/usage/logs",
            {
                "page": page,
                "pageSize": 100,
                "range": rng,
                "sort": "ts",
                "dir": "desc",
                "status": "all",
            },
            dedup=False,
            meta={"page": page, "range": rng, "since": since.isoformat()},
        )
        if body is None:
            break
        pages += 1
        rows = body.get("rows") or []
        if page == 1:
            totals = {
                k: body.get(k)
                for k in ("total", "rangeTotal", "totalCostUsdc", "totalTokens", "range")
            }
        rows_all += [transform.log_row(r, at) for r in rows]
        if (
            not rows
            or transform.page_stop(rows, since)
            or page * 100 >= int(body.get("total") or 0)
        ):
            break
    else:
        ctx.errors.append(f"usage_logs: stopped at max pages {max_pages}; older rows not fetched")
    ctx.append("billing", rows_all, lambda r: r["id"], lambda r: r["day"], with_hash=True)
    mx = max((r["ts"] for r in rows_all if r.get("ts")), default=None)
    if mx and (wm is None or transform.parse_ts(mx) > wm):  # type: ignore[operator]
        stt["max_ts"] = mx
    stt["last_run"] = {
        "at": now_iso(),
        "since": since.isoformat(),
        "range": rng,
        "pages": pages,
        "rows": len(rows_all),
        "api_totals": totals,
    }
    _save("logs_state.json", stt)
    ctx.stats["_logs"] = stt["last_run"]
