"""Cheap Top 20 regenerated in the repo (IRE #76).

Offline checks: the CSV keeps the exact 2026-09-22 header and cell conventions, the supply model
matches src/supply.mjs, gates and ranking follow the documented method, the calibrated weights
reproduce the old list's scores, and the committed list agrees with the manifest and sidecar.
"""

from __future__ import annotations

import csv
import hashlib
import importlib.util
import io
import json
import math
import re
import subprocess
import unittest
from pathlib import Path
from typing import Any

IHUB = Path(__file__).resolve().parents[1] / "telemetry" / "gravebuster" / "pipeline" / "ihub"
_spec = importlib.util.spec_from_file_location("ihub_top20", IHUB / "top20.py")
assert _spec and _spec.loader
T = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(T)
LISTS = IHUB / "lists"
OLD_HEADER = (
    "recommendation_rank,model_family,vendor,recommendation_eligible,gate_reasons,tier,"
    "capability_score_100,release_date,recency_days,priced_provider_count,"
    "catalog_availability_score_100,price_supply_availability_score_100,"
    "public_availability_7d_pct,supply_weighted_median_cost_usdc_per_1m,price_utility_score_100,"
    "price_regime,reliability_weight_boost,reliability_score_100,performance_evidence_status,"
    "shortlist_score_100,model_ids"
)
META = {"generated_at": "2026-10-04T18:00:00Z", "code_commit": "0" * 40,
        "snapshot_sha256": "a" * 64, "sources": []}  # fmt: skip


def ladder(base: float, n: int) -> list[list[float]]:
    return [[base, n], [base * 2, n * 3], [base * 5, n]]


def rail(prefix: str, models: list[dict[str, Any]], enabled: bool = True) -> dict[str, Any]:
    return {"prefix": prefix, "label": prefix.upper(), "enabled": enabled,
            "upstreamDisabled": False, "activeProviders": 10, "systemPromptNote": None,
            "models": models}  # fmt: skip


def mdl(mid: str, base: float, n: int = 20, label: str | None = None) -> dict[str, Any]:
    return {"upstreamModelId": mid, "label": label or mid, "enabled": True,
            "officialIn": "1", "officialOut": "4", "pricePointsIn": ladder(base, n),
            "pricePointsOut": ladder(base * 4, n), "outputModality": "text"}  # fmt: skip


def status(prefixes: list[str], alias_states: dict[str, str] | None = None) -> dict[str, Any]:
    return {
        "families": [
            {
                "prefix": p,
                "slug": p,
                "state": "operational",
                "availability24hPct": 99.0,
                "availability7dPct": 98.5,
            }
            for p in prefixes
        ],  # fmt: skip
        "autoRouted": [
            {"alias": a, "state": s, "availability24hPct": 99.0}
            for a, s in (alias_states or {}).items()
        ],  # fmt: skip
        "performance": {"families": []},
    }


PRIOR = {
    "confidence": "low", "status": "hypothesis", "as_of": "2026-09-22", "_sha256": "x",
    "families": [
        {"model_family": "Cheap One", "vendor": "V1", "tier": "top", "capability_score_100": "40",
         "release_date": "2026-09-01", "route_ids": ["aa/cheap-one"], "segments": ["cheap-one"]},
        {"model_family": "Pricey", "vendor": "V2", "tier": "top", "capability_score_100": "45",
         "release_date": "2026-09-01", "route_ids": ["aa/pricey"], "segments": ["pricey"]},
        {"model_family": "Lonely", "vendor": "V3", "tier": "top", "capability_score_100": "45",
         "release_date": None, "route_ids": ["aa/lonely"], "segments": ["lonely"]},
    ],
}  # fmt: skip


