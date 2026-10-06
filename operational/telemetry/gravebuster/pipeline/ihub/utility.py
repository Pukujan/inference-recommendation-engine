"""Utility (image and multimodal) recommendation list (IRE #99; item 7 of #94).

The cheap Top 20 and the frontier list are text-only: ``top20.collect_routes`` skips any model
whose ``outputModality`` is not ``text``. That leaves image-generation and multimodal families
with no surface at all, even when the live catalog carries them with live routes. This module
builds ``lists/research_model_utility_recommendations.csv`` and its sidecar JSON for those
families, from the same three GET bodies ``frontier.py`` fetches, so one fetch feeds all three
lists.

Admission is deliberately narrow. A family appears only when both hold:

* its name matches ``UTILITY_RX`` (``omni``, ``image``, ``video``, ``audio``, ``tts``,
  ``speech``, ``vision``), and
* it is already named in ``model_licences.v1.json``.

The licence map is the curated admission list, exactly as it is for the text tiers: someone
entered the family on purpose, so an unknown image model can never slip in and a closed family
is excluded by its own record. The open-weight verdict is then recorded per row rather than
used to drop the row, because today no multimodal family is verified open-weight:

* ``open_licence()`` verifies the family -> ``open_weight: true``, ``recommended`` may be true.
* the record exists but is not verified (``open_weight: null``) -> ``open_weight: null``,
  ``recommended: false``, the ``open_weight_unverified`` gate and a licence-unverified caveat.
* the record marks the family closed (``open_weight: false``) or its name or vendor matches
  ``CLOSED_FAMILY_RX`` -> excluded, counted in ``counts.excluded_closed``.

Ranking uses availability and price signals only (provider breadth, supply depth, 7-day
availability, price utility); capability and recency are not used because the utility families
carry neither. Each row is a hypothesis; the sidecar JSON carries the per-row detail.

``build`` and the writers are standard library only so CI can test them offline. The key is read
by ``frontier.fetch_live`` and only ever sent as a GET header; nothing here prints it.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import math
import os
import re
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

try:  # package import on the host; file import (tests, CLI) falls back to a sibling load
    from . import licences as L
except ImportError:  # pragma: no cover
    import importlib.util as _ilu2

    _spec2 = _ilu2.spec_from_file_location(
        "ihub_licences", os.path.join(os.path.dirname(os.path.abspath(__file__)), "licences.py")
    )
    assert _spec2 and _spec2.loader
    L = _ilu2.module_from_spec(_spec2)
    _spec2.loader.exec_module(L)

try:  # reuse the Top 20 route collectors and scoring helpers
    from . import top20 as T
except ImportError:  # pragma: no cover
    import importlib.util as _ilu3

    _spec3 = _ilu3.spec_from_file_location(
        "ihub_top20", os.path.join(os.path.dirname(os.path.abspath(__file__)), "top20.py")
    )
    assert _spec3 and _spec3.loader
    T = _ilu3.module_from_spec(_spec3)
    _spec3.loader.exec_module(T)

SCHEMA_ID = "ihub-utility-recommendations/v1"
HERE = os.path.dirname(os.path.abspath(__file__))
LISTS_DIR = os.path.join(HERE, "lists")
OUT_CSV = "research_model_utility_recommendations.csv"
OUT_JSON = "research_model_utility_recommendations.json"
GENERATOR = "operational/telemetry/gravebuster/pipeline/ihub/utility.py"
TOP_N = 20

# A family is utility when its name names a non-text capability. This is the admission regex;
# the licence map is the second gate (a family must be named there too).
UTILITY_RX = re.compile(r"\b(omni|image|video|audio|tts|speech|vision)\b", re.I)

CSV_COLUMNS = [
    "recommendation_rank", "model_family", "vendor", "utility_kind", "open_weight",
    "recommended", "gate_reasons", "priced_provider_count", "catalog_availability_score_100",
    "price_supply_availability_score_100", "public_availability_7d_pct",
    "supply_weighted_median_cost_usdc_per_1m", "price_utility_score_100", "price_regime",
    "reliability_score_100", "shortlist_score_100", "model_ids",
    "best_route_min_ask_in_usdc_per_1m", "best_route_min_ask_out_usdc_per_1m",
]  # fmt: skip

# Availability and price only: utility families carry no capability or recency prior, so those
# weights are zero. These numbers are an uncalibrated hypothesis, not a fitted result.
UTILITY_WEIGHTS = {
    "price_utility": 0.25,
    "reliability": 0.30,
    "catalog_availability": 0.25,
    "price_supply_availability": 0.20,
    "capability": 0.0,
    "recency": 0.0,
    "boost_points_per_unit": 0.0,
}

METHOD: dict[str, Any] = {
    "admission": "name matches UTILITY_RX and is named in model_licences.v1.json",
    "open_weight_verdict": "true when open_licence() verifies the family; null when the record "
    "is unverified (listed with recommended false); closed records are excluded entirely",
    "near_free_cost_cap_usd_per_mtok": 0.10,
    "catalog_availability": "100 * (ln(1 + providers) / ln(7)) ** 0.8, capped at 100",
    "price_supply_availability": "percentile rank (0-100) of total live input listings among "
    "priced utility families",
    "public_availability_7d": "InferHub /api/status availability7dPct of the best route's rail",
    "reliability": "equal to public_availability_7d_pct (no private probes in the repo)",
    "shortlist_weights": dict(UTILITY_WEIGHTS),
    "shortlist_calibration": "Not fitted. There is no historical utility list to calibrate "
    "against, so these weights are a stated hypothesis: availability and price only.",
    "ranking": "shortlist_score_100 desc, then supply_weighted_median_cost asc, then model_family",
    "model_ids_order": "best route first: health status, then a supply-depth tie-break inside a "
    "5% price band around the cheapest min ask at the same health tier, then the blended (3:1) "
    "min ask, then route id",
}
MIN_CATALOG_AVAILABILITY = 55.0
MIN_PROVIDERS = 2


def fmt(v: Any, nd: int = 6) -> str:
    """Same cell conventions as the Top 20: bool -> true/false, None -> "", compact numbers."""
    return T.fmt(v, nd)


def _file_sha256(path: str) -> str:
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def _ow_cell(v: bool | None) -> str:
    return {True: "true", False: "false", None: "null"}[v]


def _kind(name: str) -> str | None:
    m = UTILITY_RX.search(name)
    return m.group(1).lower() if m else None


# ---------------------------------------------------------------- build (pure)
def build(
    catalog: list[dict[str, Any]],
    status: dict[str, Any],
    market: dict[str, Any],
    prior: dict[str, Any],
    meta: dict[str, Any],
    licences: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Assemble the utility document (pure). ``market`` is accepted for parity with frontier."""
    del market
    licences = L.load_licences() if licences is None else licences
    routes = T.collect_routes(catalog, status, prior, text_only=False)
    live = [r for r in routes if r["health"]["status"] != "unavailable" and r["ladder_in"]]
    dist_in = T.distribution([r["ladder_in"] for r in live])
    dist_out = T.distribution([r["ladder_out"] for r in live])
    for r in routes:
        r["effective_cost"] = T.blended_cost(
            T.effective_price(r["ladder_in"], dist_in), T.effective_price(r["ladder_out"], dist_out)
        )
    fprior = F.load_prior()
    fams: dict[str, list[dict[str, Any]]] = {}
    for r in routes:
        name = r["model_family"] or r["label"]
        fams.setdefault(name, []).append(r)

    excluded_closed: list[str] = []
    unmapped: list[str] = []
    rows = []
    for name, rs in fams.items():
        if not UTILITY_RX.search(name):
            continue
        rec = licences.get(name)
        if rec is None:  # not curated in the licence map: never shown
            unmapped.append(name)
            continue
        vendor_rec = str(rec.get("vendor") or "")
        if (
            rec.get("open_weight") is False
            or L.CLOSED_FAMILY_RX.search(name)
            or L.CLOSED_FAMILY_RX.search(vendor_rec)
        ):
            excluded_closed.append(name)
            continue
        rs = T._route_order(rs)
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
        util, regime = T.price_utility(cost)
        best = rs[0]
        avail7 = best["rail_availability_7d_pct"] if best in lv else None
        rows.append(
            {
                "model_family": name,
                "vendor": rec.get("vendor") or T._vendor_guess(rs, fprior),
                "utility_kind": _kind(name),
                "open_weight": True if L.open_licence(name, licences) else None,
                "priced_provider_count": providers,
                "catalog_availability_score_100": T.catalog_availability(providers),
                "listings_total": sum(r["listings_in"] for r in lv),
                "public_availability_7d_pct": avail7,
                "supply_weighted_median_cost_usdc_per_1m": cost,
                "best_route_min_ask_in_usdc_per_1m": best["min_ask_in"],
                "best_route_min_ask_out_usdc_per_1m": best["min_ask_out"],
                "price_utility_score_100": util,
                "price_regime": regime,
                "reliability_score_100": avail7 if avail7 is not None else 0.0,
                "routes": rs,
                "live_routes": lv,
            }
        )

    priced = sorted(math.log1p(r["listings_total"]) for r in rows if r["listings_total"] > 0)
    for row in rows:
        lt = row["listings_total"]
        row["price_supply_availability_score_100"] = (
            100.0 * T._pct_rank(priced, math.log1p(lt)) if lt > 0 else 0.0
        )
        row["shortlist_score_100"] = T.shortlist_score(
            {
                "price_utility": row["price_utility_score_100"],
                "reliability": row["reliability_score_100"],
                "catalog_availability": row["catalog_availability_score_100"],
                "price_supply_availability": row["price_supply_availability_score_100"],
                "capability": 0.0,
                "recency": 0.0,
                "boost": 0.0,
            },
            UTILITY_WEIGHTS,
        )
        gates = []
        if row["priced_provider_count"] < MIN_PROVIDERS:
            gates.append("insufficient_provider_breadth")
        if row["catalog_availability_score_100"] < MIN_CATALOG_AVAILABILITY:
            gates.append("catalog_availability_below_minimum")
        if row["price_regime"] == "unpriced":
            gates.append("missing_price")
        if not any(r["health"]["status"] == "healthy" for r in row["live_routes"]):
            gates.append("not_routing_eligible")
        if row["open_weight"] is not True:
            gates.append("open_weight_unverified")
        row["gate_reasons"] = gates
        row["recommended"] = row["open_weight"] is True and not gates

    def key(r: dict[str, Any]) -> tuple[Any, ...]:
        c = r["supply_weighted_median_cost_usdc_per_1m"]
        return (-round(r["shortlist_score_100"], 4), c if c is not None else math.inf,
                r["model_family"])  # fmt: skip

    rows.sort(key=key)
    top = rows[:TOP_N]
    entries = []
    for i, r in enumerate(top, 1):
        r["recommendation_rank"] = i
        b = r["routes"][0]
        lic = L.open_licence(r["model_family"], licences)
        entry: dict[str, Any] = {
            "recommendation_rank": i,
            "model_family": r["model_family"],
            "vendor": r["vendor"] or None,
            "utility_kind": r["utility_kind"],
            "open_weight": r["open_weight"],
            "recommended": r["recommended"],
            "recommendation_status": "recommended" if r["recommended"] else "gated",
            "gate_reasons": r["gate_reasons"],
            "priced_provider_count": r["priced_provider_count"],
            "catalog_availability_score_100": round(r["catalog_availability_score_100"], 2),
            "price_supply_availability_score_100": round(
                r["price_supply_availability_score_100"], 3
            ),
            "public_availability_7d_pct": r["public_availability_7d_pct"],
            "shortlist_score_100": round(r["shortlist_score_100"], 4),
            "price_regime": r["price_regime"],
            "supply_weighted_median_cost_usd_per_mtok": F._r(
                r["supply_weighted_median_cost_usdc_per_1m"]
            ),
            "best_route": b["route"],
            "best_route_health": b["health"]["status"],
            "best_route_health_reasons": b["health"]["reasons"],
            "best_route_min_ask_in": F._r(b["min_ask_in"]),
            "best_route_min_ask_out": F._r(b["min_ask_out"]),
            "best_route_system_prompt_handling": b["system_prompt_handling"],
            "best_route_preferred_endpoint": b["preferred_endpoint"],
            "best_route_caveats": b["caveats"],
            "routes": [x["route"] for x in r["routes"]],
            "licence": (
                {
                    "name": lic["licence"],
                    "url": lic["licence_url"],
                    "weights_url": lic["weights_url"],
                }  # fmt: skip
                if lic
                else None
            ),
        }
        entries.append(entry)
    return {
        "schema": SCHEMA_ID,
        "hypothesis_notice": (
            "Each row is a hypothesis, not a fact. Utility families are ranked on availability "
            "and price only, because they carry no capability or recency prior. The open_weight "
            "verdict is per row: true means a published licence and weights were verified, null "
            "means the family is listed but its licence is unverified (recommended stays false). "
            "Prices are live InferHub asks in USD per 1M tokens at generated_at; the served price "
            "can be higher."
        ),
        "generated_at": meta["generated_at"],
        "code_commit": meta.get("code_commit"),
        "provenance": {
            "snapshot_sha256": meta.get("snapshot_sha256"),
            "sources": meta.get("sources", []),
            "licence_map": {
                "file": "operational/telemetry/gravebuster/pipeline/ihub/model_licences.v1.json",
                "sha256": _file_sha256(L.LICENCES_SRC),
            },
            "generator": GENERATOR,
        },
        "method": METHOD,
        "counts": {
            "families_considered": len(fams),
            "listed": len(top),
            "recommended": sum(1 for e in entries if e["recommended"]),
            "gated": sum(1 for e in entries if not e["recommended"]),
            "licence_unverified": sum(1 for e in entries if e["open_weight"] is None),
            "excluded_closed": len(excluded_closed),
            "unmapped_utility": len(unmapped),
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
                r["utility_kind"] or "",
                _ow_cell(r["open_weight"]),
                fmt(r["recommended"]),
                "; ".join(r["gate_reasons"]),
                r["priced_provider_count"],
                fmt(r["catalog_availability_score_100"], 2),
                fmt(r["price_supply_availability_score_100"], 3),
                fmt(r["public_availability_7d_pct"], 3),
                fmt(r["supply_weighted_median_cost_usdc_per_1m"], 6),
                fmt(r["price_utility_score_100"], 3),
                r["price_regime"],
                fmt(r["reliability_score_100"], 3),
                fmt(r["shortlist_score_100"], 4),
                "; ".join(x["route"] for x in r["routes"]),
                fmt(r["best_route_min_ask_in_usdc_per_1m"], 6),
                fmt(r["best_route_min_ask_out_usdc_per_1m"], 6),
            ]
        )
    return buf.getvalue().encode("utf-8")


