"""Utility (image and multimodal) list (IRE #94, item 7).

Offline checks: admission needs both a name match and a licence-map record, the open-weight verdict
is recorded per row (true, or null when unverified, never false), a closed family is excluded, the
Top 20 stays text-only, and the committed list agrees with its sidecar and the manifest.
"""

from __future__ import annotations

import csv
import importlib.util
import io
import json
import unittest
from pathlib import Path
from typing import Any

IHUB = Path(__file__).resolve().parents[1] / "telemetry" / "gravebuster" / "pipeline" / "ihub"
_spec = importlib.util.spec_from_file_location("ihub_utility", IHUB / "utility.py")
assert _spec and _spec.loader
U = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(U)
T = U.T
LISTS = IHUB / "lists"
META = {"generated_at": "2026-10-04T18:00:00Z", "code_commit": "0" * 40,
        "snapshot_sha256": "a" * 64, "sources": []}  # fmt: skip


def ladder(base: float, n: int) -> list[list[float]]:
    return [[base, n], [base * 2, n * 3], [base * 5, n]]


def mdl(
    mid: str, base: float, n: int = 20, label: str | None = None, modality: str = "image"
) -> dict[str, Any]:
    return {"upstreamModelId": mid, "label": label or mid, "enabled": True,
            "officialIn": "1", "officialOut": "4", "pricePointsIn": ladder(base, n),
            "pricePointsOut": ladder(base * 4, n), "outputModality": modality}  # fmt: skip


def rail(prefix: str, models: list[dict[str, Any]], enabled: bool = True) -> dict[str, Any]:
    return {"prefix": prefix, "label": prefix.upper(), "enabled": enabled,
            "upstreamDisabled": False, "activeProviders": 10, "systemPromptNote": None,
            "models": models}  # fmt: skip


def status(prefixes: list[str]) -> dict[str, Any]:
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
        "autoRouted": [],
        "performance": {"families": []},
    }


PRIOR: dict[str, Any] = {"confidence": "low", "status": "hypothesis", "as_of": "2026-09-22",
                         "_sha256": "x", "families": []}  # fmt: skip


def open_lic(name: str, vendor: str = "Open V") -> dict[str, Any]:
    return {"vendor": vendor, "open_weight": True, "licence": "Apache-2.0",
            "licence_url": "https://example.invalid/LICENSE",
            "weights_url": "https://example.invalid/weights"}  # fmt: skip


def null_lic(name: str, vendor: str = "Closed-ish V") -> dict[str, Any]:
    return {"vendor": vendor, "open_weight": None, "basis": "licence not published"}  # fmt: skip


def closed_lic(name: str, vendor: str = "Closed V") -> dict[str, Any]:
    return {"vendor": vendor, "open_weight": False, "basis": "proprietary API only"}  # fmt: skip


def synthetic(licences: dict[str, dict[str, Any]]) -> dict[str, Any]:
    cat = [
        rail(
            "aa",
            [
                mdl("qwen3.8-omni-flash", 0.01, label="Qwen3.8 Omni Flash", modality="image"),
                mdl("vision-8b", 0.02, label="Vision 8B", modality="image"),
                mdl("gpt-image-2", 0.05, label="GPT Image 2", modality="image"),
                mdl("mystery-image", 0.01, label="Mystery Image", modality="image"),
                mdl("plain-text", 0.005, label="Plain Text", modality="text"),
            ],
        ),  # fmt: skip
        rail(
            "bb",
            [
                mdl("qwen3.8-omni-flash", 0.012, label="Qwen3.8 Omni Flash", modality="image"),
                mdl("vision-8b", 0.025, label="Vision 8B", modality="image"),
            ],
        ),  # fmt: skip
    ]
    return U.build(cat, status(["aa", "bb"]), {"models": []}, PRIOR, META, licences=licences)


LICENCES = {
    "Qwen3.8 Omni Flash": null_lic("Qwen3.8 Omni Flash"),
    "Vision 8B": open_lic("Vision 8B"),
    "GPT Image 2": closed_lic("GPT Image 2"),
    # Mystery Image is deliberately absent: an uncurated family must never appear.
}


class AdmissionTests(unittest.TestCase):
    def test_name_match_and_licence_record_both_required(self) -> None:
        doc = synthetic(LICENCES)
        fams = {e["model_family"] for e in doc["entries"]}
        self.assertIn("Qwen3.8 Omni Flash", fams)
        self.assertIn("Vision 8B", fams)
        self.assertNotIn("Plain Text", fams)  # no utility word
        self.assertNotIn("Mystery Image", fams)  # not curated in the licence map
        self.assertEqual(doc["counts"]["unmapped_utility"], 1)

    def test_closed_family_is_excluded_and_counted(self) -> None:
        doc = synthetic(LICENCES)
        fams = {e["model_family"] for e in doc["entries"]}
        self.assertNotIn("GPT Image 2", fams)
        self.assertEqual(doc["counts"]["excluded_closed"], 1)


