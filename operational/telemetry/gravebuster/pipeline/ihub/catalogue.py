"""Reliability-adjusted route catalogue for agents (IRE #46 M4).

Joins IRE's static recommendation lists (``lists/``: Top 20 view + daily shortlist, verbatim input
copies, never edited here) with the live order book (``fact_route_price``) and rolling request-log
reliability (``fact_route_reliability``) into ``data/inferhub/catalogue/route-catalogue.json`` and
``route-catalogue.csv``. Schema: ``schemas/route-catalogue.v1.schema.json``.

Every status is an evidence-backed hypothesis computed from this account's own InferHub request
logs, not a fact about the route: it carries its window, counts, Wilson interval, the order-book
hash and the code commit so an agent can cite or re-check it.

The decision logic (``classify``/``build``/``csv_rows``) is standard library only so CI can test
it without DuckDB; ``run`` does the I/O on the telemetry host.
"""

from __future__ import annotations

import csv
import datetime as dt
import hashlib
import io
import json
import math
import os
from typing import Any

try:  # package import on the host; file import (tests) falls back to a sibling load
    from . import errclass
except ImportError:  # pragma: no cover
    import importlib.util as _ilu

    _spec = _ilu.spec_from_file_location(
        "ihub_errclass", os.path.join(os.path.dirname(os.path.abspath(__file__)), "errclass.py")
    )
    assert _spec and _spec.loader
    errclass = _ilu.module_from_spec(_spec)
    _spec.loader.exec_module(errclass)

SCHEMA_ID = "ihub-route-catalogue/v1.1"  # v1.1: error classes (additive to v1)
SCHEMA_FILE = "schemas/route-catalogue.v1.schema.json"
HERE = os.path.dirname(os.path.abspath(__file__))
LISTS_DIR = os.path.join(HERE, "lists")
WINDOWS = ("1h", "24h", "7d")
STATUS_ORDER = {"healthy": 0, "insufficient_data": 1, "degraded": 2, "failing": 3}
PLATFORM_BAD = {"major_outage", "partial_outage", "degraded", "degraded_performance", "down"}

# Thresholds (recorded in every artifact under method.thresholds).
METHOD: dict[str, Any] = {
    "min_service_attempts_1h": 5,
    "min_service_attempts_24h": 10,
    "failing_below_success_rate": 0.5,
    "degraded_below_success_rate_1h": 0.9,
    "healthy_min_success_rate_24h": 0.95,
    "healthy_min_wilson_lower_24h": 0.9,
    "wilson_z": 1.645,
    "confidence_high": {"min_attempts": 100, "max_interval_width": 0.1},
    "confidence_medium": {"min_attempts": 30, "max_interval_width": 0.25},
}
POLICY_PER_MTOK = float(os.environ.get("IHUB_POLICY_PER_MTOK", "0.10"))


def wilson(k: int, n: int, z: float = METHOD["wilson_z"]) -> tuple[float | None, float | None]:
    """Wilson score interval for k successes in n trials (None when n == 0)."""
    if n <= 0:
        return None, None
    p = k / n
    den = 1 + z * z / n
    mid = (p + z * z / (2 * n)) / den
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return round(max(0.0, mid - half), 6), round(min(1.0, mid + half), 6)


def _confidence(n: int, lo: float | None, hi: float | None) -> str:
    if lo is None or hi is None:
        return "low"
    width = hi - lo
    for level in ("high", "medium"):
        c = METHOD[f"confidence_{level}"]
        if n >= c["min_attempts"] and width <= c["max_interval_width"]:
            return level
    return "low"


