"""Tests for the reliability-adjusted route catalogue (ihub/catalogue.py, IRE #46 M4).

catalogue.py's decision logic is standard library only; jsonschema (a dev dependency) validates
the artifact against the checked-in JSON Schema. No DuckDB needed.
"""

from __future__ import annotations

import csv
import hashlib
import importlib.util
import io
import json
import unittest
from pathlib import Path

import jsonschema

IHUB = Path(__file__).resolve().parents[1] / "telemetry" / "gravebuster" / "pipeline" / "ihub"
_spec = importlib.util.spec_from_file_location("ihub_catalogue", IHUB / "catalogue.py")
assert _spec and _spec.loader
C = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(C)
SCHEMA = json.loads((IHUB / C.SCHEMA_FILE).read_text(encoding="utf-8"))
END = "2026-09-24 23:30:00"


def win(route: str, window: str, requests: int, ok: int, client: int = 0, **kw: object) -> dict:
    r = {
        "route": route,
        "rail": route.split("/", 1)[0],
        "window": window,
        "window_start": None,
        "window_end": END,
        "requests": requests,
        "ok": ok,
        "errors": requests - ok,
        "client_errors": client,
        "service_attempts": requests - client,
        "success_rate": ok / requests if requests else None,
        "service_success_rate": ok / (requests - client) if requests - client else None,
        "err_upstream_unavailable": requests - ok - client,
        "ttft_ms_p50": 1000.0,
        "ttft_ms_p95": 5000.0,
        "served_ask_in_median": 0.001,
        "platform_rail_state": "operational",
    }
    r.update(kw)
    return r


def windows(**w: tuple[int, int] | tuple[int, int, int]) -> dict:
    out = {}
    for name, v in w.items():
        key = name.replace("h1", "1h").replace("h24", "24h").replace("d7", "7d")
        out[key] = win("x/y", key, *v)
    return out


class ListTests(unittest.TestCase):
    def test_static_lists_are_verbatim_and_parsed(self) -> None:
        lists = C.load_lists()
        by = {x["list"]: x for x in lists["lists"]}
        self.assertEqual(set(by), {"top20", "daily_shortlist"})
        for lst in lists["lists"]:
            self.assertTrue(lst["sha256_verified"], lst["file"])
            raw = (IHUB / "lists" / lst["file"]).read_bytes()
            self.assertEqual(hashlib.sha256(raw).hexdigest(), lst["sha256_expected"])
        top = by["top20"]["entries"]
        self.assertEqual([e["rank"] for e in top], list(range(1, 21)))
        self.assertEqual(top[0]["model_family"], "DeepSeek V4.1 Flash")
        self.assertIn("cb/deepseek-v4.1-flash", top[0]["routes"])
        self.assertFalse(top[2]["recommendation_eligible"])  # Gemini 3.8 Flash is gated
        self.assertEqual(top[2]["gate_reasons"], ["insufficient_provider_breadth"])
        self.assertEqual(len(by["daily_shortlist"]["entries"]), 9)


