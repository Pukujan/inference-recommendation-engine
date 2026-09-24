"""Pure transforms from InferHub API JSON to flat rows. Standard library only (tested in CI)."""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
from decimal import Decimal, InvalidOperation
from typing import Any

# Fields documented on GET /api/usage/logs rows (OpenAPI 1.0.0, verified 2026-09-24).
LOG_FIELDS = (
    "id",
    "ts",
    "status",
    "http_status",
    "prompt_tokens",
    "completion_tokens",
    "cached_tokens",
    "cache_write_tokens",
    "cost_consumer_usdc",
    "ask_input_per_mtok",
    "ask_output_per_mtok",
    "region",
    "model",
    "upstream_label",
    "ttft_ms",
    "duration_ms",
    "routing_ms",
)
# Any key that looks like seller / provider / price-paid information is also kept verbatim in
# extras_json, so fields InferHub adds later (see the #46 M1 seller-routing investigation) are
# captured without a code change.


def canon(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_hex(data: bytes | str) -> str:
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def num(v: Any) -> float | None:
    """Decimal strings (management API) and JSON numbers (inference API) -> float."""
    if v is None or v == "":
        return None
    try:
        return float(Decimal(str(v)))
    except (InvalidOperation, ValueError):
        return None


def dec_str(v: Any, places: int = 8) -> str | None:
    """Normalise a money/price value to a fixed decimal string (exact, no float noise)."""
    if v is None or v == "":
        return None
    try:
        d = Decimal(str(v))
    except (InvalidOperation, ValueError):
        return None
    return format(d.quantize(Decimal(1).scaleb(-places)), "f")


def iso_ms(epoch_ms: int | float) -> str:
    t = _dt.datetime.fromtimestamp(float(epoch_ms) / 1000.0, _dt.timezone.utc)
    return t.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def parse_ts(s: str | None) -> _dt.datetime | None:
    if not s:
        return None
    s = s.strip().replace("Z", "+00:00")
    try:
        t = _dt.datetime.fromisoformat(s)
    except ValueError:
        return None
    if t.tzinfo is None:
        t = t.replace(tzinfo=_dt.timezone.utc)
    return t.astimezone(_dt.timezone.utc)


def utc_day(ts: str | None) -> str:
    t = parse_ts(ts)
    return t.strftime("%Y-%m-%d") if t else "unknown"


# ---------------------------------------------------------------- catalog (order book) ----------


def price_points(m: dict[str, Any], side: str) -> list[tuple[float, int]]:
    """Order-book tiers for one side ('in'|'out') as sorted (price_per_mtok, avail_count).

    GET /api/catalog returns ``pricePointsIn``/``pricePointsOut`` = [[price, count], ...] (the tiers
    shown on dashboard/models with their 'avail.' counts). The OpenAPI schema documents
    ``asksIn``/``asksOut`` (one entry per live provider); both shapes are accepted.
    """
    key = "pricePointsIn" if side == "in" else "pricePointsOut"
    pts = m.get(key)
    out: dict[float, int] = {}
    if isinstance(pts, list) and pts:
        for p in pts:
            if isinstance(p, (list, tuple)) and len(p) >= 2:
                price = num(p[0])
                if price is None:
                    continue
                out[price] = out.get(price, 0) + int(p[1] or 0)
    else:
        asks = m.get("asksIn" if side == "in" else "asksOut") or []
        for a in asks:
            price = num(a)
            if price is not None:
                out[price] = out.get(price, 0) + 1
    return sorted(out.items())


def catalog_rows(
    catalog: list[dict[str, Any]], fetched_at: str
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """-> (rail_rows, route_rows, tier_rows) for one /api/catalog fetch.

    rail  = upstream (e.g. cb 'CodeBuddy', cx 'Codex') with activeProviders + enabled flags
    route = '<prefix>/<upstreamModelId>' with official prices, min asks, tier count, book_hash
    tier  = one price level on one side with its avail count (route, side, tier_rank, price, avail)
    """
    rails, routes, tiers = [], [], []
    for u in catalog or []:
        prefix = u.get("prefix")
        models = u.get("models") or []
        rails.append(
            {
                "fetched_at": fetched_at,
                "rail": prefix,
                "rail_slug": u.get("slug"),
                "rail_label": u.get("label"),
                "rail_status": u.get("status"),
                "rail_enabled": u.get("enabled"),
                "rail_upstream_disabled": u.get("upstreamDisabled"),
                "active_providers": u.get("activeProviders"),
                "model_count": len(models),
            }
        )
        for m in models:
            route = f"{prefix}/{m.get('upstreamModelId')}"
            book_in = price_points(m, "in")
            book_out = price_points(m, "out")
            official_in = num(m.get("officialIn"))
            official_out = num(m.get("officialOut"))
            enabled = bool(m.get("enabled", True)) and not bool(m.get("modelDisabled", False))
            book = {
                "in": book_in,
                "out": book_out,
                "oi": official_in,
                "oo": official_out,
                "en": enabled,
            }
            book_hash = sha256_hex(canon(book))[:16]
            routes.append(
                {
                    "fetched_at": fetched_at,
                    "route": route,
                    "rail": prefix,
                    "model_label": m.get("label"),
                    "upstream_model_id": m.get("upstreamModelId"),
                    "official_in": official_in,
                    "official_out": official_out,
                    "min_ask_in": book_in[0][0] if book_in else None,
                    "min_ask_out": book_out[0][0] if book_out else None,
                    "min_tier_avail_in": book_in[0][1] if book_in else None,
                    "min_tier_avail_out": book_out[0][1] if book_out else None,
                    "tiers_in": len(book_in),
                    "tiers_out": len(book_out),
                    "avail_total_in": sum(c for _, c in book_in),
                    "avail_total_out": sum(c for _, c in book_out),
                    "enabled": enabled,
                    "model_enabled": m.get("enabled"),
                    "model_disabled": m.get("modelDisabled"),
                    "supports_cache": m.get("supportsCache"),
                    "context_window": m.get("contextWindow"),
                    "max_output_tokens": m.get("maxOutputTokens"),
                    "modalities": ",".join(m.get("modalities") or []),
                    "output_modality": m.get("outputModality"),
                    "reasoning_levels": "|".join(m.get("reasoningLevels") or []),
                    "book_hash": book_hash,
                }
            )
            for side, bk, official in (
                ("in", book_in, official_in),
                ("out", book_out, official_out),
            ):
                cum = 0
                for rank, (price, cnt) in enumerate(bk, start=1):
                    cum += cnt
                    tiers.append(
                        {
                            "observed_at": fetched_at,
                            "route": route,
                            "rail": prefix,
                            "side": side,
                            "tier_rank": rank,
                            "tier_price": price,
                            "avail_count": cnt,
                            "cum_avail": cum,
                            "official_price": official,
                            "discount_pct": round((1 - price / official) * 100, 4)
                            if official
                            else None,
                            "book_hash": book_hash,
                        }
                    )
    return rails, routes, tiers


def changed_routes(routes: list[dict[str, Any]], last_hash: dict[str, str]) -> set[str]:
    """Routes whose book (tiers + counts + official + enabled) differs from the last stored one."""
    return {r["route"] for r in routes if last_hash.get(r["route"]) != r["book_hash"]}


# ---------------------------------------------------------------- market / status / balance -----


def market_rows(market: dict[str, Any], fetched_at: str) -> list[dict[str, Any]]:
    ts = market.get("ts")
    snap = iso_ms(ts) if isinstance(ts, (int, float)) else fetched_at
    return [
        {
            "market_ts": snap,
            "fetched_at": fetched_at,
            "route": m.get("slug"),
            "rail": (m.get("slug") or "").split("/")[0] or None,
            "family": m.get("family"),
            "min_ask_in": num(m.get("minAskIn")),
            "min_ask_out": num(m.get("minAskOut")),
            "max_ask_in": num(m.get("maxAskIn")),
            "max_ask_out": num(m.get("maxAskOut")),
            "last_rate_blended": num(m.get("lastRate")),
        }
        for m in market.get("models") or []
    ]


def status_rows(
    status: dict[str, Any], fetched_at: str
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """-> (current rows per family + 'overall', 2-hour history windows)."""
    upd = status.get("updatedAt") or fetched_at
    cur, hist = [], []
    fams = [dict(status.get("overall") or {}, prefix="*", slug="overall", family="overall")]
    fams += list(status.get("families") or [])
    for f in fams:
        cur.append(
            {
                "status_updated_at": upd,
                "fetched_at": fetched_at,
                "rail": f.get("prefix"),
                "rail_slug": f.get("slug"),
                "family": f.get("family"),
                "state": f.get("state"),
                "availability_now_pct": num(f.get("currentAvailabilityPct")),
                "availability_24h_pct": num(f.get("availability24hPct")),
                "availability_7d_pct": num(f.get("availability7dPct")),
                "p50_duration_ms": num(f.get("p50DurationMs")),
                "p95_duration_ms": num(f.get("p95DurationMs")),
                "avg_ttft_ms_1h": num(f.get("avgTtftMs1h")),
                "avg_tps_1h": num(f.get("avgTps1h")),
            }
        )
        for h in f.get("history") or []:
            hist.append(
                {
                    "rail": f.get("prefix"),
                    "rail_slug": f.get("slug"),
                    "window_start": h.get("start"),
                    "availability_pct": num(h.get("availabilityPct")),
                    "state": h.get("state"),
                    "status_updated_at": upd,
                }
            )
    return cur, hist


def balance_row(usage: dict[str, Any], fetched_at: str) -> dict[str, Any]:
    bal = usage.get("balance") or {}
    win = usage.get("window") or {}
    at = usage.get("all_time") or {}
    return {
        "fetched_at": fetched_at,
        "balance_usdc": dec_str(bal.get("amount_usdc"), 6),
        "balance_updated_at": bal.get("updated_at"),
        "window_kind": win.get("kind"),
        "window_tz": win.get("tz"),
        "window_since": win.get("since"),
        "window_requests_ok": win.get("requests"),
        "window_prompt_tokens": win.get("prompt_tokens"),
        "window_completion_tokens": win.get("completion_tokens"),
        "window_spend_usdc": dec_str(win.get("spend_usdc"), 6),
        "all_time_spend_usdc": dec_str(at.get("spend_usdc"), 6),
        "all_time_requests_ok": at.get("requests"),
    }


# ---------------------------------------------------------------- request logs (billing) --------


def log_row(r: dict[str, Any], fetched_at: str) -> dict[str, Any]:
    out: dict[str, Any] = {k: r.get(k) for k in LOG_FIELDS}
    out["cost_consumer_usdc"] = dec_str(r.get("cost_consumer_usdc"), 6)
    out["ask_input_per_mtok"] = dec_str(r.get("ask_input_per_mtok"), 8)
    out["ask_output_per_mtok"] = dec_str(r.get("ask_output_per_mtok"), 8)
    extras = {k: v for k, v in r.items() if k not in LOG_FIELDS}
    out["extras_json"] = canon(extras) if extras else None
    out["content_hash"] = sha256_hex(canon(r))[:32]
    out["fetched_at"] = fetched_at
    out["day"] = utc_day(r.get("ts"))
    return out


def page_stop(rows: list[dict[str, Any]], stop_before: _dt.datetime | None) -> bool:
    """True when this (ts-descending) page already reaches rows older than ``stop_before``."""
    if stop_before is None or not rows:
        return False
    oldest = min((parse_ts(r.get("ts")) for r in rows if r.get("ts")), default=None)
    return oldest is not None and oldest < stop_before


def pick_range(since: _dt.datetime, now: _dt.datetime) -> str:
    age_h = (now - since).total_seconds() / 3600.0
    if age_h <= 23.5:
        return "24h"
    if age_h <= 24 * 7 - 1:
        return "7d"
    if age_h <= 24 * 30 - 1:
        return "30d"
    if age_h <= 24 * 90 - 1:
        return "90d"
    return "all"


# ---------------------------------------------------------------- curated snapshot CSV ----------

# Column order of the 2026-09-22 snapshot data/pricing.csv (kept identical so old tooling reads it).
PRICING_CSV_COLUMNS = (
    "model_id",
    "provider",
    "model_label",
    "official_input_usdc_per_1m",
    "official_output_usdc_per_1m",
    "minimum_input_ask_usdc_per_1m",
    "minimum_output_ask_usdc_per_1m",
    "input_price_points_json",
    "output_price_points_json",
    "input_discount_points_json",
    "output_discount_points_json",
    "input_price_point_count",
    "output_price_point_count",
    "maximum_input_available_count",
    "maximum_output_available_count",
    "input_savings_pct",
    "output_savings_pct",
    "supports_cache",
    "context_window",
    "max_output_tokens",
    "modality",
    "reasoning_levels",
)
PROVIDERS_CSV_COLUMNS = (
    "slug",
    "prefix",
    "label",
    "status",
    "enabled",
    "active_providers",
    "model_count",
)


def _fmt(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return "True" if v else "False"
    if isinstance(v, float):
        return format(Decimal(repr(v)).normalize(), "f")
    return str(v)


def _disc(price: float, official: float | None) -> float | None:
    if not official:
        return None
    return float(Decimal(str(round((1 - price / official) * 100, 4))).normalize())


def pricing_csv_rows(
    models: dict[str, Any] | None, catalog: list[dict[str, Any]]
) -> list[list[str]]:
    """Rows in the Sep 22 pricing.csv format: /v1/models ids (incl. aliases) + /catalog tiers."""
    cat: dict[str, dict[str, Any]] = {}
    for u in catalog or []:
        for m in u.get("models") or []:
            cat[f"{u.get('prefix')}/{m.get('upstreamModelId')}"] = m
    ids: list[tuple[str, dict[str, Any]]] = []
    if models and models.get("data"):
        ids = [(d.get("id"), d) for d in models["data"]]
        seen = {i for i, _ in ids}
        ids += [(k, {}) for k in sorted(cat) if k not in seen]
    else:
        ids = [(k, {}) for k in sorted(cat)]
    rows = []
    for mid, d in ids:
        m = cat.get(mid, {})
        p = d.get("pricing") or {}
        oi = num(m.get("officialIn")) if m else num(p.get("official_in"))
        oo = num(m.get("officialOut")) if m else num(p.get("official_out"))
        if oi is None:
            oi = 0.0 if d.get("owned_by") == "alias" else None
        if oo is None:
            oo = 0.0 if d.get("owned_by") == "alias" else None
        bi = price_points(m, "in") if m else []
        bo = price_points(m, "out") if m else []
        prefix = d.get("owned_by") or mid.split("/")[0]
        label = m.get("label") if m else None
        mods = ",".join(m.get("modalities") or []) if m else (d.get("modality") or "")
        rl = m.get("reasoningLevels") if m else d.get("reasoning_levels")
        rows.append(
            [
                mid,
                prefix,
                _fmt(label),
                _fmt(oi),
                _fmt(oo),
                _fmt(bi[0][0]) if bi else "",
                _fmt(bo[0][0]) if bo else "",
                json.dumps([[p_, c] for p_, c in bi]),
                json.dumps([[p_, c] for p_, c in bo]),
                json.dumps([[p_, c, _disc(p_, oi)] for p_, c in bi]),
                json.dumps([[p_, c, _disc(p_, oo)] for p_, c in bo]),
                str(len(bi)),
                str(len(bo)),
                _fmt(max((c for _, c in bi), default=None)),
                _fmt(max((c for _, c in bo), default=None)),
                _fmt(_disc(bi[0][0], oi)) if bi else "",
                _fmt(_disc(bo[0][0], oo)) if bo else "",
                _fmt(bool(m.get("supportsCache")) if m else bool(d.get("supports_cache", False))),
                _fmt(m.get("contextWindow") if m else d.get("input_token_limit")),
                _fmt(m.get("maxOutputTokens") if m else d.get("max_output_tokens")),
                mods,
                "|".join(rl or []),
            ]
        )
    return rows


def providers_csv_rows(catalog: list[dict[str, Any]]) -> list[list[str]]:
    return [
        [
            _fmt(u.get("slug")),
            _fmt(u.get("prefix")),
            _fmt(u.get("label")),
            _fmt(u.get("status")),
            _fmt(u.get("enabled")),
            _fmt(u.get("activeProviders")),
            str(len(u.get("models") or [])),
        ]
        for u in catalog or []
    ]