def classify(
    windows: dict[str, dict[str, Any] | None], platform_state: str | None = None
) -> dict[str, Any]:
    """Status hypothesis for one route from its 1h/24h/7d rollups.

    Uses service attempts (requests minus client/account-attributable errors) and ok counts.
    Order: insufficient_data -> failing -> degraded -> healthy. A 24h failure whose last hour
    recovered (>= degraded threshold with enough attempts) is reported as degraded, not failing.
    The platform rail state only degrades a route that has no usable last-hour evidence of its own.
    """
    m = METHOD

    def nk(w: str) -> tuple[int, int]:
        r = windows.get(w) or {}
        return int(r.get("service_attempts") or 0), int(r.get("ok") or 0)

    n1, k1 = nk("1h")
    n24, k24 = nk("24h")
    n7, k7 = nk("7d")
    reasons: list[str] = []
    sr1 = k1 / n1 if n1 >= m["min_service_attempts_1h"] else None
    sr24 = k24 / n24 if n24 >= m["min_service_attempts_24h"] else None
    bad_platform = (platform_state or "") in PLATFORM_BAD
    if sr1 is None and sr24 is None:
        reasons.append(
            f"service_attempts_24h={n24} < {m['min_service_attempts_24h']} and "
            f"service_attempts_1h={n1} < {m['min_service_attempts_1h']}"
        )
        if n7:
            reasons.append(f"7d: {k7}/{n7} ok (context only)")
        else:
            reasons.append("no requests from this account on this route in 7d")
        if bad_platform:
            reasons.append(f"platform_rail_state={platform_state}")
        lo, hi = wilson(k7, n7)
        return {
            "status": "insufficient_data",
            "confidence": "low",
            "decided_window": "7d" if n7 else None,
            "decided_service_attempts": n7,
            "decided_ok": k7,
            "wilson_lower": lo,
            "wilson_upper": hi,
            "reasons": reasons,
        }
    lo24, hi24 = wilson(k24, n24)
    recovered = sr1 is not None and sr1 >= m["degraded_below_success_rate_1h"]
    if sr1 is not None and sr1 < m["failing_below_success_rate"]:
        status, win = "failing", "1h"
        reasons.append(f"1h success {k1}/{n1} < {m['failing_below_success_rate']}")
    elif sr24 is not None and sr24 < m["failing_below_success_rate"] and not recovered:
        status, win = "failing", "24h"
        reasons.append(f"24h success {k24}/{n24} < {m['failing_below_success_rate']}")
        if sr1 is None:
            reasons.append(f"1h has {n1} service attempts (not enough to show recovery)")
    elif sr1 is not None and sr1 < m["degraded_below_success_rate_1h"]:
        status, win = "degraded", "1h"
        reasons.append(f"1h success {k1}/{n1} < {m['degraded_below_success_rate_1h']}")
    elif sr24 is not None and (
        sr24 < m["healthy_min_success_rate_24h"]
        or (lo24 is not None and lo24 < m["healthy_min_wilson_lower_24h"])
    ):
        status, win = "degraded", "24h"
        reasons.append(
            f"24h success {k24}/{n24} (wilson lower {lo24}) below healthy thresholds "
            f"{m['healthy_min_success_rate_24h']}/{m['healthy_min_wilson_lower_24h']}"
        )
        if recovered:
            reasons.append(f"last hour recovered: {k1}/{n1} ok")
    elif bad_platform and sr1 is None:
        status, win = "degraded", "24h" if sr24 is not None else "1h"
        reasons.append(f"platform_rail_state={platform_state} and no usable 1h evidence")
    else:
        status = "healthy"
        win = "24h" if sr24 is not None else "1h"
        k, n = (k24, n24) if win == "24h" else (k1, n1)
        reasons.append(f"{win} success {k}/{n} service attempts")
        if bad_platform:
            reasons.append(f"platform_rail_state={platform_state} (own requests succeed)")
    k, n = {"1h": (k1, n1), "24h": (k24, n24)}[win]
    wd = windows.get(win) or {}
    rej = int(wd.get("err_upstream_reject") or 0)
    if rej:
        pres = int(wd.get("err_upstream_reject_presumed") or 0)
        reasons.append(
            f"{win}: {rej} upstream 400 rejects counted as failures (11133, #40 M0.6; "
            f"{rej - pres} with a telemetry error code, {pres} presumed by the cb/cbcn 400 rule)"
        )
    cl = int(wd.get("client_errors") or 0)
    if cl:
        reasons.append(f"{win}: {cl} client/account-attributable errors excluded")
    lo, hi = wilson(k, n)
    return {
        "status": status,
        "confidence": _confidence(n, lo, hi),
        "decided_window": win,
        "decided_service_attempts": n,
        "decided_ok": k,
        "wilson_lower": lo,
        "wilson_upper": hi,
        "reasons": reasons,
    }


