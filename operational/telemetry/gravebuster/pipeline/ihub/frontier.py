"""Frontier / expensive-tier recommendation list (IRE #67, part of #65 workstream 2).

Ranks the strongest models InferHub serves right now, capability first, and attaches the live
price of every route. It sits next to the cheap Top 20 in ``lists/`` and never reads, rewrites or
re-ranks the Top 20 files.

Inputs (all GET; nothing here can POST):
* ``https://inferhub.dev/api/catalog`` (key): live order book per route: official price,
  ``pricePointsIn``/``pricePointsOut`` = ``[[ask_per_1M, sellers], ...]``, enabled flags, rail
  ``activeProviders`` and the rail ``systemPromptNote``.
* ``https://inferhub.dev/api/status`` (public): rail state, per-model alias state, recent
  per-route traffic series.
* ``https://inferhub.dev/api/market`` (public): last trade rate per route.
* ``frontier_prior.v1.json``: curated capability prior per model line (a low-confidence
  hypothesis until benchmark ingestion lands).

Every model row is a hypothesis with status, confidence, evidence and provenance (sha256 of each
raw body, code commit, endpoints). Prices are listed asks in USD per 1M tokens at fetch time.

``build`` and the CSV writers are standard library only so CI can test them offline; ``main``
does the I/O. The API key is read from an env file or the environment, sent only as a header,
and never printed (errors pass through ``redact``).
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
import re
import subprocess
import sys
import urllib.error
import urllib.request
from typing import Any

SCHEMA_ID = "ihub-frontier-recommendations/v1"
SCHEMA_FILE = "schemas/frontier-recommendations.v1.schema.json"
HERE = os.path.dirname(os.path.abspath(__file__))
LISTS_DIR = os.path.join(HERE, "lists")
PRIOR_FILE = os.path.join(HERE, "frontier_prior.v1.json")
OUT_MODELS_CSV = "research_model_frontier_recommendations.csv"
OUT_ROUTES_CSV = "research_model_frontier_routes.csv"
OUT_JSON = "research_model_frontier_recommendations.json"
POLICY_PER_MTOK = float(os.environ.get("IHUB_POLICY_PER_MTOK", "0.10"))
BLEND_INPUT_WEIGHT = 3  # blended ask = (3 * in + 1 * out) / 4, used only to compare routes
# A route counts as price-tied with the cheapest ask at its own health tier when its blended ask
# is within this fraction of it; ties are broken by supply depth at that ask (see route_order).
ROUTE_PRICE_TIE_TOL = 0.05

CATALOG_URL = "https://inferhub.dev/api/catalog"
STATUS_URL = "https://inferhub.dev/api/status"
MARKET_URL = "https://inferhub.dev/api/market"
USER_AGENT = "ire-frontier-list/1 (GET-only; IRE #67)"

HEALTH_ORDER = {"healthy": 0, "insufficient_data": 1, "degraded": 2, "failing": 3, "unavailable": 4}
BAD_STATES = {"major_outage", "partial_outage", "down", "outage"}
WARN_STATES = {"degraded", "degraded_performance"}
LOW_TRAFFIC_HEALTHY_MIN_24H = 95.0
ASTRA_OWNER_ROUTE = "cb/gpt-6-astra"

# InferHub's own note for the OpenAI Codex rail (cx/), kept verbatim in the caveat.
CX_CAVEAT = (
    "System prompt is sent as a developer message; the upstream required instructions field is "
    "set to 'You are a helpful assistant.'; native /v1/responses keeps instructions as sent."
)
CX_REQUIRED_INSTRUCTIONS = "You are a helpful assistant."

_KEY_RE = re.compile(r"sk-[A-Za-z0-9]+-[A-Za-z0-9_\-]+")


def redact(s: str) -> str:
    return _KEY_RE.sub("sk-<redacted>", s or "")


# ---------------------------------------------------------------- small helpers
def _num(v: Any) -> float | None:
    if v is None or v == "":
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _r(v: float | None, nd: int = 6) -> float | None:
    return None if v is None else round(v, nd)


def norm_version(v: str) -> str:
    """'4-6' -> '4.6' (Claude Code ids use dashes)."""
    return v.replace("-", ".")


def version_key(v: str) -> tuple[int, ...]:
    return tuple(int(x) for x in v.split("."))


def major(v: str) -> int:
    return version_key(v)[0]


def weighted_median(points: list[list[float]]) -> float | None:
    """Capacity-weighted median ask from [[price, sellers], ...]."""
    pts = sorted((float(p), int(n)) for p, n in points if n and n > 0)
    total = sum(n for _, n in pts)
    if not total:
        return None
    half, acc = total / 2.0, 0
    for p, n in pts:
        acc += n
        if acc >= half:
            return p
    return pts[-1][0]


def blended(i: float | None, o: float | None) -> float | None:
    if i is None or o is None:
        return None
    return (BLEND_INPUT_WEIGHT * i + o) / (BLEND_INPUT_WEIGHT + 1)


def alias_key(model_id: str) -> str:
    """Route model id -> InferHub alias spelling ('claude-opus-4-6-thinking' -> 'claude-opus-4.6-thinking')."""
    seg = model_id.split("/")[-1].lower()
    return re.sub(r"(?<![\d.])(\d+)-(\d+)(?![\d.a-z])", r"\1.\2", seg)


# ---------------------------------------------------------------- prior + classification
def load_prior(path: str = PRIOR_FILE) -> dict[str, Any]:
    with open(path, "rb") as fh:
        raw = fh.read()
    prior = json.loads(raw)
    prior["_sha256"] = hashlib.sha256(raw).hexdigest()
    return prior


def classify_model(
    model_id: str, label: str | None, prior: dict[str, Any]
) -> dict[str, Any] | None:
    """Match a route's model id to a prior line; None when no line matches."""
    seg = model_id.split("/")[-1].lower()
    for line in prior["lines"]:
        m = re.match(line["id_pattern"], seg)
        if not m:
            continue
        v = m.group("v")
        if not v and line.get("label_pattern") and label:
            lm = re.match(line["label_pattern"], label)
            v = lm.group("v") if lm else None
        if not v:
            continue
        v = norm_version(v)
        return {
            "line": line["line"],
            "vendor": line["vendor"],
            "line_class": line["line_class"],
            "capability_prior_100": float(line["capability_prior_100"]),
            "older_gen_exception": line.get("older_gen_exception"),
            "version": v,
            "variant": (m.groupdict().get("variant") or "").lstrip("-") or None,
            "model_family": line["family_template"].format(v=v),
        }
    return None