class ClassifyTests(unittest.TestCase):
    def test_wilson(self) -> None:
        lo, hi = C.wilson(100, 100)
        assert lo is not None and hi is not None
        self.assertGreater(lo, 0.97)
        self.assertEqual(hi, 1.0)
        self.assertEqual(C.wilson(0, 0), (None, None))

    def test_insufficient_data(self) -> None:
        c = C.classify(windows(h24=(4, 4), d7=(9, 9)))
        self.assertEqual((c["status"], c["confidence"], c["decided_window"]),
                         ("insufficient_data", "low", "7d"))  # fmt: skip
        self.assertEqual(C.classify({})["decided_window"], None)

    def test_healthy_high_confidence(self) -> None:
        c = C.classify(windows(h1=(150, 150), h24=(600, 598)))
        self.assertEqual((c["status"], c["confidence"], c["decided_window"]),
                         ("healthy", "high", "24h"))  # fmt: skip

    def test_client_errors_do_not_count_against_the_route(self) -> None:
        c = C.classify(windows(h1=(20, 17, 3), h24=(683, 668, 15)))
        self.assertEqual(c["status"], "healthy")
        self.assertTrue(any("client" in r for r in c["reasons"]))

    def test_failing_24h(self) -> None:
        c = C.classify(windows(h24=(150, 13, 1)))
        self.assertEqual((c["status"], c["decided_window"]), ("failing", "24h"))

    def test_failing_1h_wins(self) -> None:
        c = C.classify(windows(h1=(10, 2), h24=(500, 480)))
        self.assertEqual((c["status"], c["decided_window"]), ("failing", "1h"))

    def test_recovered_last_hour_is_degraded_not_failing(self) -> None:
        c = C.classify(windows(h1=(20, 20), h24=(150, 40)))
        self.assertEqual(c["status"], "degraded")
        self.assertTrue(any("recovered" in r for r in c["reasons"]))

    def test_degraded_1h(self) -> None:
        c = C.classify(windows(h1=(20, 15), h24=(500, 490)))
        self.assertEqual((c["status"], c["decided_window"]), ("degraded", "1h"))

    def test_degraded_24h_rate(self) -> None:
        self.assertEqual(C.classify(windows(h24=(100, 90)))["status"], "degraded")

    def test_platform_outage_only_without_own_recent_evidence(self) -> None:
        self.assertEqual(C.classify(windows(h24=(100, 100)), "major_outage")["status"], "degraded")
        c = C.classify(windows(h1=(10, 10), h24=(100, 100)), "major_outage")
        self.assertEqual(c["status"], "healthy")

    def test_more_evidence_never_lowers_confidence(self) -> None:
        order = {"low": 0, "medium": 1, "high": 2}
        prev = -1
        for n in (10, 30, 100, 1000):
            c = C.classify(windows(h24=(n, n)))
            self.assertGreaterEqual(order[c["confidence"]], prev)
            prev = order[c["confidence"]]


def sample_doc() -> dict:
    rel = [
        win("cb/deepseek-v4.1-flash", "1h", 300, 300),
        win("cb/deepseek-v4.1-flash", "24h", 900, 900),
        win("cb/deepseek-v4.1-flash", "7d", 900, 900),
        win("zai/glm-5.3-flash", "24h", 151, 13, 1, platform_rail_state="major_outage"),
        win("zai/glm-5.3-flash", "7d", 151, 13, 1),
        # 15 HTTP 400s on cb = upstream rejects (11133), counted as failures since v1.1
        win(
            "cb/gpt-6-astra",
            "24h",
            683,
            668,
            0,
            err_upstream_reject=15,
            err_upstream_reject_presumed=12,
            upstream_errors=15,
        ),
    ]
    prices = [
        {"route": "cb/deepseek-v4.1-flash", "rail": "cb", "ts": END, "min_ask_in": 0.00015,
         "min_ask_out": 0.0006, "min_tier_avail_in": 3, "cw_median_ask_in": 0.0015,
         "cw_median_ask_out": 0.006, "official_in": 0.3, "official_out": 1.2,
         "avail_total_in": 900, "enabled": True, "book_hash": "abc"},
        {"route": "zai/glm-5.3-flash", "rail": "zai", "ts": END, "min_ask_in": 0.00015,
         "min_ask_out": 0.0005, "enabled": True, "book_hash": "def"},
        {"route": "cbcn/glm-5.3-flash", "rail": "cbcn", "ts": END, "min_ask_in": 0.0003,
         "min_ask_out": 0.001, "cw_median_ask_in": 0.0004, "enabled": True, "book_hash": "ghi"},
        {"route": "cb/gpt-6-astra", "rail": "cb", "ts": END, "min_ask_in": 0.1,
         "min_ask_out": 0.5, "enabled": True, "book_hash": "jkl"},
    ]  # fmt: skip
    dims = [{"route": p["route"], "rail": p["rail"], "model_label": None} for p in prices]
    status = {"zai": {"rail": "zai", "state": "major_outage", "availability_24h_pct": 92.9}}
    meta = {
        "generated_at": "2026-09-24T23:45:00Z",
        "code_commit": "0" * 40,
        "request_log_coverage_end": "2026-09-24T23:30:00Z",
        "modeled_snapshot": {"dir": "snap-x", "run_id": "x", "built_at": "2026-09-24T23:44:00Z"},
        "latest_catalog_fetch": {
            "fetched_at": "2026-09-24T23:40:00Z",
            "sha256": "f" * 64,
            "raw_path": None,
            "dup_of_previous": False,
        },  # fmt: skip
        "inputs_sha256": "e" * 64,
    }
    return C.build(rel, prices, dims, status, C.load_lists(), meta)


