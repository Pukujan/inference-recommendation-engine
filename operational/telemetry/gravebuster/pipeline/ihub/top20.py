"""Cheap Top 20 recommendation list, rebuilt in the repo from live InferHub data (IRE #76).

The Top 20 used to be copied in from an off-repo research workspace that was wiped on
2026-10-03, so it stopped changing after 2026-09-22. This module regenerates
``lists/research_model_top20_recommendations.csv`` at the same path, with the same 21 columns in
the same order and the same cell conventions (``true``/``false``, ``; ``-joined lists, LF line
endings), so claude-code-launcher and the litellm bot keep reading it unchanged.

Inputs are the same three GET bodies ``frontier.py`` fetches (``/api/catalog`` with the key,
``/api/status`` and ``/api/market`` public), so one fetch feeds both lists:

    frontier.py --raw-dir DIR --fetch --env-file F   # GET once, writes the frontier list
    top20.py --raw-dir DIR                           # reuses DIR, writes the Top 20

Capability, tier and release date come from ``top20_prior.v1.json``: the values of the last
off-repo list, carried over as a low-confidence prior because the benchmark inputs behind them
were lost. Price, provider breadth, supply depth and 7-day availability are live. The shortlist
score reuses weights calibrated against the 2026-09-22 list (see ``METHOD``). Each row is a
hypothesis; the sidecar ``research_model_top20_recommendations.json`` carries per-row
confidence, best route, live prices, health, gate reasons and provenance.

``build`` and the writers are standard library only so CI can test them offline. The key is
read by ``frontier.fetch_live`` and only ever sent as a GET header; nothing here prints it.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import io
import json
import math
import os
import sys
from typing import Any

try:  # package import on the host; file import (tests, CLI) falls back to a sibling load
    from . import frontier as F
except ImportError:  # pragma: no cover
    import importlib.util as _ilu

    _spec = _ilu.spec_from_file_location(
        "ihub_frontier", os.path.join(os.path.dirname(os.path.abspath(__file__)), "frontier.py")
    )
    assert _spec and _spec.loader
    F = _ilu.module_from_spec(_spec)
    _spec.loader.exec_module(F)

SCHEMA_ID = "ihub-top20-recommendations/v1"
HERE = os.path.dirname(os.path.abspath(__file__))
LISTS_DIR = os.path.join(HERE, "lists")
PRIOR_FILE = os.path.join(HERE, "top20_prior.v1.json")
OUT_CSV = "research_model_top20_recommendations.csv"
OUT_JSON = "research_model_top20_recommendations.json"
GENERATOR = "operational/telemetry/gravebuster/pipeline/ihub/top20.py"
TOP_N = 20

# Byte-compatible with the 2026-09-22 file: same names, same order. Do not reorder or rename.
CSV_COLUMNS = [
    "recommendation_rank", "model_family", "vendor", "recommendation_eligible", "gate_reasons",
    "tier", "capability_score_100", "release_date", "recency_days", "priced_provider_count",
    "catalog_availability_score_100", "price_supply_availability_score_100",
    "public_availability_7d_pct", "supply_weighted_median_cost_usdc_per_1m",
    "price_utility_score_100", "price_regime", "reliability_weight_boost", "reliability_score_100",
    "performance_evidence_status", "shortlist_score_100", "model_ids",
]  # fmt: skip

# Supply weighting: a port of src/supply.mjs DEFAULT_SUPPLY_POLICY (policy.example.json).
SUPPLY = {
    "availability_exponent": 1.0,
    "distribution_weight": 0.5,
    "derivative_weight": 0.25,
    "derivative_cap": 3.0,
    "lower_price_preference_weight": 0.5,
    "input_weight": 0.4,
    "output_weight": 0.6,
}

METHOD: dict[str, Any] = {
    "near_free_cost_cap_usd_per_mtok": 0.10,
    "price_utility": "100 * min(1, cap / cost); cost <= cap is near_free, no price is unpriced (0)",
    "catalog_availability": "100 * (ln(1 + providers) / ln(7)) ** 0.8, capped at 100",
    "price_supply_availability": "percentile rank (0-100) of total live input listings among "
    "priced families",
    "public_availability_7d": "InferHub /api/status availability7dPct of the best route's rail",
    "reliability": "equal to public_availability_7d_pct (no private probes in the repo)",
    "reliability_weight_boost": "0.15 when price_regime is near_free, else 0",
    "recency_tau_days": 90,
    "shortlist_weights": {
        "price_utility": 0.265,
        "reliability": 0.21,
        "catalog_availability": 0.14,
        "price_supply_availability": 0.10,
        "capability": 0.11,
        "recency": 0.14,
        "boost_points_per_unit": 20.0,
    },
    "shortlist_calibration": "weights fitted (non-negative least squares) to the 2026-09-22 "
    "list's own component columns; they reproduce its shortlist_score_100 within about 1 point "
    "(RMSE 0.5). The component formulas themselves are reconstructions, so live scores are not "
    "comparable point for point with the old list.",
    "gates": {
        "insufficient_provider_breadth": "priced_provider_count < 2",
        "catalog_availability_below_minimum": "catalog_availability_score_100 < 55",
        "release_date_unknown": "no release_date in the prior",
        "tier_below_minimum": "tier is unscored (family not in the prior)",
        "capability_below_minimum": "capability_score_100 < 20",
        "missing_price": "no live input and output price ladder",
        "not_routing_eligible": "no live route is healthy (frontier.route_health)",
    },
    "ranking": "shortlist_score_100 desc, then supply_weighted_median_cost asc, then "
    "model_family; gated rows stay visible, recommendation_eligible marks the usable ones",
    "model_ids_order": "best route first: health status, then route supply-weighted cost",
}
MIN_CATALOG_AVAILABILITY = 55.0
MIN_CAPABILITY = 20.0
MIN_PROVIDERS = 2


# ---------------------------------------------------------------- formatting
def fmt(v: Any, nd: int = 6) -> str:
    """Compact numbers like the old file: 100 not 100.0, at most nd decimals."""
    if v is None:
        return ""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        s = f"{round(v, nd):.{nd}f}".rstrip("0").rstrip(".")
        return "0" if s in ("-0", "") else s
    return str(v)


# ---------------------------------------------------------------- supply model (src/supply.mjs)
def _ladder(points: list[list[Any]] | None) -> list[tuple[float, float]]:
    out = []
    for p in points or []:
        try:
            price, n = float(p[0]), float(p[1])
        except (TypeError, ValueError, IndexError):
            continue
        if price > 0 and n >= 0 and math.isfinite(price) and math.isfinite(n):
            out.append((price, n))
    return sorted(out, key=lambda x: (x[0], -x[1]))


def _quantile(values: list[float], q: float) -> float | None:
    vs = sorted(v for v in values if math.isfinite(v))
    if not vs:
        return None
    pos = (len(vs) - 1) * min(1.0, max(0.0, q))
    lo, hi = math.floor(pos), math.ceil(pos)
    return vs[lo] if lo == hi else vs[lo] + (vs[hi] - vs[lo]) * (pos - lo)


def _derivs(lad: list[tuple[float, float]]) -> list[float]:
    out = [0.0]
    for i in range(1, len(lad)):
        dp = max(math.log(lad[i][0]) - math.log(lad[i - 1][0]), 1e-9)
        out.append(max(0.0, (math.log1p(lad[i][1]) - math.log1p(lad[i - 1][1])) / dp))
    return out[: len(lad)]


def distribution(ladders: list[list[tuple[float, float]]]) -> dict[str, Any]:
    logs: list[float] = []
    ders: list[float] = []
    for lad in ladders:
        logs += [math.log1p(n) for _, n in lad]
        ders += [d for d in _derivs(lad) if d > 0]
    return {
        "sorted_logs": sorted(logs),
        "median_log": _quantile(logs, 0.5) or 0.0,
        "derivative_p90": _quantile(ders, 0.9) or 0.0,
    }


def _pct_rank(sorted_vals: list[float], v: float) -> float:
    if not sorted_vals:
        return 0.0
    lo, hi = 0, len(sorted_vals)
    while lo < hi:
        mid = (lo + hi) // 2
        if sorted_vals[mid] <= v:
            lo = mid + 1
        else:
            hi = mid
    return lo / len(sorted_vals)


def effective_price(lad: list[tuple[float, float]], dist: dict[str, Any]) -> float | None:
    """Supply-weighted price of one ladder (weightPriceLadder in src/supply.mjs)."""
    if not lad:
        return None
    s = SUPPLY
    med_log = max(dist["median_log"], 1e-9)
    anchor = max(dist["derivative_p90"], 1e-9)
    lad_med = max(_quantile([p for p, _ in lad], 0.5) or lad[0][0], 1e-12)
    ders = _derivs(lad)
    num = den = 0.0
    for (price, n), d in zip(lad, ders, strict=True):
        lg = math.log1p(n)
        pct = _pct_rank(dist["sorted_logs"], lg)
        level = max(math.exp(lg - med_log), 1e-9) ** max(1e-6, s["availability_exponent"])
        dmul = max(0.05, 1 + s["distribution_weight"] * (pct * 2 - 1))
        der = 1 + s["derivative_weight"] * min(s["derivative_cap"], d / anchor)
        low = max(lad_med / price, 1e-9) ** s["lower_price_preference_weight"]
        w = level * dmul * der * low
        num += price * w
        den += w
    return num / den if den > 0 else None


def blended_cost(pin: float | None, pout: float | None) -> float | None:
    """Weighted geometric mean of input/output effective prices (0.4 / 0.6)."""
    if pin is None or pout is None:
        return None
    wi, wo = SUPPLY["input_weight"], SUPPLY["output_weight"]
    return math.exp((wi * math.log(max(pin, 1e-12)) + wo * math.log(max(pout, 1e-12))) / (wi + wo))


# ---------------------------------------------------------------- scoring helpers
def catalog_availability(providers: int) -> float:
    if providers <= 0:
        return 0.0
    return min(100.0, 100.0 * (math.log1p(providers) / math.log(7)) ** 0.8)


def price_utility(cost: float | None) -> tuple[float, str]:
    cap = METHOD["near_free_cost_cap_usd_per_mtok"]
    if cost is None or not cost > 0:
        return 0.0, "unpriced"
    if cost <= cap:
        return 100.0, "near_free"
    return 100.0 * cap / cost, "priced"


def recency_score(days: int | None) -> float:
    return 0.0 if days is None else 100.0 * math.exp(-max(0, days) / METHOD["recency_tau_days"])


def shortlist_score(c: dict[str, float]) -> float:
    w = METHOD["shortlist_weights"]
    return (
        w["price_utility"] * c["price_utility"]
        + w["reliability"] * c["reliability"]
        + w["catalog_availability"] * c["catalog_availability"]
        + w["price_supply_availability"] * c["price_supply_availability"]
        + w["capability"] * c["capability"]
        + w["recency"] * c["recency"]
        + w["boost_points_per_unit"] * c["boost"]
    )


def load_prior(path: str = PRIOR_FILE) -> dict[str, Any]:
    with open(path, "rb") as fh:
        raw = fh.read()
    prior = json.loads(raw)
    prior["_sha256"] = hashlib.sha256(raw).hexdigest()
    return prior


def _family_index(prior: dict[str, Any]) -> tuple[dict[str, str], dict[str, str]]:
    by_route: dict[str, str] = {}
    by_seg: dict[str, str] = {}
    for f in prior["families"]:
        for rid in f["route_ids"]:
            by_route[rid] = f["model_family"]
        for s in f["segments"]:
            by_seg.setdefault(s, f["model_family"])
    return by_route, by_seg


# ---------------------------------------------------------------- assembly
def collect_routes(
    catalog: list[dict[str, Any]], status: dict[str, Any], prior: dict[str, Any]
) -> list[dict[str, Any]]:
    """Every live text route with ladders, health and its family (prior or unscored)."""
    rails, aliases, _ = F._status_maps(status)
    by_route, by_seg = _family_index(prior)
    out = []
    for rail in catalog:
        pre = rail["prefix"]
        rail_ok = bool(rail.get("enabled")) and not rail.get("upstreamDisabled")
        rst = rails.get(pre) or {}
        for m in rail.get("models") or []:
            if (m.get("outputModality") or "text") != "text":
                continue
            mid = m["upstreamModelId"]
            rid = f"{pre}/{mid}"
            seg = mid.split("/")[-1].lower()
            fam = by_route.get(rid) or by_seg.get(seg)
            pin, pout = _ladder(m.get("pricePointsIn")), _ladder(m.get("pricePointsOut"))
            listings = int(sum(n for _, n in pin))
            enabled = rail_ok and bool(m.get("enabled")) and not m.get("modelDisabled")
            alias = F._alias_for(mid, aliases)
            al = aliases.get(alias) or {}
            h = F.route_health(
                enabled=enabled,
                listings=listings,
                rail_state=rst.get("state") or rail.get("status"),
                alias_state=al.get("state"),
                alias_avail_24h=F._num(al.get("availability24hPct")),
            )
            cav = F.route_caveats(rid, pre, rail.get("systemPromptNote"), None)
            out.append(
                {
                    "route": rid,
                    "rail": pre,
                    "segment": seg,
                    "label": m.get("label") or mid,
                    "model_family": fam,
                    "in_prior": fam is not None,
                    "ladder_in": pin,
                    "ladder_out": pout,
                    "listings_in": listings,
                    "min_ask_in": min((p for p, n in pin if n > 0), default=None),
                    "min_ask_out": min((p for p, n in pout if n > 0), default=None),
                    "official_in": F._num(m.get("officialIn")),
                    "official_out": F._num(m.get("officialOut")),
                    "health": h,
                    "rail_availability_7d_pct": F._num(rst.get("availability7dPct")),
                    "system_prompt_handling": cav["system_prompt_handling"],
                    "preferred_endpoint": cav["preferred_endpoint"],
                    "caveats": cav["caveats"],
                }
            )
    return out


def _vendor_guess(rs: list[dict[str, Any]], fprior: dict[str, Any]) -> str:
    """Vendor for a family outside the prior, from the frontier line patterns (else blank)."""
    for r in rs:
        cls = F.classify_model(r["route"].split("/")[-1], r["label"], fprior)
        if cls:
            return str(cls["vendor"])
    return ""


def _route_key(r: dict[str, Any]) -> tuple[Any, ...]:
    c = r.get("effective_cost")
    return (F.HEALTH_ORDER[r["health"]["status"]], c if c is not None else math.inf, r["route"])


def build(
    catalog: list[dict[str, Any]],
    status: dict[str, Any],
    market: dict[str, Any],
    prior: dict[str, Any],
    meta: dict[str, Any],
) -> dict[str, Any]:
    """Assemble the Top 20 document (pure). ``market`` is accepted for parity with frontier."""
    del market
    as_of = dt.date.fromisoformat(meta["generated_at"][:10])
    routes = collect_routes(catalog, status, prior)
    live = [r for r in routes if r["health"]["status"] != "unavailable" and r["ladder_in"]]
    dist_in = distribution([r["ladder_in"] for r in live])
    dist_out = distribution([r["ladder_out"] for r in live])
    for r in routes:
        r["effective_cost"] = blended_cost(
            effective_price(r["ladder_in"], dist_in), effective_price(r["ladder_out"], dist_out)
        )
    pf = {f["model_family"]: f for f in prior["families"]}
    fprior = F.load_prior()
    fams: dict[str, list[dict[str, Any]]] = {}
    for r in routes:
        name = r["model_family"] or r["label"]
        fams.setdefault(name, []).append(r)
    rows = []
    for name, rs in fams.items():
        rs.sort(key=_route_key)
        p = pf.get(name)
        vendor = p["vendor"] if p else _vendor_guess(rs, fprior)
        lv = [r for r in rs if r in live and r["effective_cost"] is not None and r["ladder_out"]]
        providers = len({r["rail"] for r in lv})
        costs = sorted((r["effective_cost"], max(r["listings_in"], 1)) for r in lv)
        cost = None
        if costs:
            half, acc = sum(w for _, w in costs) / 2.0, 0.0
            for c, w in costs:
                acc += w
                if acc >= half:
                    cost = c
                    break
        util, regime = price_utility(cost)
        best = rs[0]
        avail7 = best["rail_availability_7d_pct"] if best in lv else None
        rel = avail7 if avail7 is not None else 0.0
        cap = float(p["capability_score_100"]) if p else 0.0
        release = p.get("release_date") if p else None
        days = (as_of - dt.date.fromisoformat(release)).days if release else None
        boost = 0.15 if regime == "near_free" else 0.0
        rows.append(
            {
                "model_family": name,
                "vendor": vendor,
                "tier": p["tier"] if p else "unscored",
                "capability_score_100": cap,
                "capability_cell": p["capability_score_100"] if p else "0",
                "release_date": release,
                "recency_days": days,
                "priced_provider_count": providers,
                "catalog_availability_score_100": catalog_availability(providers),
                "listings_total": sum(r["listings_in"] for r in lv),
                "public_availability_7d_pct": avail7,
                "supply_weighted_median_cost_usdc_per_1m": cost,
                "price_utility_score_100": util,
                "price_regime": regime,
                "reliability_weight_boost": boost,
                "reliability_score_100": rel,
                "performance_evidence_status": "provisional_public",
                "routes": rs,
                "live_routes": lv,
                "in_prior": p is not None,
            }
        )
    priced = sorted(math.log1p(r["listings_total"]) for r in rows if r["listings_total"] > 0)
    for row in rows:
        lt = row["listings_total"]
        row["price_supply_availability_score_100"] = (
            100.0 * _pct_rank(priced, math.log1p(lt)) if lt > 0 else 0.0
        )
        row["shortlist_score_100"] = shortlist_score(
            {
                "price_utility": row["price_utility_score_100"],
                "reliability": row["reliability_score_100"],
                "catalog_availability": row["catalog_availability_score_100"],
                "price_supply_availability": row["price_supply_availability_score_100"],
                "capability": row["capability_score_100"],
                "recency": recency_score(row["recency_days"]),
                "boost": row["reliability_weight_boost"],
            }
        )
        gates = []
        if row["priced_provider_count"] < MIN_PROVIDERS:
            gates.append("insufficient_provider_breadth")
        if row["catalog_availability_score_100"] < MIN_CATALOG_AVAILABILITY:
            gates.append("catalog_availability_below_minimum")
        if row["tier"] == "unscored":
            gates.append("tier_below_minimum")
        if row["capability_score_100"] < MIN_CAPABILITY:
            gates.append("capability_below_minimum")
        if row["release_date"] is None:
            gates.append("release_date_unknown")
        if row["price_regime"] == "unpriced":
            gates.append("missing_price")
        if not any(r["health"]["status"] == "healthy" for r in row["live_routes"]):
            gates.append("not_routing_eligible")
        row["gate_reasons"] = gates
        row["recommendation_eligible"] = not gates

    def key(r: dict[str, Any]) -> tuple[Any, ...]:
        c = r["supply_weighted_median_cost_usdc_per_1m"]
        return (-round(r["shortlist_score_100"], 4), c if c is not None else math.inf,
                r["model_family"])  # fmt: skip

    rows.sort(key=key)
    top = rows[:TOP_N]
    conf_rank = {"low": 0, "medium": 1, "high": 2}
    entries = []
    for i, r in enumerate(top, 1):
        r["recommendation_rank"] = i
        b = r["routes"][0]
        cap_conf = prior.get("confidence", "low") if r["in_prior"] else "none"
        hconf = b["health"]["confidence"]
        conf = "low" if cap_conf == "none" else min((cap_conf, hconf), key=lambda c: conf_rank[c])
        entries.append(
            {
                "recommendation_rank": i,
                "model_family": r["model_family"],
                "vendor": r["vendor"] or None,
                "recommendation_eligible": r["recommendation_eligible"],
                "recommendation_status": "recommended" if r["recommendation_eligible"] else "gated",
                "gate_reasons": r["gate_reasons"],
                "tier": r["tier"],
                "capability_score_100": r["capability_score_100"],
                "capability_basis": "carried_over_prior_2026-09-22"
                if r["in_prior"]
                else "unscored_not_in_prior",
                "capability_confidence": cap_conf,
                "health_confidence": hconf,
                "confidence": conf,
                "release_date": r["release_date"],
                "recency_days": r["recency_days"],
                "priced_provider_count": r["priced_provider_count"],
                "shortlist_score_100": round(r["shortlist_score_100"], 4),
                "price_regime": r["price_regime"],
                "supply_weighted_median_cost_usd_per_mtok": F._r(
                    r["supply_weighted_median_cost_usdc_per_1m"]
                ),
                "public_availability_7d_pct": r["public_availability_7d_pct"],
                "best_route": b["route"],
                "best_route_health": b["health"]["status"],
                "best_route_health_reasons": b["health"]["reasons"],
                "best_route_min_ask_in": F._r(b["min_ask_in"]),
                "best_route_min_ask_out": F._r(b["min_ask_out"]),
                "best_route_official_in": b["official_in"],
                "best_route_official_out": b["official_out"],
                "best_route_system_prompt_handling": b["system_prompt_handling"],
                "best_route_preferred_endpoint": b["preferred_endpoint"],
                "best_route_caveats": b["caveats"],
                "routes": [x["route"] for x in r["routes"]],
            }
        )
    return {
        "schema": SCHEMA_ID,
        "hypothesis_notice": (
            "Each row is a hypothesis, not a fact. Capability, tier and release date are carried "
            "over from the 2026-09-22 list (benchmark inputs lost; low confidence). Prices, "
            "provider breadth, supply depth and 7-day availability are live InferHub data at "
            "generated_at; listed asks are USD per 1M tokens and the served price can be higher."
        ),
        "generated_at": meta["generated_at"],
        "code_commit": meta.get("code_commit"),
        "provenance": {
            "snapshot_sha256": meta.get("snapshot_sha256"),
            "sources": meta.get("sources", []),
            "capability_prior": {
                "file": "operational/telemetry/gravebuster/pipeline/ihub/top20_prior.v1.json",
                "sha256": prior.get("_sha256"),
                "status": prior.get("status"),
                "confidence": prior.get("confidence"),
                "as_of": prior.get("as_of"),
            },
            "generator": GENERATOR,
        },
        "method": METHOD,
        "counts": {
            "families_considered": len(rows),
            "listed": len(top),
            "recommended": sum(1 for e in entries if e["recommendation_eligible"]),
            "gated": sum(1 for e in entries if not e["recommendation_eligible"]),
            "unscored_listed": sum(1 for e in entries if e["capability_confidence"] == "none"),
        },
        "entries": entries,
        "_rows": top,
    }


def to_csv(doc: dict[str, Any]) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(CSV_COLUMNS)
    for r in doc["_rows"]:
        w.writerow(
            [
                r["recommendation_rank"],
                r["model_family"],
                r["vendor"],
                fmt(r["recommendation_eligible"]),
                "; ".join(r["gate_reasons"]),
                r["tier"],
                r["capability_cell"],
                r["release_date"] or "",
                fmt(r["recency_days"]),
                r["priced_provider_count"],
                fmt(r["catalog_availability_score_100"], 2),
                fmt(r["price_supply_availability_score_100"], 3),
                fmt(r["public_availability_7d_pct"], 3),
                fmt(r["supply_weighted_median_cost_usdc_per_1m"], 6),
                fmt(r["price_utility_score_100"], 3),
                r["price_regime"],
                fmt(r["reliability_weight_boost"]),
                fmt(r["reliability_score_100"], 3),
                r["performance_evidence_status"],
                fmt(r["shortlist_score_100"], 4),
                "; ".join(x["route"] for x in r["routes"]),
            ]
        )
    return buf.getvalue().encode("utf-8")


def to_json(doc: dict[str, Any]) -> bytes:
    pub = {k: v for k, v in doc.items() if not k.startswith("_")}
    return (json.dumps(pub, indent=1, ensure_ascii=False) + "\n").encode("utf-8")


def update_manifest(lists_dir: str, written: dict[str, bytes], doc: dict[str, Any]) -> None:
    """Point manifest.lists[top20] at the regenerated file (other entries untouched)."""
    path = os.path.join(lists_dir, "manifest.json")
    with open(path, encoding="utf-8") as fh:
        man = json.load(fh)
    for spec in man["lists"]:
        if spec["list"] != "top20":
            continue
        spec["sha256"] = hashlib.sha256(written[OUT_CSV]).hexdigest()
        spec["source_path"] = GENERATOR
        spec["generated_at"] = doc["generated_at"]
        spec["code_commit"] = doc["code_commit"]
        spec["snapshot_sha256"] = doc["provenance"]["snapshot_sha256"]
        spec["sidecar_json"] = OUT_JSON
        spec["sidecar_json_sha256"] = hashlib.sha256(written[OUT_JSON]).hexdigest()
        spec["schema"] = SCHEMA_ID
        spec["issue"] = "https://github.com/Pukujan/inference-recommendation-engine/issues/76"
        spec["meaning"] = (
            "Top 20 recommendation view by empirical score, regenerated in this repo from live "
            "InferHub data (top20.py); gated rows stay visible, recommendation_eligible marks rows "
            "allowed into the operational shortlist. Capability is a carried-over low-confidence "
            "prior (see the sidecar JSON)."
        )
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(man, indent=1, ensure_ascii=False) + "\n")


def generate(raw_dir: str, out_dir: str, code_commit: str | None) -> dict[str, Any]:
    parsed, meta = F.load_raw(raw_dir)
    meta["generated_at"] = F._iso_now()
    meta["code_commit"] = code_commit
    doc = build(parsed["catalog"], parsed["status"], parsed["market"], load_prior(), meta)
    written = {OUT_CSV: to_csv(doc), OUT_JSON: to_json(doc)}
    os.makedirs(out_dir, exist_ok=True)
    for name, data in written.items():
        tmp = os.path.join(out_dir, name + ".tmp")
        with open(tmp, "wb") as fh:
            fh.write(data)
        os.replace(tmp, os.path.join(out_dir, name))
    if os.path.abspath(out_dir) == os.path.abspath(LISTS_DIR):
        update_manifest(out_dir, written, doc)
    return doc


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="top20", description=(__doc__ or "").splitlines()[0])
    ap.add_argument("--raw-dir", required=True, help="catalog/status/market JSON bodies")
    ap.add_argument("--fetch", action="store_true", help="GET live bodies into --raw-dir first")
    ap.add_argument("--env-file", default=os.environ.get("IHUB_ENV_FILE"))
    ap.add_argument("--out-dir", default=LISTS_DIR)
    ap.add_argument("--code-commit", default=None)
    a = ap.parse_args(argv)
    try:
        if a.fetch:
            F.fetch_live(a.raw_dir, a.env_file)
        doc = generate(a.raw_dir, a.out_dir, a.code_commit or F._git_head())
    except Exception as e:  # never echo a key
        print(F.redact(f"{type(e).__name__}: {e}"), file=sys.stderr)
        return 1
    print(json.dumps({"out_dir": a.out_dir, "counts": doc["counts"],
                      "snapshot_sha256": doc["provenance"]["snapshot_sha256"]}))  # fmt: skip
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