# ---------------------------------------------------------------- static lists (read-only input)
def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        h.update(fh.read())
    return h.hexdigest()


def load_lists(lists_dir: str = LISTS_DIR) -> dict[str, Any]:
    """Parse lists/manifest.json and its CSVs; verify each file's sha256 against the manifest."""
    with open(os.path.join(lists_dir, "manifest.json"), encoding="utf-8") as fh:
        man = json.load(fh)
    out: dict[str, Any] = {
        "profile": man.get("profile"),
        "generated_utc": man.get("generated_utc"),
        "lists": [],
    }
    for spec in man["lists"]:
        path = os.path.join(lists_dir, spec["file"])
        sha = _sha256_file(path)
        with open(path, encoding="utf-8", newline="") as fh:
            rows = list(csv.DictReader(fh))
        entries = []
        for r in rows:
            elig_col = spec.get("eligible_column")
            eligible = (r.get(elig_col, "").strip().lower() == "true") if elig_col else True
            entries.append(
                {
                    "rank": int(r[spec["rank_column"]]),
                    "model_family": r["model_family"],
                    "vendor": r["vendor"],
                    "recommendation_eligible": eligible,
                    "gate_reasons": [
                        g.strip() for g in (r.get("gate_reasons") or "").split(";") if g.strip()
                    ],
                    "tier": r.get("tier") or None,
                    "static_cost_usd_per_mtok": _num(
                        r.get("supply_weighted_median_cost_usdc_per_1m")
                    ),
                    "price_regime": r.get("price_regime") or None,
                    "performance_evidence_status": r.get("performance_evidence_status") or None,
                    "shortlist_score_100": _num(r.get("shortlist_score_100")),
                    "routes": [x.strip() for x in r["model_ids"].split(";") if x.strip()],
                }
            )
        out["lists"].append(
            {
                "list": spec["list"],
                "file": spec["file"],
                "sha256": sha,
                "sha256_expected": spec["sha256"],
                "sha256_verified": sha == spec["sha256"],
                "meaning": spec.get("meaning"),
                "entries": entries,
            }
        )
    return out


def _num(v: Any) -> float | None:
    if v is None or v == "":
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _int(v: Any) -> int | None:
    return None if v is None else int(v)


# ---------------------------------------------------------------- assembly
_WIN_FIELDS_INT = (
    "requests", "ok", "errors", "client_errors", "service_attempts",
    "err_upstream_unavailable", "err_timeout", "err_rate_limited", "err_server_error",
    "err_client_request_error", "err_client_cancelled", "err_auth", "err_payment_required",
    "err_other", "upstream_errors", "account_errors", "unknown_errors", "err_upstream_reject",
    "err_upstream_reject_presumed", "served_tier_known", "served_at_min_tier", "tokens_in", "tokens_out",
    "tokens_cached",
)  # fmt: skip
_WIN_FIELDS_NUM = (
    "success_rate", "service_success_rate", "ttft_ms_p50", "ttft_ms_p95", "duration_ms_p50",
    "duration_ms_p95", "served_ask_in_median", "served_ask_out_median", "served_ask_in_min",
    "served_ask_in_max", "served_tier_rank_in_median", "share_served_at_min_tier",
    "share_served_under_policy", "billed_cost_usdc",
)  # fmt: skip