def synthetic() -> dict[str, Any]:
    cat = [
        rail(
            "aa",
            [
                mdl("cheap-one", 0.005),
                mdl("pricey", 0.5),
                mdl("lonely", 0.01),
                mdl("mystery-1", 0.004),
            ],
        ),  # fmt: skip
        rail("bb", [mdl("cheap-one", 0.006), mdl("pricey", 0.6), mdl("mystery-1", 0.004)]),
        rail("cc", [mdl("cheap-one", 0.007)], enabled=False),
    ]
    return T.build(cat, status(["aa", "bb", "cc"]), {"models": []}, PRIOR, META)


class FormatTests(unittest.TestCase):
    def test_header_is_byte_compatible(self) -> None:
        self.assertEqual(",".join(T.CSV_COLUMNS), OLD_HEADER)
        committed = (LISTS / T.OUT_CSV).read_bytes()
        self.assertTrue(committed.startswith(OLD_HEADER.encode() + b"\n"))
        self.assertNotIn(b"\r", committed)
        self.assertTrue(committed.endswith(b"\n"))

    def test_cells_follow_old_conventions(self) -> None:
        self.assertEqual(T.fmt(100.0), "100")
        self.assertEqual(T.fmt(0.0221080, 6), "0.022108")
        self.assertEqual(T.fmt(True), "true")
        self.assertEqual(T.fmt(None), "")
        body = T.to_csv(synthetic()).decode()
        rows = list(csv.DictReader(io.StringIO(body)))
        for r in rows:
            self.assertIn(r["recommendation_eligible"], ("true", "false"))
            self.assertNotRegex(r["shortlist_score_100"], r"\.0$")
            self.assertNotIn(";", r["model_ids"].replace("; ", "|"))


class SupplyModelTests(unittest.TestCase):
    def test_matches_src_supply_mjs(self) -> None:
        # Golden values from src/supply.mjs weightPriceLadder + weightedGeometricMean(0.4/0.6).
        a = T._ladder([[0.01, 3], [0.02, 40], [0.05, 200], [0.1, 12]])
        b = T._ladder([[0.04, 3], [0.08, 40], [0.2, 200], [0.4, 12]])
        c = T._ladder([[0.5, 1], [1, 5]])
        pi = T.effective_price(a, T.distribution([a, c]))
        po = T.effective_price(b, T.distribution([b, c]))
        assert pi is not None and po is not None
        self.assertAlmostEqual(pi, 0.043506054604478366, places=12)
        self.assertAlmostEqual(po, 0.17402421841791346, places=12)
        self.assertAlmostEqual(T.blended_cost(pi, po) or 0, 0.09995066671315095, places=12)

    def test_price_utility(self) -> None:
        self.assertEqual(T.price_utility(0.05), (100.0, "near_free"))
        self.assertEqual(T.price_utility(None), (0.0, "unpriced"))
        u, reg = T.price_utility(0.2)
        self.assertEqual(reg, "priced")
        self.assertAlmostEqual(u, 50.0)

    def test_catalog_availability_is_monotone(self) -> None:
        vals = [T.catalog_availability(n) for n in range(0, 9)]
        self.assertEqual(vals, sorted(vals))
        self.assertEqual(vals[6], 100.0)