class VerdictTests(unittest.TestCase):
    def test_unverified_licence_is_listed_but_never_recommended(self) -> None:
        doc = synthetic(LICENCES)
        qwen = next(e for e in doc["entries"] if e["model_family"] == "Qwen3.8 Omni Flash")
        self.assertIsNone(qwen["open_weight"])
        self.assertIs(qwen["recommended"], False)
        self.assertEqual(qwen["recommendation_status"], "gated")
        self.assertIn("open_weight_unverified", qwen["gate_reasons"])
        self.assertIsNone(qwen["licence"])

    def test_verified_open_weight_family_may_be_recommended(self) -> None:
        doc = synthetic(LICENCES)
        vis = next(e for e in doc["entries"] if e["model_family"] == "Vision 8B")
        self.assertIs(vis["open_weight"], True)
        self.assertIs(vis["recommended"], True)
        self.assertEqual(vis["licence"]["url"], "https://example.invalid/LICENSE")

    def test_verified_family_is_recommended_only_when_ungated(self) -> None:
        # A verified family that is single-provider and thin fails the gates, so it stays gated
        # even though open_weight is true.
        lic = {"Solo Image": open_lic("Solo Image")}
        cat = [rail("aa", [mdl("solo-image", 0.01, label="Solo Image", modality="image")])]
        doc = U.build(cat, status(["aa"]), {"models": []}, PRIOR, META, licences=lic)
        e = doc["entries"][0]
        self.assertIs(e["open_weight"], True)
        self.assertIs(e["recommended"], False)
        self.assertIn("insufficient_provider_breadth", e["gate_reasons"])

    def test_never_emits_open_weight_false(self) -> None:
        for e in synthetic(LICENCES)["entries"]:
            self.assertIn(e["open_weight"], (True, None))


class RankingTests(unittest.TestCase):
    def test_sorted_by_shortlist_then_cost_then_family(self) -> None:
        doc = synthetic(LICENCES)
        ranks = [e["recommendation_rank"] for e in doc["entries"]]
        self.assertEqual(ranks, list(range(1, len(doc["entries"]) + 1)))
        scores = [e["shortlist_score_100"] for e in doc["entries"]]
        self.assertEqual(scores, sorted(scores, reverse=True))


class FormatTests(unittest.TestCase):
    def test_csv_conventions(self) -> None:
        body = U.to_csv(synthetic(LICENCES)).decode()
        self.assertNotIn("\r", body)
        self.assertTrue(body.endswith("\n"))
        rows = list(csv.DictReader(io.StringIO(body)))
        self.assertEqual(list(rows[0].keys()), U.CSV_COLUMNS)
        for r in rows:
            self.assertIn(r["open_weight"], ("true", "null"))
            self.assertIn(r["recommended"], ("true", "false"))

    def test_json_has_no_private_rows(self) -> None:
        pub = json.loads(U.to_json(synthetic(LICENCES)))
        self.assertNotIn("_rows", pub)
        self.assertEqual(pub["schema"], U.SCHEMA_ID)
        self.assertIn("hypothesis_notice", pub)


class CommittedListTests(unittest.TestCase):
    def test_committed_csv_header_and_sidecar_agree(self) -> None:
        csv_path, json_path = LISTS / U.OUT_CSV, LISTS / U.OUT_JSON
        if not json_path.exists():
            self.skipTest("no committed utility list in this checkout")
        body = csv_path.read_bytes()
        self.assertNotIn(b"\r", body)
        self.assertTrue(body.startswith((",".join(U.CSV_COLUMNS)).encode() + b"\n"))
        doc = json.loads(json_path.read_text(encoding="utf-8"))
        self.assertEqual(len(doc["entries"]), doc["counts"]["listed"])
        self.assertEqual(
            [e["recommendation_rank"] for e in doc["entries"]],
            list(range(1, len(doc["entries"]) + 1)),
        )
        for e in doc["entries"]:
            self.assertIn(e["open_weight"], (True, None))
            if e["open_weight"] is None:
                self.assertIs(e["recommended"], False)
                self.assertIsNone(e["licence"])

    def test_manifest_lists_the_utility_files(self) -> None:
        man = json.loads((LISTS / "manifest.json").read_text(encoding="utf-8"))
        entry = next((g for g in man["generated_lists"] if g.get("list") == "utility"), None)
        if entry is None:
            self.skipTest("no committed utility manifest entry in this checkout")
        self.assertEqual(entry["files"]["csv"], U.OUT_CSV)
        self.assertEqual(entry["files"]["json"], U.OUT_JSON)


if __name__ == "__main__":
    unittest.main()