def tier_for(score: float) -> str:
    if score >= 85:
        return "frontier"
    if score >= 70:
        return "top"
    if score >= 55:
        return "upper_mid"
    return "mid"


# ---------------------------------------------------------------- route health (hypothesis)
def route_health(
    *,
    enabled: bool,
    listings: int,
    rail_state: str | None,
    alias_state: str | None,
    alias_avail_24h: float | None,
) -> dict[str, Any]:
    """Health hypothesis for one route from InferHub's public status + the live order book.

    Rail state is per route family (prefix); alias state is per model across all rails, so a
    model-wide outage marks every route of that model degraded (it cannot be pinned to one rail).
    This is platform evidence, not this account's own request logs, so confidence is at most
    medium.
    """
    reasons: list[str] = []
    if not enabled:
        return {"status": "unavailable", "confidence": "high", "reasons": ["disabled in catalog"]}
    if listings <= 0:
        return {"status": "unavailable", "confidence": "high", "reasons": ["no seller listings"]}
    rs, a = (rail_state or "").lower(), (alias_state or "").lower()
    if rs in BAD_STATES:
        return {"status": "failing", "confidence": "medium", "reasons": [f"rail_state={rs}"]}
    status, conf = "healthy", "medium"
    if rs in WARN_STATES:
        status = "degraded"
        reasons.append(f"rail_state={rs}")
    elif rs and rs not in ("operational", "available"):
        reasons.append(f"rail_state={rs} (unrecognised, ignored)")
    if a in BAD_STATES or a in WARN_STATES:
        status = "degraded"
        reasons.append(f"model alias_state={a} (24h {alias_avail_24h}%), model-wide signal")
    elif a == "low_traffic":
        conf = "low"
        if alias_avail_24h is None:
            if status == "healthy":
                status = "insufficient_data"
            reasons.append("model alias low_traffic with no 24h availability")
        elif alias_avail_24h < LOW_TRAFFIC_HEALTHY_MIN_24H:
            status = "degraded"
            reasons.append(
                f"model alias low_traffic, 24h availability {alias_avail_24h}% "
                f"< {LOW_TRAFFIC_HEALTHY_MIN_24H}"
            )
        else:
            reasons.append(f"model alias low_traffic, 24h availability {alias_avail_24h}%")
    elif a == "operational":
        reasons.append(f"model alias operational (24h {alias_avail_24h}%)")
    elif not a:
        conf = "low"
        reasons.append("no model alias status published")
    if status == "healthy" and rs in ("operational", "available"):
        reasons.append(f"rail_state={rs}")
    return {"status": status, "confidence": conf, "reasons": reasons}


def route_caveats(
    route: str, rail: str, rail_note: str | None, variant: str | None
) -> dict[str, Any]:
    """System-prompt handling, preferred endpoint and caveats for one route."""
    caveats: list[str] = []
    if rail == "cx":
        caveats.append(CX_CAVEAT)
        out: dict[str, Any] = {
            "system_prompt_handling": "developer_message",
            "preferred_endpoint": "/v1/responses",
            "required_instructions_value": CX_REQUIRED_INSTRUCTIONS,
        }
    else:
        out = {
            "system_prompt_handling": "upstream_note" if rail_note else "unspecified",
            "preferred_endpoint": None,
            "required_instructions_value": None,
        }
        if rail_note:
            caveats.append(f"InferHub rail note: {rail_note.strip()}")
    if route == ASTRA_OWNER_ROUTE:
        caveats.append(
            "Fixed Astra owner route (CKFF failover hop 3, docs/CKFF-PROVIDER.md); ranked here "
            "only, owner setup unchanged."
        )
    if variant:
        caveats.append(f"route variant '{variant}' (same model family, different upstream id)")
    out["caveats"] = caveats
    return out