def to_json(doc: dict[str, Any]) -> bytes:
    pub = {k: v for k, v in doc.items() if not k.startswith("_")}
    return (json.dumps(pub, indent=1, ensure_ascii=False) + "\n").encode("utf-8")


def update_manifest(lists_dir: str, written: dict[str, bytes], doc: dict[str, Any]) -> None:
    """Add or replace the utility entry under manifest.generated_lists (other lists untouched)."""
    path = os.path.join(lists_dir, "manifest.json")
    with open(path, encoding="utf-8") as fh:
        man = json.load(fh)
    entry = {
        "list": "utility",
        "files": {"csv": OUT_CSV, "json": OUT_JSON},
        "sha256": {name: hashlib.sha256(body).hexdigest() for name, body in written.items()},
        "schema": SCHEMA_ID,
        "generator": GENERATOR,
        "generated_at": doc["generated_at"],
        "code_commit": doc["code_commit"],
        "snapshot_sha256": doc["provenance"]["snapshot_sha256"],
        "rank_column": "recommendation_rank",
        "eligible_column": "recommended",
        "meaning": "Utility tier: image-generation and multimodal families, ranked on availability "
        "and price. Admission is name match plus a record in model_licences.v1.json. The open_weight "
        "verdict is per row: a family with an unverified licence is listed with open_weight null and "
        "recommended false; closed families are excluded. Separate from the Top 20 and the frontier "
        "list, which it never changes.",
        "issue": "https://github.com/Pukujan/inference-recommendation-engine/issues/99",
    }
    others = [g for g in man.get("generated_lists", []) if g.get("list") != "utility"]
    man["generated_lists"] = others + [entry]
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(man, indent=1, ensure_ascii=False) + "\n")


def generate(raw_dir: str, out_dir: str, code_commit: str | None) -> dict[str, Any]:
    parsed, meta = F.load_raw(raw_dir)
    meta["generated_at"] = F._iso_now()
    meta["code_commit"] = code_commit
    doc = build(parsed["catalog"], parsed["status"], parsed["market"], T.load_prior(), meta)
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
    ap = argparse.ArgumentParser(prog="utility", description=(__doc__ or "").splitlines()[0])
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
        print(
            json.dumps(
                {
                    "listed": doc["counts"]["listed"],
                    "recommended": doc["counts"]["recommended"],
                    "licence_unverified": doc["counts"]["licence_unverified"],
                    "excluded_closed": doc["counts"]["excluded_closed"],
                }
            )
        )
        return 0
    except Exception as e:  # noqa: BLE001
        print(f"{type(e).__name__}: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