class BuildTests(unittest.TestCase):
    def test_artifact_validates_against_schema(self) -> None:
        jsonschema.Draft202012Validator.check_schema(SCHEMA)
        doc = sample_doc()
        # round trip through JSON like the written artifact
        doc = json.loads(json.dumps(doc, default=str))
        jsonschema.validate(doc, SCHEMA, cls=jsonschema.Draft202012Validator)

    def test_statuses_and_best_route(self) -> None:
        doc = sample_doc()
        r = {x["route"]: x for x in doc["routes"]}
        self.assertEqual(r["cb/deepseek-v4.1-flash"]["status"], "healthy")
        self.assertEqual(r["zai/glm-5.3-flash"]["status"], "failing")
        astra = r["cb/gpt-6-astra"]
        self.assertEqual(astra["status"], "healthy")  # 668/683 still clears the thresholds
        self.assertEqual(astra["evidence"]["decided_service_attempts"], 683)
        self.assertTrue(any("upstream 400 rejects" in x for x in astra["status_reasons"]))
        self.assertIsNone(r["cb/gpt-6-astra"]["recommendation_eligible_static"])  # not listed
        self.assertFalse(r["ag/gemini-3.8-flash-high"]["in_live_catalog"])
        self.assertEqual(r["zai/glm-5.3-flash"]["evidence"]["platform_rail_state"], "major_outage")
        m = {x["model_family"]: x for x in doc["models"]}
        glm = m["GLM 5.3 Flash"]
        # the failing route is never preferred over an untested, listed, enabled alternative
        self.assertEqual(glm["best_route"], "cbcn/glm-5.3-flash")
        self.assertEqual(glm["best_route_status"], "insufficient_data")
        self.assertEqual(doc["models"][0]["model_family"], "DeepSeek V4.1 Flash")
        self.assertEqual(sorted(x["reliability_adjusted_rank"] for x in doc["models"]),
                         list(range(1, len(doc["models"]) + 1)))  # fmt: skip
        self.assertEqual(sum(doc["status_counts"].values()), len(doc["routes"]))

    def test_static_list_fields_are_copied_unchanged(self) -> None:
        doc = sample_doc()
        lists = {x["list"]: x for x in C.load_lists()["lists"]}
        for r in doc["routes"]:
            for mem in r["lists"]:
                e = next(x for x in lists[mem["list"]]["entries"] if x["rank"] == mem["rank"])
                self.assertEqual(mem["recommendation_eligible"], e["recommendation_eligible"])
                self.assertIn(r["route"], e["routes"])

    def test_csv_matches_schema_columns(self) -> None:
        doc = sample_doc()
        rows = list(csv.reader(io.StringIO(C.to_csv(doc).decode("utf-8"))))
        self.assertEqual(rows[0], SCHEMA["x-csv"]["columns"])
        self.assertEqual(len(rows) - 1, len(doc["routes"]))
        self.assertTrue(all(len(x) == len(rows[0]) for x in rows))


if __name__ == "__main__":
    unittest.main()