# ---------------------------------------------------------------- assembly
def _status_maps(status: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], set[str]]:
    rails = {f.get("prefix"): f for f in status.get("families") or [] if f.get("prefix")}
    aliases = {a.get("alias"): a for a in status.get("autoRouted") or [] if a.get("alias")}
    seen: set[str] = set()
    perf = status.get("performance") or {}
    slug_to_prefix = {f.get("slug"): f.get("prefix") for f in status.get("families") or []}
    for fam in perf.get("families") or []:
        pre = slug_to_prefix.get(fam.get("slug"))
        for s in fam.get("series") or []:
            vals = [x for x in (s.get("ttftMs") or [])[-6:] if x is not None]
            if pre and vals:
                seen.add(f"{pre}/{s.get('label')}")
    return rails, aliases, seen


def _alias_for(model_id: str, aliases: dict[str, Any]) -> str | None:
    key = alias_key(model_id)
    if key in aliases:
        return key
    best = None
    for a in aliases:
        if key.startswith(a + "-") and (best is None or len(a) > len(best)):
            best = a
    return best


def build_routes(
    catalog: list[dict[str, Any]],
    status: dict[str, Any],
    market: dict[str, Any],
    prior: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[str]]:
    rails, aliases, seen = _status_maps(status)
    last_rate = {m.get("slug"): _num(m.get("lastRate")) for m in market.get("models") or []}
    routes: list[dict[str, Any]] = []
    unclassified: list[str] = []
    for rail in catalog:
        pre = rail["prefix"]
        rail_ok = bool(rail.get("enabled")) and not rail.get("upstreamDisabled")
        rst = rails.get(pre) or {}
        for m in rail.get("models") or []:
            rid = f"{pre}/{m['upstreamModelId']}"
            if (m.get("outputModality") or "text") != "text":
                continue
            cls = classify_model(m["upstreamModelId"], m.get("label"), prior)
            if cls is None:
                unclassified.append(rid)
                continue
            pin = [[float(p), int(n)] for p, n in (m.get("pricePointsIn") or [])]
            pout = [[float(p), int(n)] for p, n in (m.get("pricePointsOut") or [])]
            listings = sum(n for _, n in pin)
            min_in = min((p for p, n in pin if n > 0), default=None)
            min_out = min((p for p, n in pout if n > 0), default=None)
            sellers_min = sum(n for p, n in pin if p == min_in) if min_in is not None else 0
            off_in, off_out = _num(m.get("officialIn")), _num(m.get("officialOut"))
            enabled = rail_ok and bool(m.get("enabled")) and not m.get("modelDisabled")
            alias = _alias_for(m["upstreamModelId"], aliases)
            al = aliases.get(alias) or {}
            h = route_health(
                enabled=enabled,
                listings=listings,
                rail_state=rst.get("state") or rail.get("status"),
                alias_state=al.get("state"),
                alias_avail_24h=_num(al.get("availability24hPct")),
            )
            cav = route_caveats(rid, pre, rail.get("systemPromptNote"), cls["variant"])
            if sellers_min == 1:
                cav["caveats"].append(
                    "only 1 seller at the cheapest ask; served price may be higher"
                )

            def disc(a: float | None, o: float | None) -> float | None:
                return None if a is None or not o else round(100.0 * (1 - a / o), 3)

            routes.append(
                {
                    "route": rid,
                    "rail": pre,
                    "rail_label": rail.get("label"),
                    "model_id": m["upstreamModelId"],
                    "model_label": m.get("label"),
                    "model_family": cls["model_family"],
                    "vendor": cls["vendor"],
                    "line": cls["line"],
                    "line_class": cls["line_class"],
                    "version": cls["version"],
                    "variant": cls["variant"],
                    "enabled": enabled,
                    "price": {
                        "min_ask_in": _r(min_in),
                        "min_ask_out": _r(min_out),
                        "blended_min_ask_3to1": _r(blended(min_in, min_out)),
                        "cw_median_ask_in": _r(weighted_median(pin)),
                        "cw_median_ask_out": _r(weighted_median(pout)),
                        "official_in": off_in,
                        "official_out": off_out,
                        "discount_vs_official_in_pct": disc(min_in, off_in),
                        "discount_vs_official_out_pct": disc(min_out, off_out),
                        "last_trade_rate": _r(last_rate.get(rid)),
                        "min_ask_in_under_policy": None
                        if min_in is None
                        else min_in < POLICY_PER_MTOK,
                        "min_ask_out_under_policy": None
                        if min_out is None
                        else min_out < POLICY_PER_MTOK,
                    },
                    "sellers": {
                        "sellers_at_min_ask_in": sellers_min,
                        "listings_in": listings,
                        "price_tiers_in": sum(1 for _, n in pin if n > 0),
                        "rail_active_providers": rail.get("activeProviders"),
                    },
                    "health": {
                        "status": h["status"],
                        "confidence": h["confidence"],
                        "reasons": h["reasons"],
                        "rail_state": rst.get("state"),
                        "rail_availability_24h_pct": _num(rst.get("availability24hPct")),
                        "alias": alias,
                        "alias_state": al.get("state"),
                        "alias_availability_24h_pct": _num(al.get("availability24hPct")),
                        "recent_traffic_observed": rid in seen,
                    },
                    "context_window": m.get("contextWindow"),
                    "max_output_tokens": m.get("maxOutputTokens"),
                    "supports_cache": m.get("supportsCache"),
                    "cache_hit_rate_24h": _r(_num(m.get("cacheHitRate")), 4),
                    "system_prompt_handling": cav["system_prompt_handling"],
                    "preferred_endpoint": cav["preferred_endpoint"],
                    "required_instructions_value": cav["required_instructions_value"],
                    "caveats": cav["caveats"],
                    "is_astra_owner_route": rid == ASTRA_OWNER_ROUTE,
                }
            )
    routes.sort(key=lambda r: r["route"])
    return routes, sorted(unclassified)