def _iso(v: Any) -> str | None:
    if v is None:
        return None
    if isinstance(v, dt.datetime):
        if v.tzinfo is None:
            v = v.replace(tzinfo=dt.timezone.utc)
        return v.astimezone(dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    return str(v)


def _window(r: dict[str, Any]) -> dict[str, Any]:
    w: dict[str, Any] = {"window_start": _iso(r.get("window_start")),
                         "window_end": _iso(r.get("window_end"))}  # fmt: skip
    for f in _WIN_FIELDS_INT:
        w[f] = _int(r.get(f))
    for f in _WIN_FIELDS_NUM:
        w[f] = _num(r.get(f))
    w["last_request_at"] = _iso(r.get("last_request_at"))
    w["last_ok_at"] = _iso(r.get("last_ok_at"))
    w["last_service_error_at"] = _iso(r.get("last_service_error_at"))
    return w


def _under(v: float | None) -> bool | None:
    return None if v is None else v < POLICY_PER_MTOK


def build(
    reliability: list[dict[str, Any]],
    prices: list[dict[str, Any]],
    dim_routes: list[dict[str, Any]],
    status_by_rail: dict[str, dict[str, Any]],
    lists: dict[str, Any],
    meta: dict[str, Any],
    breakdown: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Assemble the catalogue document (pure; inputs are plain dict rows).

    reliability: fact_route_reliability rows; prices: latest fact_route_price row per route;
    dim_routes: inferhub_dim_route rows; status_by_rail: latest fact_route_status per rail;
    meta: generated_at, code_commit, modeled snapshot, catalog fetch evidence, inputs_sha256."""
    rel: dict[str, dict[str, dict[str, Any]]] = {}
    for r in reliability:
        rel.setdefault(r["route"], {})[r["window"]] = r
    price = {p["route"]: p for p in prices}
    errs: dict[str, list[dict[str, Any]]] = {}
    for e in breakdown or []:
        if e.get("window") == "24h" and e.get("error_type") != "ok":
            errs.setdefault(e["route"], []).append(
                {
                    "http_status": _int(e.get("http_status")),
                    "status": e.get("status"),
                    "error_code": None if e.get("error_code") is None else str(e["error_code"]),
                    "error_type": e.get("error_type"),
                    "error_class": e.get("error_class"),
                    "class_basis": e.get("class_basis"),
                    "requests": int(e.get("requests") or 0),
                    "last_at": _iso(e.get("last_at")),
                }
            )
    dim = {d["route"]: d for d in dim_routes}
    membership: dict[str, list[dict[str, Any]]] = {}
    for lst in lists["lists"]:
        for e in lst["entries"]:
            for pos, route in enumerate(e["routes"], 1):
                membership.setdefault(route, []).append(
                    {
                        "list": lst["list"],
                        "rank": e["rank"],
                        "model_family": e["model_family"],
                        "recommendation_eligible": e["recommendation_eligible"],
                        "gate_reasons": e["gate_reasons"],
                        "route_position": pos,
                    }
                )
    route_ids = sorted(set(membership) | set(rel))
    routes: list[dict[str, Any]] = []
    for rid in route_ids:
        windows = rel.get(rid, {})
        d, p = dim.get(rid), price.get(rid)
        rail = (d or p or {}).get("rail") or rid.split("/", 1)[0]
        any_w = next(iter(windows.values()), None)
        st = status_by_rail.get(rail) or {}
        plat_state = st.get("state") or (any_w or {}).get("platform_rail_state")
        c = classify(windows, plat_state)
        mem = membership.get(rid, [])
        in_catalog = p is not None
        if not in_catalog:
            c["reasons"].append("route not in the live catalog (no order book)")
        elif p.get("enabled") is False:
            c["reasons"].append("route listed but disabled in the live catalog")
        live_in, live_out = _num((p or {}).get("min_ask_in")), _num((p or {}).get("min_ask_out"))
        cw_in, cw_out = (
            _num((p or {}).get("cw_median_ask_in")),
            _num((p or {}).get("cw_median_ask_out")),
        )
        w24 = windows.get("24h") or {}
        routes.append(
            {
                "route": rid,
                "rail": rail,
                "model_id": rid[len(rail) + 1 :] if rid.startswith(rail + "/") else rid,
                "model_label": (d or {}).get("model_label"),
                "lists": mem,
                "recommendation_eligible_static": (
                    any(m["recommendation_eligible"] for m in mem) if mem else None
                ),
                "in_live_catalog": in_catalog,
                "enabled": None if p is None else p.get("enabled"),
                "status": c["status"],
                "confidence": c["confidence"],
                "status_reasons": c["reasons"],
                "price": {
                    "live_price_ts": _iso((p or {}).get("ts")),
                    "live_min_ask_in": live_in,
                    "live_min_ask_out": live_out,
                    "live_min_tier_avail_in": _int((p or {}).get("min_tier_avail_in")),
                    "live_cw_median_ask_in": cw_in,
                    "live_cw_median_ask_out": cw_out,
                    "official_in": _num((p or {}).get("official_in")),
                    "official_out": _num((p or {}).get("official_out")),
                    "live_listings_in": _int((p or {}).get("avail_total_in")),
                    "policy_threshold_usd_per_mtok": POLICY_PER_MTOK,
                    "live_min_ask_in_under_policy": _under(live_in),
                    "live_min_ask_out_under_policy": _under(live_out),
                    "live_cw_median_ask_in_under_policy": _under(cw_in),
                    "served_ask_in_median_24h": _num(w24.get("served_ask_in_median")),
                    "served_ask_out_median_24h": _num(w24.get("served_ask_out_median")),
                    "served_tier_rank_in_median_24h": _num(w24.get("served_tier_rank_in_median")),
                    "share_served_at_min_tier_24h": _num(w24.get("share_served_at_min_tier")),
                    "share_served_under_policy_24h": _num(w24.get("share_served_under_policy")),
                },
                "reliability": {
                    w: (_window(windows[w]) if w in windows else None) for w in WINDOWS
                },
                "error_breakdown_24h": sorted(errs.get(rid, []), key=lambda e: -e["requests"]),
                "evidence": {
                    "decided_window": c["decided_window"],
                    "decided_service_attempts": c["decided_service_attempts"],
                    "decided_ok": c["decided_ok"],
                    "wilson_lower": c["wilson_lower"],
                    "wilson_upper": c["wilson_upper"],
                    "request_log_coverage_end": meta.get("request_log_coverage_end"),
                    "live_book_hash": (p or {}).get("book_hash"),
                    "platform_rail_state": plat_state,
                    "platform_availability_24h_pct": _num(
                        st.get("availability_24h_pct")
                        if st
                        else (any_w or {}).get("platform_availability_24h_pct")
                    ),
                    "source": "InferHub /api/usage/logs of this account (GET), /api/catalog, "
                    "/api/status",
                },
            }
        )
    by_route = {r["route"]: r for r in routes}
    models = _models(lists, by_route)
    counts: dict[str, int] = {s: 0 for s in STATUS_ORDER}
    for r in routes:
        counts[r["status"]] += 1
    return {
        "schema": SCHEMA_ID,
        "schema_file": "operational/telemetry/gravebuster/pipeline/ihub/" + SCHEMA_FILE,
        "hypothesis_notice": (
            "Statuses, confidence and reliability_adjusted_rank are evidence-backed hypotheses "
            "from this account's own request logs and the live order book at generated_at, not "
            "facts or guarantees. Static list fields are copied from the 2026-09-22 lists "
            "unchanged. Prices are listed asks (USD per 1M tokens); served prices can be higher."
        ),
        "generated_at": meta["generated_at"],
        "code_commit": meta.get("code_commit"),
        "evidence": {
            "request_log_coverage_end": meta.get("request_log_coverage_end"),
            "modeled_snapshot": meta.get("modeled_snapshot"),
            "latest_catalog_fetch": meta.get("latest_catalog_fetch"),
            "inputs_sha256": meta.get("inputs_sha256"),
            "static_lists": [
                {k: lst[k] for k in ("list", "file", "sha256", "sha256_verified", "meaning")}
                | {"profile": lists.get("profile"), "generated_utc": lists.get("generated_utc")}
                for lst in lists["lists"]
            ],
        },
        "method": {
            "windows": list(WINDOWS),
            "window_end": "request-log coverage end (last log fetch), not generated_at",
            "status_values": list(STATUS_ORDER),
            "service_attempts": "requests minus errors of error_class client or account",
            "error_classification": {
                "rules": "ihub/errclass.py (rendered into sql/fact_request_outcome.sql)",
                "error_classes": ["upstream", "client", "account", "unknown"],
                "excluded_from_service_attempts": ["client", "account"],
                "upstream_reject_codes": list(errclass.UPSTREAM_REJECT_CODES),
                "reject_400_rails": list(errclass.REJECT_400_RAILS),
                "note": "InferHub request logs carry no error code. A telemetry error code "
                "(request match, or an incident on the same route within 60 s) wins; otherwise an "
                "HTTP 400 on a cb/cbcn rail is presumed 11133 (intermittent upstream/seller reject, "
                "#40 M0.6) and counts as a failure; other 4xx stay client errors.",
            },
            "thresholds": METHOD,
            "platform_bad_states": sorted(PLATFORM_BAD),
            "best_route_order": "status (healthy, insufficient_data, degraded, failing), then "
            "in live catalog + enabled, then live capacity-weighted median input ask, then list "
            "position",
            "reliability_adjusted_rank_order": "best route status, then static rank (top20 "
            "before daily_shortlist-only models)",
        },
        "status_counts": counts,
        "models": models,
        "routes": routes,
    }


def _models(lists: dict[str, Any], by_route: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    fam: dict[str, dict[str, Any]] = {}
    for lst in lists["lists"]:
        for e in lst["entries"]:
            m = fam.setdefault(
                e["model_family"],
                {
                    "model_family": e["model_family"],
                    "vendor": e["vendor"],
                    "lists": [],
                    "routes": [],
                    "static_cost_usd_per_mtok": e["static_cost_usd_per_mtok"],
                    "price_regime": e["price_regime"],
                    "performance_evidence_status": e["performance_evidence_status"],
                },
            )
            m["lists"].append(
                {
                    "list": lst["list"],
                    "rank": e["rank"],
                    "recommendation_eligible": e["recommendation_eligible"],
                    "gate_reasons": e["gate_reasons"],
                }
            )
            for rt in e["routes"]:
                if rt not in m["routes"]:
                    m["routes"].append(rt)
    out = []
    for m in fam.values():
        m["recommendation_eligible_static"] = any(x["recommendation_eligible"] for x in m["lists"])
        cands = [by_route[r] for r in m["routes"] if r in by_route]

        def key(r: dict[str, Any], m: dict[str, Any] = m) -> tuple[Any, ...]:
            cw = r["price"]["live_cw_median_ask_in"]
            usable = r["in_live_catalog"] and r["enabled"] is not False
            return (
                STATUS_ORDER[r["status"]],
                0 if usable else 1,
                cw if cw is not None else math.inf,
                m["routes"].index(r["route"]),
            )

        cands.sort(key=key)
        best = cands[0] if cands else None
        m["route_status"] = {r["route"]: r["status"] for r in cands}
        m["best_route"] = best["route"] if best else None
        m["best_route_status"] = best["status"] if best else None
        m["best_route_confidence"] = best["confidence"] if best else None
        m["best_route_live_min_ask_in"] = best["price"]["live_min_ask_in"] if best else None
        m["best_route_live_min_ask_out"] = best["price"]["live_min_ask_out"] if best else None
        out.append(m)

    def mkey(m: dict[str, Any]) -> tuple[Any, ...]:
        ranks = {x["list"]: x["rank"] for x in m["lists"]}
        static = ranks.get("top20", 100 + ranks.get("daily_shortlist", 900))
        return (STATUS_ORDER.get(m["best_route_status"] or "failing", 3), static)

    out.sort(key=mkey)
    for i, m in enumerate(out, 1):
        m["reliability_adjusted_rank"] = i
    return out


CSV_COLUMNS = [
    "route", "rail", "model_id", "model_label", "lists", "recommendation_eligible_static",
    "in_live_catalog", "enabled", "status", "confidence", "decided_window",
    "decided_service_attempts", "decided_ok", "wilson_lower", "wilson_upper", "status_reasons",
    "live_min_ask_in", "live_min_ask_out", "live_min_tier_avail_in", "live_cw_median_ask_in",
    "live_cw_median_ask_out", "official_in", "official_out", "live_min_ask_in_under_policy",
    "served_ask_in_median_24h", "served_ask_out_median_24h", "served_tier_rank_in_median_24h",
    "share_served_under_policy_24h",
    *[f"{f}_{w}" for w in WINDOWS for f in (
        "requests", "ok", "errors", "client_errors", "service_attempts", "service_success_rate",
        "err_upstream_unavailable", "err_timeout", "err_rate_limited", "err_server_error",
        "ttft_ms_p50", "ttft_ms_p95", "duration_ms_p50", "duration_ms_p95")],
    "platform_rail_state", "live_book_hash", "live_price_ts", "request_log_coverage_end",
    "generated_at", "code_commit", "catalog_raw_sha256",
    # v1.1 (additive): error classes
    *[f"{f}_{w}" for w in WINDOWS for f in (
        "upstream_errors", "account_errors", "unknown_errors", "err_upstream_reject",
        "err_upstream_reject_presumed")],
]  # fmt: skip


def csv_rows(doc: dict[str, Any]) -> list[list[Any]]:
    cat_sha = ((doc["evidence"].get("latest_catalog_fetch") or {}).get("sha256")) or ""
    rows = []
    for r in doc["routes"]:
        flat: dict[str, Any] = {
            k: r[k]
            for k in ("route", "rail", "model_id", "model_label", "recommendation_eligible_static",
                      "in_live_catalog", "enabled", "status", "confidence")
        }  # fmt: skip
        flat["lists"] = ";".join(f"{m['list']}:{m['rank']}" for m in r["lists"])
        flat["status_reasons"] = " | ".join(r["status_reasons"])
        flat.update({k: r["evidence"][k] for k in (
            "decided_window", "decided_service_attempts", "decided_ok", "wilson_lower",
            "wilson_upper", "platform_rail_state", "live_book_hash", "request_log_coverage_end")})  # fmt: skip
        flat.update(r["price"])
        for w in WINDOWS:
            win = r["reliability"][w] or {}
            for f in ("requests", "ok", "errors", "client_errors", "service_attempts",
                      "service_success_rate", "err_upstream_unavailable", "err_timeout",
                      "err_rate_limited", "err_server_error", "ttft_ms_p50", "ttft_ms_p95",
                      "duration_ms_p50", "duration_ms_p95"):  # fmt: skip
                flat[f"{f}_{w}"] = win.get(f)
        for w in WINDOWS:
            win = r["reliability"][w] or {}
            for f in ("upstream_errors", "account_errors", "unknown_errors",
                      "err_upstream_reject", "err_upstream_reject_presumed"):  # fmt: skip
                flat[f"{f}_{w}"] = win.get(f)
        flat["generated_at"] = doc["generated_at"]
        flat["code_commit"] = doc["code_commit"]
        flat["catalog_raw_sha256"] = cat_sha
        rows.append(["" if flat.get(c) is None else flat.get(c) for c in CSV_COLUMNS])
    return rows


def to_csv(doc: dict[str, Any]) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(CSV_COLUMNS)
    w.writerows(csv_rows(doc))
    return buf.getvalue().encode("utf-8")


# ---------------------------------------------------------------- I/O (telemetry host)
def _latest_catalog_fetch(raw_manifest: str) -> dict[str, Any] | None:
    """Last successful /api/catalog fetch from the tail of raw/manifest.jsonl."""
    try:
        with open(raw_manifest, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            fh.seek(max(0, size - 1_000_000))
            tail = fh.read().decode("utf-8", "replace").splitlines()
    except OSError:
        return None
    for ln in reversed(tail):
        try:
            r = json.loads(ln)
        except ValueError:
            continue
        if r.get("endpoint") == "catalog" and r.get("http_status") == 200:
            return {
                "fetched_at": r.get("fetched_at"),
                "sha256": r.get("sha256"),
                "raw_path": r.get("path"),
                "dup_of_previous": bool(r.get("dup_of_previous")),
            }
    return None


def _rows(con: Any, q: str) -> list[dict[str, Any]]:
    rel = con.execute(q)
    cols = [d[0] for d in rel.description]
    return [dict(zip(cols, row, strict=True)) for row in rel.fetchall()]


def _write_atomic(path: str, data: bytes) -> None:
    tmp = path + ".tmp"
    with open(tmp, "wb") as fh:
        fh.write(data)
    os.replace(tmp, path)


def generate(modeled_dir: str, raw_manifest: str, code_commit: str | None) -> dict[str, Any]:
    from . import lake

    mod = os.path.realpath(modeled_dir)
    need = (
        "fact_route_reliability",
        "fact_route_price",
        "inferhub_dim_route",
        "fact_route_status",
        "fact_route_error_breakdown",
    )
    files = {t: os.path.join(mod, t + ".parquet") for t in need}
    missing = [t for t, f in files.items() if not os.path.exists(f)]
    if "fact_route_price" in missing or "inferhub_dim_route" in missing:
        raise RuntimeError(f"modeled tables missing: {missing}")
    con = lake.connect()
    con.execute("SET TimeZone='UTC'")
    rel = (
        _rows(con, f"SELECT * FROM read_parquet('{files['fact_route_reliability']}')")
        if "fact_route_reliability" not in missing
        else []
    )
    prices = _rows(
        con,
        f"SELECT * FROM read_parquet('{files['fact_route_price']}') "
        "QUALIFY row_number() OVER (PARTITION BY route ORDER BY ts DESC) = 1",
    )
    dims = _rows(
        con, f"SELECT route, rail, model_label FROM read_parquet('{files['inferhub_dim_route']}')"
    )
    status = (
        {
            r["rail"]: r
            for r in _rows(
                con,
                f"SELECT * FROM read_parquet('{files['fact_route_status']}') "
                "QUALIFY row_number() OVER (PARTITION BY rail ORDER BY ts DESC) = 1",
            )
        }
        if "fact_route_status" not in missing
        else {}
    )
    h = hashlib.sha256()
    for t in need:
        if os.path.exists(files[t]):
            h.update(f"{t}:{_sha256_file(files[t])}\n".encode())
    build_info: dict[str, Any] = {}
    try:
        with open(os.path.join(mod, "_build.json"), encoding="utf-8") as fh:
            build_info = json.load(fh)
    except OSError:
        pass
    cov = max((r["window_end"] for r in rel if r.get("window_end")), default=None)
    meta = {
        "generated_at": _iso(dt.datetime.now(dt.timezone.utc)),
        "code_commit": code_commit,
        "request_log_coverage_end": _iso(cov),
        "modeled_snapshot": {
            "dir": os.path.basename(mod),
            "run_id": build_info.get("run_id"),
            "built_at": build_info.get("built_at"),
        },
        "latest_catalog_fetch": _latest_catalog_fetch(raw_manifest),
        "inputs_sha256": h.hexdigest(),
    }
    brk = (
        _rows(con, f"SELECT * FROM read_parquet('{files['fact_route_error_breakdown']}')")
        if "fact_route_error_breakdown" not in missing
        else []
    )
    return build(rel, prices, dims, status, load_lists(), meta, brk)


def run(ih_root: str, code_commit: str | None) -> dict[str, Any]:
    """Regenerate data/inferhub/catalogue/route-catalogue.{json,csv} (atomic replace)."""
    doc = generate(
        os.path.join(ih_root, "modeled", "current"),
        os.path.join(ih_root, "raw", "manifest.jsonl"),
        code_commit,
    )
    out = os.path.join(ih_root, "catalogue")
    os.makedirs(out, exist_ok=True)
    body = (json.dumps(doc, indent=1, sort_keys=False, default=str) + "\n").encode("utf-8")
    _write_atomic(os.path.join(out, "route-catalogue.json"), body)
    _write_atomic(os.path.join(out, "route-catalogue.csv"), to_csv(doc))
    return {
        "dir": out,
        "routes": len(doc["routes"]),
        "status_counts": doc["status_counts"],
        "json_sha256": hashlib.sha256(body).hexdigest(),
        "generated_at": doc["generated_at"],
    }
