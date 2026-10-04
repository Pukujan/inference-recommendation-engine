"""Daily read-only feed (IRE #75): shape, schema, provenance, staleness and the secret guard."""

from __future__ import annotations

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
SCHEMA = json.loads((IHUB / "schemas" / "feed.v1.schema.json").read_text(encoding="utf-8"))
COMMIT = "1" * 40


def build() -> dict:
    top20, frontier, sources = FD.load_sources()
    return FD.build_feed(top20, frontier, sources, COMMIT, "2026-10-04T19:00:00Z")


class FeedShapeTests(unittest.TestCase):
    def test_feed_from_committed_lists_matches_schema(self) -> None:
        doc = build()
        jsonschema.validate(doc, SCHEMA, cls=jsonschema.Draft202012Validator)
        cheap = doc["tiers"]["cheap"]["entries"]
        self.assertEqual([e["rank"] for e in cheap], list(range(1, len(cheap) + 1)))
        self.assertEqual(len(cheap), 20)
        self.assertTrue(all(e["recommended"] for e in doc["tiers"]["frontier"]["entries"]))
        for e in cheap:
            self.assertEqual(e["best_route"], e["routes"][0])
            if not e["recommended"]:
                self.assertTrue(e["gate_reasons"])

    def test_provenance_and_staleness(self) -> None:
        doc = build()
        self.assertEqual(doc["schema_version"], "ire-feed/v1")
        self.assertEqual(doc["code_commit"], COMMIT)
        self.assertEqual(set(doc["sources"]), {"top20", "frontier"})
        oldest = min(FD._parse(doc["tiers"][t]["as_of"]) for t in ("cheap", "frontier"))
        self.assertEqual(FD._parse(doc["stale_after"]), oldest + dt.timedelta(hours=36))

    def test_et_day(self) -> None:
        self.assertEqual(FD._et_day("2026-10-05T03:30:00Z"), "2026-10-04")  # EDT
        self.assertEqual(FD._et_day("2026-12-05T04:30:00Z"), "2026-12-04")  # EST
        self.assertEqual(FD._et_day("2026-12-05T05:30:00Z"), "2026-12-05")

    def test_write_feed_keeps_history_and_index(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            days = Path(d, "feed", "v1", "days")
            days.mkdir(parents=True)
            days.joinpath("2026-10-01.json").write_text("{}\n", encoding="utf-8")
            today = FD.write_feed(d, COMMIT)
            idx = json.loads(Path(d, "feed", "v1", "index.json").read_text(encoding="utf-8"))
            self.assertEqual([x["day_et"] for x in idx["days"]][-1], "2026-10-01")
            self.assertIn(today["day_et"], [x["day_et"] for x in idx["days"]])
            self.assertEqual(
                Path(d, "feed", "v1", "today.json").read_bytes(),
                days.joinpath(today["day_et"] + ".json").read_bytes(),
            )
            self.assertEqual(FD.check_tree(d), [])


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
                self.assertFalse(os.path.exists(os.path.join(d, "feed", "v1", "today.json")))
        finally:
            FD.build_feed = orig


if __name__ == "__main__":
    unittest.main()