def _route_ask(r: dict[str, Any]) -> float | None:
    return r["price"]["blended_min_ask_3to1"]


def _route_depth(r: dict[str, Any]) -> int:
    return int(r["sellers"]["sellers_at_min_ask_in"] or 0)


def route_order(routes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Order one family's routes: health status, then price band, then supply depth, ask, route.

    The band is what keeps the named route honest. Two rails can quote the same min ask while
    one backs it with thousands of sellers and the other with a handful; the deep rail is the
    route a caller should be sent to. So a route within ROUTE_PRICE_TIE_TOL of the cheapest ask
    at its own health tier counts as price-tied, and inside that band more sellers at the ask
    wins. Outside the band the cheaper ask still wins, so a genuinely cheaper route is never
    passed over for a deeper but dearer one.
    """
    if not routes:
        return []
    leader_by_status: dict[str, float] = {}
    for r in routes:
        a = _route_ask(r)
        if a is None:
            continue
        s = r["health"]["status"]
        if s not in leader_by_status or a < leader_by_status[s]:
            leader_by_status[s] = a

    def key(r: dict[str, Any]) -> tuple[Any, ...]:
        a = _route_ask(r)
        s = r["health"]["status"]
        ref = leader_by_status.get(s)
        tied = ref is not None and a is not None and a <= ref * (1 + ROUTE_PRICE_TIE_TOL)
        return (
            HEALTH_ORDER[s],
            0 if tied else 1,
            -_route_depth(r),
            a if a is not None else math.inf,
            r["route"],
        )

    return sorted(routes, key=key)


def best_route(routes: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Best route for one family: best health, then the cheapest ask with a supply tie-break."""
    return route_order(routes)[0] if routes else None


def build_models(routes: list[dict[str, Any]], prior: dict[str, Any]) -> list[dict[str, Any]]:
    fams: dict[str, list[dict[str, Any]]] = {}
    for r in routes:
        fams.setdefault(r["model_family"], []).append(r)
    live = [r for r in routes if r["health"]["status"] != "unavailable"]
    vendor_major: dict[str, int] = {}
    line_versions: dict[str, set[str]] = {}
    for r in live:
        vendor_major[r["vendor"]] = max(vendor_major.get(r["vendor"], 0), major(r["version"]))
        line_versions.setdefault(r["line"], set()).add(r["version"])
    penalty = float(prior.get("version_lag_penalty", 3))
    min_cap = float(prior.get("min_frontier_capability_100", 60))
    models: list[dict[str, Any]] = []
    for fam, rs in fams.items():
        r0 = rs[0]
        best = best_route(rs)
        assert best is not None
        newer = sum(
            1
            for v in line_versions.get(r0["line"], ())
            if version_key(v) > version_key(r0["version"])
        )
        base = next(x["capability_prior_100"] for x in prior["lines"] if x["line"] == r0["line"])
        score = round(float(base) - penalty * newer, 3)
        cur = vendor_major.get(r0["vendor"], major(r0["version"]))
        models.append(
            {
                "model_family": fam,
                "vendor": r0["vendor"],
                "line": r0["line"],
                "line_class": r0["line_class"],
                "version": r0["version"],
                "major_version": major(r0["version"]),
                "vendor_current_major": cur,
                "newer_versions_in_line": newer,
                "capability_score_100": score,
                "tier": tier_for(score),
                "routes": route_order(rs),
                "best": best,
            }
        )
    # current-generation gate, then the Claude older-generation price exception
    by_line_current: dict[str, list[float]] = {}
    for m in models:
        if m["major_version"] >= m["vendor_current_major"]:
            b = m["best"]["price"]["blended_min_ask_3to1"]
            if b is not None and m["best"]["health"]["status"] == "healthy":
                by_line_current.setdefault(m["line"], []).append(b)
    by_line_current_any: dict[str, list[float]] = {}
    for m in models:
        if m["major_version"] >= m["vendor_current_major"]:
            b = m["best"]["price"]["blended_min_ask_3to1"]
            if b is not None and m["best"]["health"]["status"] != "unavailable":
                by_line_current_any.setdefault(m["line"], []).append(b)
    exc_lines = {x["line"]: x.get("older_gen_exception") for x in prior["lines"]}
    out = []
    for m in models:
        gates: list[str] = []
        exceptions: list[str] = []
        evidence: list[str] = []
        if m["major_version"] < m["vendor_current_major"]:
            ref = by_line_current.get(m["line"]) or by_line_current_any.get(m["line"]) or []
            b = m["best"]["price"]["blended_min_ask_3to1"]
            if (
                exc_lines.get(m["line"]) == "allowed_if_cheaper_than_current_line"
                and ref
                and b is not None
                and b < min(ref)
            ):
                m["generation_status"] = "older_allowed_cheaper"
                exceptions.append("older_gen_allowed_cheaper")
                evidence.append(
                    f"older generation {m['major_version']} < current {m['vendor_current_major']}"
                    f"; blended ask {b} < cheapest current {m['line']} {round(min(ref), 6)}"
                )
            else:
                m["generation_status"] = "older_gated"
                gates.append("older_generation")
                evidence.append(
                    f"major version {m['major_version']} < {m['vendor']} current major "
                    f"{m['vendor_current_major']}"
                )
        else:
            m["generation_status"] = "current"
        if m["line_class"] == "small" or m["capability_score_100"] < min_cap:
            gates.append("below_frontier_capability")
        bs = m["best"]["health"]["status"]
        if bs == "unavailable":
            gates.append("no_live_route")
        elif bs != "healthy":
            gates.append("no_healthy_route")
        m["gate_reasons"] = gates
        m["gate_exceptions"] = exceptions
        m["generation_evidence"] = evidence
        m["recommendation_eligible"] = not gates
        out.append(m)

    def mkey(m: dict[str, Any]) -> tuple[Any, ...]:
        b = m["best"]["price"]["blended_min_ask_3to1"]
        return (
            0 if m["recommendation_eligible"] else 1,
            -m["capability_score_100"],
            b if b is not None else math.inf,
            m["model_family"],
        )

    out.sort(key=mkey)
    for i, m in enumerate(out, 1):
        m["frontier_rank"] = i
    return out


def _model_doc(m: dict[str, Any], prior: dict[str, Any]) -> dict[str, Any]:
    b = m["best"]
    p = b["price"]
    hconf = b["health"]["confidence"]
    conf_rank = {"low": 0, "medium": 1, "high": 2}
    cap_conf = prior.get("confidence", "low")
    confidence = min((cap_conf, hconf, "high"), key=lambda c: conf_rank[c])
    under = p["min_ask_in_under_policy"]
    return {
        "frontier_rank": m["frontier_rank"],
        "model_family": m["model_family"],
        "vendor": m["vendor"],
        "line": m["line"],
        "line_class": m["line_class"],
        "version": m["version"],
        "tier": m["tier"],
        "capability_score_100": m["capability_score_100"],
        "capability_basis": "curated_line_prior_minus_version_lag",
        "recommendation_status": "recommended" if m["recommendation_eligible"] else "gated",
        "recommendation_eligible": m["recommendation_eligible"],
        "gate_reasons": m["gate_reasons"],
        "gate_exceptions": m["gate_exceptions"],
        "generation_status": m["generation_status"],
        "major_version": m["major_version"],
        "vendor_current_major": m["vendor_current_major"],
        "newer_versions_in_line": m["newer_versions_in_line"],
        "confidence": confidence,
        "capability_confidence": cap_conf,
        "health_confidence": hconf,
        "best_route": b["route"],
        "best_route_health": b["health"]["status"],
        "best_route_min_ask_in": p["min_ask_in"],
        "best_route_min_ask_out": p["min_ask_out"],
        "best_route_blended_min_ask_3to1": p["blended_min_ask_3to1"],
        "best_route_official_in": p["official_in"],
        "best_route_official_out": p["official_out"],
        "best_route_discount_vs_official_in_pct": p["discount_vs_official_in_pct"],
        "best_route_sellers_at_min_ask_in": b["sellers"]["sellers_at_min_ask_in"],
        "best_route_listings_in": b["sellers"]["listings_in"],
        "price_policy_threshold_usd_per_mtok": POLICY_PER_MTOK,
        "price_policy_basis": "best_route_min_ask_in",
        "price_policy_status": None
        if under is None
        else ("under_policy" if under else "over_policy"),
        "best_route_system_prompt_handling": b["system_prompt_handling"],
        "best_route_preferred_endpoint": b["preferred_endpoint"],
        "best_route_caveats": b["caveats"],
        "route_count": len(m["routes"]),
        "healthy_route_count": sum(1 for r in m["routes"] if r["health"]["status"] == "healthy"),
        "routes": [r["route"] for r in m["routes"]],
        "evidence": {
            "generation": m["generation_evidence"],
            "best_route_health_reasons": b["health"]["reasons"],
            "best_route_selection": "lowest health status, then the cheapest blended (3:1) live "
            f"min ask with a supply-depth tie-break inside a {ROUTE_PRICE_TIE_TOL:.0%} price "
            "band, then route id",
        },
    }


def build(
    catalog: list[dict[str, Any]],
    status: dict[str, Any],
    market: dict[str, Any],
    prior: dict[str, Any],
    meta: dict[str, Any],
) -> dict[str, Any]:
    """Assemble the frontier document (pure)."""
    routes, unclassified = build_routes(catalog, status, market, prior)
    models = build_models(routes, prior)
    rank = {m["model_family"]: m["frontier_rank"] for m in models}
    best = {m["best"]["route"] for m in models}
    for r in routes:
        r["frontier_rank"] = rank[r["model_family"]]
        r["is_best_route"] = r["route"] in best
    by_family: dict[str, list[dict[str, Any]]] = {}
    for r in routes:
        by_family.setdefault(r["model_family"], []).append(r)
    ordered: list[dict[str, Any]] = []
    for m in models:
        ordered.extend(route_order(by_family.get(m["model_family"], [])))
    routes[:] = ordered
    return {
        "schema": SCHEMA_ID,
        "schema_file": "operational/telemetry/gravebuster/pipeline/ihub/" + SCHEMA_FILE,
        "hypothesis_notice": (
            "Each row is a hypothesis, not a fact. Capability comes from a curated line prior "
            "(low confidence, no benchmarks). Route health comes from InferHub's public status "
            "(platform-wide, not this account's own requests). Prices are live listed asks in USD "
            "per 1M tokens at fetched_at; the served price can be higher. The cheap Top 20 is a "
            "separate list and is not changed by this one."
        ),
        "generated_at": meta["generated_at"],
        "code_commit": meta.get("code_commit"),
        "provenance": {
            "snapshot_sha256": meta.get("snapshot_sha256"),
            "sources": meta.get("sources", []),
            "capability_prior": {
                "file": "operational/telemetry/gravebuster/pipeline/ihub/frontier_prior.v1.json",
                "sha256": prior.get("_sha256"),
                "status": prior.get("status"),
                "confidence": prior.get("confidence"),
            },
        },
        "method": {
            "ranking": "recommendation_eligible first, then capability_score_100 desc, then best "
            "route blended (3:1 input:output) live min ask asc, then model_family",
            "capability": "line capability_prior_100 minus version_lag_penalty per newer live "
            "version in the same line (frontier_prior.v1.json)",
            "generation_gate": "a model whose major version is below its vendor's newest live "
            "major version gets gate_reason older_generation (e.g. GPT 5.x while GPT 6.x is live)",
            "claude_exception": "lines with older_gen_exception=allowed_if_cheaper_than_current_line "
            "(Claude Opus, Claude Sonnet) stay eligible when the older model's best-route blended "
            "ask is below the cheapest current-generation model of the same line (gate_exceptions "
            "older_gen_allowed_cheaper); compared against healthy current routes, else any live one",
            "other_gates": {
                "below_frontier_capability": "line_class small or capability_score_100 < "
                f"{prior.get('min_frontier_capability_100')}",
                "no_healthy_route": "best route health is not healthy",
                "no_live_route": "every route disabled or without sellers",
            },
            "route_health": "unavailable (disabled / no sellers), failing (rail outage), degraded "
            "(rail degraded, model alias degraded/outage, or low_traffic with 24h availability "
            f"< {LOW_TRAFFIC_HEALTHY_MIN_24H}%), insufficient_data (low_traffic, no 24h figure), "
            "else healthy",
            "health_values": list(HEALTH_ORDER),
            "price_policy": f"under_policy when the best route's live min input ask < "
            f"${POLICY_PER_MTOK} per 1M tokens (docs/INFERHUB-API-SETUP.md); a field, never a "
            "filter",
            "price_source": "live /api/catalog order book only; no snapshot CSVs, no estimates",
            "non_text_routes": "excluded (image/video output)",
        },
        "counts": {
            "models": len(models),
            "recommended": sum(1 for m in models if m["recommendation_eligible"]),
            "gated": sum(1 for m in models if not m["recommendation_eligible"]),
            "routes": len(routes),
            "unclassified_text_routes": len(unclassified),
        },
        "models": [_model_doc(m, prior) for m in models],
        "routes": routes,
        "unclassified_text_routes": unclassified,
    }


# ---------------------------------------------------------------- CSV
MODEL_CSV_COLUMNS = [
    "frontier_rank", "model_family", "vendor", "line", "version", "tier", "capability_score_100",
    "capability_basis", "recommendation_status", "recommendation_eligible", "gate_reasons",
    "gate_exceptions", "generation_status", "major_version", "vendor_current_major", "confidence",
    "capability_confidence", "health_confidence", "best_route", "best_route_health",
    "best_route_min_ask_in", "best_route_min_ask_out", "best_route_blended_min_ask_3to1",
    "best_route_official_in", "best_route_official_out", "best_route_discount_vs_official_in_pct",
    "best_route_sellers_at_min_ask_in", "best_route_listings_in",
    "price_policy_threshold_usd_per_mtok", "price_policy_status",
    "best_route_system_prompt_handling", "best_route_preferred_endpoint", "best_route_caveats",
    "route_count", "healthy_route_count", "model_ids", "route_prices", "generated_at",
    "code_commit", "snapshot_sha256",
]  # fmt: skip

ROUTE_CSV_COLUMNS = [
    "frontier_rank", "model_family", "route", "is_best_route", "rail", "rail_label", "model_id",
    "model_label", "variant", "enabled", "health_status", "health_confidence", "health_reasons",
    "rail_state", "alias", "alias_state", "alias_availability_24h_pct", "recent_traffic_observed",
    "min_ask_in", "min_ask_out", "blended_min_ask_3to1", "cw_median_ask_in", "cw_median_ask_out",
    "official_in", "official_out", "discount_vs_official_in_pct", "discount_vs_official_out_pct",
    "last_trade_rate", "min_ask_in_under_policy", "sellers_at_min_ask_in", "listings_in",
    "price_tiers_in", "rail_active_providers", "system_prompt_handling", "preferred_endpoint",
    "required_instructions_value", "caveats", "is_astra_owner_route", "context_window",
    "generated_at", "code_commit", "snapshot_sha256",
]  # fmt: skip


def _cell(v: Any) -> Any:
    if v is None:
        return ""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, list):
        return "; ".join(str(x) for x in v)
    return v


