"""Daily read-only feed (IRE #75): open-weight only, shape, schema, provenance, the guards."""

from __future__ import annotations

import copy
import datetime as dt
import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path

import jsonschema

IHUB = Path(__file__).resolve().parents[1] / "telemetry" / "gravebuster" / "pipeline" / "ihub"
_spec = importlib.util.spec_from_file_location("ihub_feed", IHUB / "feed.py")
assert _spec and _spec.loader
FD = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(FD)
SCHEMA_V1 = json.loads((IHUB / "schemas" / "feed.v1.schema.json").read_text(encoding="utf-8"))
SCHEMA_V2 = json.loads((IHUB / "schemas" / "feed.v2.schema.json").read_text(encoding="utf-8"))
LICENCES = json.loads((IHUB / "model_licences.v1.json").read_text(encoding="utf-8"))
COMMIT = "1" * 40
V = jsonschema.Draft202012Validator
CLOSED_NAMES = ("GPT", "Claude", "Gemini", "Grok", "Muse Spark", "OpenAI", "Anthropic", "Google")


def build(licences: dict | None = None) -> dict:
    top20, frontier, sources = FD.load_sources()
    return FD.build_feed(top20, frontier, sources, COMMIT, "2026-10-04T19:00:00Z", licences)


def all_list_families() -> set[str]:
    top20, frontier, _ = FD.load_sources()
    return {e["model_family"] for e in top20["entries"]} | {
        m["model_family"] for m in frontier["models"]
    }


def entries(doc: dict) -> list[dict]:
    return [e for t in doc["tiers"].values() for e in t["entries"]]