class BuildTests(unittest.TestCase):
    def test_ranking_and_gates(self) -> None:
        doc = synthetic()
        by = {e["model_family"]: e for e in doc["entries"]}
        self.assertEqual(doc["entries"][0]["model_family"], "Cheap One")
        self.assertTrue(by["Cheap One"]["recommendation_eligible"])
        self.assertEqual(by["Cheap One"]["priced_provider_count"], 2)  # cc rail disabled
        self.assertEqual(by["Cheap One"]["routes"][-1], "cc/cheap-one")  # unavailable last
        self.assertIn("insufficient_provider_breadth", by["Lonely"]["gate_reasons"])
        self.assertIn("release_date_unknown", by["Lonely"]["gate_reasons"])
        self.assertEqual(by["Pricey"]["price_regime"], "priced")
        m = by["mystery-1"]
        self.assertEqual((m["tier"], m["capability_confidence"]), ("unscored", "none"))
        self.assertIn("tier_below_minimum", m["gate_reasons"])
        ranks = [e["recommendation_rank"] for e in doc["entries"]]
        self.assertEqual(ranks, list(range(1, len(ranks) + 1)))
        scores = [e["shortlist_score_100"] for e in doc["entries"]]
        self.assertEqual(scores, sorted(scores, reverse=True))

    def test_model_wide_outage_gates_every_route(self) -> None:
        cat = [rail("aa", [mdl("cheap-one", 0.005)]), rail("bb", [mdl("cheap-one", 0.006)])]
        doc = T.build(cat, status(["aa", "bb"], {"cheap-one": "major_outage"}),
                      {"models": []}, PRIOR, META)  # fmt: skip
        self.assertIn("not_routing_eligible", doc["entries"][0]["gate_reasons"])

    def test_deterministic(self) -> None:
        self.assertEqual(T.to_csv(synthetic()), T.to_csv(synthetic()))
        self.assertEqual(T.to_json(synthetic()), T.to_json(synthetic()))

    def test_calibrated_weights_reproduce_old_scores(self) -> None:
        old = subprocess.run(
            ["git", "-C", str(IHUB), "show",
             "3492b2d:operational/telemetry/gravebuster/pipeline/ihub/lists/" + T.OUT_CSV],
            capture_output=True, text=True, check=False,
        )  # fmt: skip
        if old.returncode != 0:
            self.skipTest("2026-09-22 list not reachable in this checkout (shallow clone)")
        errs = []
        for r in csv.DictReader(io.StringIO(old.stdout)):
            days = int(r["recency_days"]) if r["recency_days"] else None
            s = T.shortlist_score({
                "price_utility": float(r["price_utility_score_100"]),
                "reliability": float(r["reliability_score_100"]),
                "catalog_availability": float(r["catalog_availability_score_100"]),
                "price_supply_availability": float(r["price_supply_availability_score_100"]),
                "capability": float(r["capability_score_100"]),
                "recency": T.recency_score(days),
                "boost": float(r["reliability_weight_boost"]),
            })  # fmt: skip
            errs.append(s - float(r["shortlist_score_100"]))
        self.assertLess(max(abs(e) for e in errs), 1.5)
        self.assertLess(math.sqrt(sum(e * e for e in errs) / len(errs)), 0.75)


class CommittedListTests(unittest.TestCase):
    def test_committed_list_matches_manifest_and_sidecar(self) -> None:
        man = json.loads((LISTS / "manifest.json").read_text(encoding="utf-8"))
        spec = {x["list"]: x for x in man["lists"]}["top20"]
        raw = (LISTS / spec["file"]).read_bytes()
        self.assertEqual(hashlib.sha256(raw).hexdigest(), spec["sha256"])
        side = (LISTS / spec["sidecar_json"]).read_bytes()
        self.assertEqual(hashlib.sha256(side).hexdigest(), spec["sidecar_json_sha256"])
        doc = json.loads(side)
        rows = list(csv.DictReader(io.StringIO(raw.decode())))
        self.assertEqual(len(rows), 20)
        self.assertEqual([int(r["recommendation_rank"]) for r in rows], list(range(1, 21)))
        for r, e in zip(rows, doc["entries"], strict=True):
            self.assertEqual(r["model_family"], e["model_family"])
            self.assertEqual(r["recommendation_eligible"] == "true", e["recommendation_eligible"])
            self.assertEqual(r["model_ids"].split("; ")[0], e["best_route"])
        prior_sha = hashlib.sha256((IHUB / "top20_prior.v1.json").read_bytes()).hexdigest()
        self.assertEqual(doc["provenance"]["capability_prior"]["sha256"], prior_sha)
        self.assertRegex(doc["code_commit"] or "", r"^[0-9a-f]{40}$")
        self.assertIsNone(re.search(rb"sk-[A-Za-z0-9]{8}|Bearer ", raw + side))


if __name__ == "__main__":
    unittest.main()