def _write_csv(columns: list[str], rows: list[dict[str, Any]]) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(columns)
    for r in rows:
        w.writerow([_cell(r.get(c)) for c in columns])
    return buf.getvalue().encode("utf-8")


def models_csv(doc: dict[str, Any]) -> bytes:
    routes = {r["route"]: r for r in doc["routes"]}
    rows = []
    for m in doc["models"]:
        flat = dict(m)
        flat["model_ids"] = m["routes"]
        flat["route_prices"] = [
            f"{rid}={routes[rid]['price']['min_ask_in']}/{routes[rid]['price']['min_ask_out']}"
            f"@{routes[rid]['health']['status']}"
            for rid in m["routes"]
        ]
        flat["best_route_caveats"] = " | ".join(m["best_route_caveats"])
        flat["generated_at"] = doc["generated_at"]
        flat["code_commit"] = doc["code_commit"]
        flat["snapshot_sha256"] = doc["provenance"]["snapshot_sha256"]
        rows.append(flat)
    return _write_csv(MODEL_CSV_COLUMNS, rows)


def routes_csv(doc: dict[str, Any]) -> bytes:
    rows = []
    for r in doc["routes"]:
        flat = {k: v for k, v in r.items() if not isinstance(v, dict)}
        flat.update(r["price"])
        flat.update(r["sellers"])
        h = r["health"]
        flat.update(
            {
                "health_status": h["status"],
                "health_confidence": h["confidence"],
                "health_reasons": " | ".join(h["reasons"]),
                "rail_state": h["rail_state"],
                "alias": h["alias"],
                "alias_state": h["alias_state"],
                "alias_availability_24h_pct": h["alias_availability_24h_pct"],
                "recent_traffic_observed": h["recent_traffic_observed"],
            }
        )
        flat["caveats"] = " | ".join(r["caveats"])
        flat["generated_at"] = doc["generated_at"]
        flat["code_commit"] = doc["code_commit"]
        flat["snapshot_sha256"] = doc["provenance"]["snapshot_sha256"]
        rows.append(flat)
    return _write_csv(ROUTE_CSV_COLUMNS, rows)