class FeedShapeTests(unittest.TestCase):
    def test_feed_from_committed_lists_matches_v2_schema(self) -> None:
        doc = build()
        jsonschema.validate(doc, SCHEMA_V2, cls=V)
        self.assertEqual(set(doc["tiers"]), {"cheap", "strongest_open"})
        cheap = doc["tiers"]["cheap"]["entries"]
        self.assertEqual([e["rank"] for e in cheap], list(range(1, len(cheap) + 1)))
        self.assertTrue(0 < len(cheap) <= 20)
        strong = doc["tiers"]["strongest_open"]["entries"]
        self.assertTrue(strong and all(e["recommended"] for e in strong))
        self.assertEqual([e["rank"] for e in strong], list(range(1, len(strong) + 1)))
        for e in cheap:
            self.assertEqual(e["best_route"], e["routes"][0])
            if not e["recommended"]:
                self.assertTrue(e["gate_reasons"])

    def test_v1_mirror_carries_the_same_open_weight_data(self) -> None:
        doc = build()
        v1 = FD.to_v1(doc)
        jsonschema.validate(v1, SCHEMA_V1, cls=V)
        self.assertEqual(v1["schema_version"], "ire-feed/v1")
        self.assertIn("feed/v2/today.json", v1["superseded_by"])
        self.assertEqual(v1["tiers"]["frontier"], doc["tiers"]["strongest_open"])
        self.assertEqual(FD.open_weight_problems(v1), [])

    def test_provenance_and_staleness(self) -> None:
        doc = build()
        self.assertEqual(doc["schema_version"], "ire-feed/v2")
        self.assertIs(doc["open_weight_only"], True)
        self.assertEqual(doc["code_commit"], COMMIT)
        self.assertEqual(set(doc["sources"]), {"top20", "frontier", "licences"})
        oldest = min(FD._parse(doc["tiers"][t]["as_of"]) for t in ("cheap", "strongest_open"))
        self.assertEqual(FD._parse(doc["stale_after"]), oldest + dt.timedelta(hours=36))

    def test_et_day(self) -> None:
        self.assertEqual(FD._et_day("2026-10-05T03:30:00Z"), "2026-10-04")  # EDT
        self.assertEqual(FD._et_day("2026-12-05T04:30:00Z"), "2026-12-04")  # EST
        self.assertEqual(FD._et_day("2026-12-05T05:30:00Z"), "2026-12-05")

    def test_write_feed_keeps_history_and_index(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            days = Path(d, "feed", "v2", "days")
            days.mkdir(parents=True)
            days.joinpath("2026-10-01.json").write_text("{}\n", encoding="utf-8")
            today = FD.write_feed(d, COMMIT)
            for ver in ("v1", "v2"):
                idx = json.loads(Path(d, "feed", ver, "index.json").read_text(encoding="utf-8"))
                self.assertIn(today["day_et"], [x["day_et"] for x in idx["days"]])
                self.assertTrue(idx["today"]["url"].endswith(f"feed/{ver}/today.json"))
                self.assertTrue(all(f"/feed/{ver}/days/" in x["url"] for x in idx["days"]))
            idx2 = json.loads(Path(d, "feed", "v2", "index.json").read_text(encoding="utf-8"))
            self.assertEqual([x["day_et"] for x in idx2["days"]][-1], "2026-10-01")
            self.assertEqual(
                Path(d, "feed", "v2", "today.json").read_bytes(),
                days.joinpath(today["day_et"] + ".json").read_bytes(),
            )
            self.assertEqual(
                json.loads(Path(d, "feed", "v2", "schema.json").read_text(encoding="utf-8")),
                SCHEMA_V2,
            )
            self.assertEqual(FD.check_tree(d), [])


class OpenWeightTests(unittest.TestCase):
    def test_every_entry_is_open_weight_with_a_licence(self) -> None:
        for e in entries(build()):
            self.assertIs(e["open_weight"], True)
            rec = LICENCES["families"][e["model_family"]]
            self.assertIs(rec["open_weight"], True)
            self.assertEqual(e["licence"]["url"], rec["licence_url"])
            self.assertEqual(e["licence"]["weights_url"], rec["weights_url"])

    def test_no_closed_family_in_public_feed(self) -> None:
        doc = build()
        text = json.dumps(entries(doc))
        for e in entries(doc):
            for field in (e["model_family"], e["vendor"] or "", e["best_route"], *e["routes"]):
                self.assertIsNone(FD.CLOSED_FAMILY_RX.search(field), field)
        for name in ("GPT", "Gemini", "Grok", "Muse Spark", "Anthropic", "OpenAI"):
            self.assertNotIn(name, text)

    def test_closed_families_in_the_lists_are_dropped(self) -> None:
        fams = all_list_families()
        closed = {f for f in fams if any(n.lower() in f.lower() for n in CLOSED_NAMES)}
        self.assertTrue(closed, "lists are expected to contain closed families today")
        published = {e["model_family"] for e in entries(build())}
        self.assertFalse(closed & published)

    def test_unknown_family_is_excluded(self) -> None:
        fams = all_list_families()
        published = {e["model_family"] for e in entries(build())}
        for f in fams - set(LICENCES["families"]):
            self.assertNotIn(f, published)
        # Dropping a family from the map removes it from the feed.
        lic = copy.deepcopy(LICENCES["families"])
        lic.pop("GLM 5.3")
        self.assertNotIn("GLM 5.3", {e["model_family"] for e in entries(build(lic))})
        # open_weight null (unverified) is excluded too.
        lic["GLM 5.1"]["open_weight"] = None
        self.assertNotIn("GLM 5.1", {e["model_family"] for e in entries(build(lic))})

    def test_licence_map_is_well_formed(self) -> None:
        fams = LICENCES["families"]
        self.assertTrue(fams)
        for name, rec in fams.items():
            self.assertIn(rec["open_weight"], (True, False, None), name)
            if rec["open_weight"] is True:
                for k in ("licence", "licence_url", "weights_url", "vendor"):
                    self.assertTrue(rec.get(k), f"{name} missing {k}")
                self.assertTrue(rec["licence_url"].startswith("https://"))
                self.assertTrue(rec["weights_url"].startswith("https://huggingface.co/"))
                self.assertIsNone(FD.CLOSED_FAMILY_RX.search(name + " " + rec["vendor"]), name)
            else:
                self.assertTrue(rec.get("basis"), name)
        for vendor, rec in LICENCES["closed_vendors"].items():
            self.assertTrue(FD.CLOSED_FAMILY_RX.search(vendor + " " + rec["families"]), vendor)

    def test_guard_rejects_injected_closed_family(self) -> None:
        doc = build()
        bad = copy.deepcopy(doc["tiers"]["cheap"]["entries"][0])
        bad.update(model_family="Claude Opus 5", vendor="Anthropic", best_route="ant/claude-opus-5")
        bad["routes"] = ["ant/claude-opus-5"]
        doc["tiers"]["strongest_open"]["entries"].append(bad)
        probs = FD.open_weight_problems(doc)
        self.assertTrue(any("closed model family" in p for p in probs))
        self.assertTrue(any("no verified open-weight licence" in p for p in probs))
        # A closed model hidden behind an open-looking name still trips the route check.
        doc2 = build()
        doc2["tiers"]["cheap"]["entries"][0]["routes"].append("oa/gpt-5.5")
        self.assertTrue(FD.open_weight_problems(doc2))

    def test_guard_rejects_missing_open_weight_flag(self) -> None:
        doc = build()
        doc["tiers"]["cheap"]["entries"][0]["open_weight"] = False
        self.assertTrue(any("open_weight" in p for p in FD.open_weight_problems(doc)))

    def test_guard_rejects_official_price_and_discount_fields(self) -> None:
        for key in ("official_price_in", "discount_pct", "officialPrice", "Discount"):
            doc = build()
            doc["tiers"]["cheap"]["entries"][0]["price_usd_per_mtok"][key] = 1
            probs = FD.open_weight_problems(doc)
            self.assertTrue(any("price-comparison" in p for p in probs), key)
            self.assertFalse(V(SCHEMA_V2).is_valid(doc), key)
        self.assertEqual(FD._walk_keys(build()), [])

    def test_check_command_fails_on_closed_family_in_tree(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            FD.write_feed(d, COMMIT)
            self.assertEqual(FD.main(["check", d]), 0)
            p = Path(d, "feed", "v2", "today.json")
            doc = json.loads(p.read_text(encoding="utf-8"))
            doc["tiers"]["cheap"]["entries"][0]["model_family"] = "Gemini 3.5 Flash"
            p.write_text(json.dumps(doc), encoding="utf-8")
            self.assertEqual(FD.main(["check", d]), 1)

    def test_write_refuses_closed_family(self) -> None:
        orig = FD.build_feed

        def closed(*a: object, **k: object) -> dict:
            doc = orig(*a, **k)  # type: ignore[arg-type]
            doc["tiers"]["cheap"]["entries"][0]["vendor"] = "OpenAI"
            return doc

        FD.build_feed = closed
        try:
            with tempfile.TemporaryDirectory() as d:
                with self.assertRaisesRegex(RuntimeError, "open-weight guard"):
                    FD.write_feed(d, COMMIT)
                self.assertFalse(os.path.exists(os.path.join(d, "feed", "v2", "today.json")))
        finally:
            FD.build_feed = orig


class SecretGuardTests(unittest.TestCase):
    # Built by concatenation so the test file itself never holds a key-shaped literal.
    PLANTED = [
        "s" + "k-" + "abcDEF123456ghiJKL",
        "Bear" + "er " + "abcdefghijklmnop",
        "Author" + "ization: x",
        "x-api" + "-key: x",
        "gh" + "p_" + "A" * 30,
        "INFER" + "HUB_API" + "_KEY=" + "abc",
        "-----BEGIN RSA PRIV" + "ATE KEY-----",
    ]

    def test_guard_flags_each_pattern(self) -> None:
        for s in self.PLANTED:
            self.assertTrue(FD.scan_bytes("x.json", json.dumps({"v": s}).encode()), s)

    def test_guard_passes_clean_feed_and_blocks_write(self) -> None:
        self.assertEqual(FD.scan_bytes("today.json", FD._dump(build())), [])
        with tempfile.TemporaryDirectory() as d:
            Path(d, "leak.txt").write_text(self.PLANTED[0], encoding="utf-8")
            self.assertEqual(len(FD.check_tree(d)), 1)
            self.assertEqual(FD.main(["check", d]), 1)

    def test_publish_refuses_without_push_when_guard_fails(self) -> None:
        orig = FD.build_feed

        def leaky(*a: object, **k: object) -> dict:
            doc = orig(*a, **k)  # type: ignore[arg-type]
            doc["notice"] += " " + self.PLANTED[1]
            return doc

        FD.build_feed = leaky
        try:
            with tempfile.TemporaryDirectory() as d:
                with self.assertRaises(RuntimeError):
                    FD.write_feed(d, COMMIT)
                self.assertFalse(os.path.exists(os.path.join(d, "feed", "v2", "today.json")))
        finally:
            FD.build_feed = orig


if __name__ == "__main__":
    unittest.main()