# ---------------------------------------------------------------- I/O
def _iso_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _read_key(env_file: str | None) -> str | None:
    if env_file and os.path.exists(env_file):
        with open(env_file, encoding="utf-8") as fh:
            for line in fh:
                m = re.match(r"^\s*(?:export\s+)?INFERHUB_API_KEY\s*=\s*(.*)$", line)
                if m:
                    return m.group(1).strip().strip('"').strip("'") or None
    return os.environ.get("INFERHUB_API_KEY") or None


def _get(url: str, key: str | None) -> bytes:
    req = urllib.request.Request(url, method="GET")
    req.add_header("Accept", "application/json")
    req.add_header("User-Agent", USER_AGENT)
    if key:
        req.add_header("Authorization", "Bearer " + key)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:  # noqa: S310 (fixed https URLs)
            return bytes(resp.read())
    except urllib.error.HTTPError as e:
        raise RuntimeError(redact(f"GET {url} -> HTTP {e.code}")) from None
    except urllib.error.URLError as e:
        raise RuntimeError(redact(f"GET {url} -> {e.reason}")) from None


def fetch_live(raw_dir: str, env_file: str | None) -> None:
    """GET catalog (key), status and market (public) into raw_dir with a _fetch.json sidecar."""
    key = _read_key(env_file)
    if not key:
        raise RuntimeError("no INFERHUB_API_KEY (env file or environment); /api/catalog needs it")
    os.makedirs(raw_dir, exist_ok=True)
    side: dict[str, Any] = {}
    for name, url, auth in (
        ("catalog", CATALOG_URL, True),
        ("status", STATUS_URL, False),
        ("market", MARKET_URL, False),
    ):
        body = _get(url, key if auth else None)
        with open(os.path.join(raw_dir, name + ".json"), "wb") as fh:
            fh.write(body)
        side[name] = {"url": url, "auth": "key" if auth else "public", "fetched_at": _iso_now()}
    with open(os.path.join(raw_dir, "_fetch.json"), "w", encoding="utf-8") as fh:
        json.dump(side, fh, indent=1)


def load_raw(raw_dir: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """Read catalog/status/market bodies; returns (parsed, meta with sha256 provenance)."""
    side: dict[str, Any] = {}
    sp = os.path.join(raw_dir, "_fetch.json")
    if os.path.exists(sp):
        with open(sp, encoding="utf-8") as fh:
            side = json.load(fh)
    parsed: dict[str, Any] = {}
    sources = []
    h = hashlib.sha256()
    defaults = {"catalog": (CATALOG_URL, "key"), "status": (STATUS_URL, "public"),
                "market": (MARKET_URL, "public")}  # fmt: skip
    for name in ("catalog", "status", "market"):
        with open(os.path.join(raw_dir, name + ".json"), "rb") as fh:
            body = fh.read()
        parsed[name] = json.loads(body)
        sha = hashlib.sha256(body).hexdigest()
        h.update(f"{name}:{sha}\n".encode())
        s = side.get(name) or {}
        sources.append(
            {
                "endpoint": name,
                "url": s.get("url", defaults[name][0]),
                "auth": s.get("auth", defaults[name][1]),
                "method": "GET",
                "fetched_at": s.get("fetched_at"),
                "fetched_from": s.get("fetched_from"),
                "sha256": sha,
                "bytes": len(body),
            }
        )
    return parsed, {"sources": sources, "snapshot_sha256": h.hexdigest()}


def _git_head() -> str | None:
    try:
        return (
            subprocess.check_output(["git", "-C", HERE, "rev-parse", "HEAD"], text=True).strip()
            or None
        )
    except (OSError, subprocess.CalledProcessError):
        return None


def update_manifest(lists_dir: str, written: dict[str, bytes], doc: dict[str, Any]) -> None:
    """Record the generated frontier files under manifest.generated_lists (lists[] untouched).

    Replaces only the ``frontier`` entry and keeps every other generated list, so the daily
    frontier run does not wipe the utility list's manifest record."""
    path = os.path.join(lists_dir, "manifest.json")
    with open(path, encoding="utf-8") as fh:
        man = json.load(fh)
    entry = {
        "list": "frontier",
        "files": {
            "models_csv": OUT_MODELS_CSV,
            "routes_csv": OUT_ROUTES_CSV,
            "json": OUT_JSON,
        },
        "sha256": {name: hashlib.sha256(body).hexdigest() for name, body in written.items()},
        "schema": doc["schema"],
        "schema_file": doc["schema_file"],
        "generator": "operational/telemetry/gravebuster/pipeline/ihub/frontier.py",
        "generated_at": doc["generated_at"],
        "code_commit": doc["code_commit"],
        "snapshot_sha256": doc["provenance"]["snapshot_sha256"],
        "rank_column": "frontier_rank",
        "eligible_column": "recommendation_eligible",
        "meaning": "Frontier / expensive tier, ranked capability first with live InferHub "
        "route prices. Separate from the cheap Top 20, which it never changes. Generated in "
        "this repo (not a verbatim copy).",
        "issue": "https://github.com/Pukujan/inference-recommendation-engine/issues/67",
    }
    others = [g for g in man.get("generated_lists", []) if g.get("list") != "frontier"]
    man["generated_lists"] = others + [entry]
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(man, indent=1, ensure_ascii=False) + "\n")


def generate(raw_dir: str, out_dir: str, code_commit: str | None) -> dict[str, Any]:
    parsed, meta = load_raw(raw_dir)
    meta["generated_at"] = _iso_now()
    meta["code_commit"] = code_commit
    doc = build(parsed["catalog"], parsed["status"], parsed["market"], load_prior(), meta)
    body = (json.dumps(doc, indent=1, ensure_ascii=False) + "\n").encode("utf-8")
    written = {OUT_MODELS_CSV: models_csv(doc), OUT_ROUTES_CSV: routes_csv(doc), OUT_JSON: body}
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
    ap = argparse.ArgumentParser(prog="frontier", description=__doc__.splitlines()[0])
    ap.add_argument("--raw-dir", required=True, help="catalog/status/market JSON bodies")
    ap.add_argument("--fetch", action="store_true", help="GET live bodies into --raw-dir first")
    ap.add_argument("--env-file", default=os.environ.get("IHUB_ENV_FILE"))
    ap.add_argument("--out-dir", default=LISTS_DIR)
    ap.add_argument("--code-commit", default=None)
    a = ap.parse_args(argv)
    try:
        if a.fetch:
            fetch_live(a.raw_dir, a.env_file)
        doc = generate(a.raw_dir, a.out_dir, a.code_commit or _git_head())
    except Exception as e:  # never echo a key
        print(redact(f"{type(e).__name__}: {e}"), file=sys.stderr)
        return 1
    print(json.dumps({"out_dir": a.out_dir, "counts": doc["counts"],
                      "snapshot_sha256": doc["provenance"]["snapshot_sha256"]}))  # fmt: skip
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
