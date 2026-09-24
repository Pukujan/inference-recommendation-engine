"""Unit tests for the GET-only market collector (ihub)'s pure transforms (IRE #46 M2/M3).

Only ``ihub/transform.py`` (standard library) is imported, so CI needs no DuckDB/zstandard.
"""

from __future__ import annotations

import csv
import importlib.util
import io
import json
import unittest
from pathlib import Path

PIPE = Path(__file__).resolve().parents[1] / "telemetry" / "gravebuster" / "pipeline"
_spec = importlib.util.spec_from_file_location("ihub_transform", PIPE / "ihub" / "transform.py")
assert _spec and _spec.loader
T = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(T)

TS = "2026-09-24T22:50:00.000Z"
CATALOG = [
    {
        "prefix": "cb",
        "slug": "codebuddy",
        "label": "CodeBuddy",
        "status": "available",
        "enabled": True,
        "upstreamDisabled": False,
        "activeProviders": 14930,
        "models": [
            {
                "upstreamModelId": "gpt-6-astra",
                "label": "GPT 6 Astra",
                "officialIn": "10.00000000",
                "officialOut": "50.00000000",
                "pricePointsIn": [[0.11, 299], [0.1, 1], [0.12, 3296]],
                "pricePointsOut": [[0.5, 1], [0.55, 299], [0.6, 3296]],
                "enabled": True,
                "modelDisabled": False,
                "supportsCache": True,
                "contextWindow": 272000,
                "maxOutputTokens": 128000,
                "reasoningLevels": ["low", "medium", "high"],
                "modalities": ["text", "image"],
                "outputModality": "text",
            },
            {
                "upstreamModelId": "legacy",
                "label": "Legacy",
                "officialIn": "1",
                "officialOut": "2",
                "asksIn": [0.3, 0.2, 0.2],
                "asksOut": [],
            },
        ],
    }
]


class CatalogTests(unittest.TestCase):
    def test_tiers_sorted_with_avail_counts(self) -> None:
        rails, routes, tiers = T.catalog_rows(CATALOG, TS)
        self.assertEqual(rails[0]["active_providers"], 14930)
        astra = [t for t in tiers if t["route"] == "cb/gpt-6-astra" and t["side"] == "in"]
        self.assertEqual(
            [(t["tier_price"], t["avail_count"]) for t in astra],
            [(0.1, 1), (0.11, 299), (0.12, 3296)],
        )
        self.assertEqual([t["tier_rank"] for t in astra], [1, 2, 3])
        self.assertEqual(astra[-1]["cum_avail"], 3596)
        self.assertEqual(astra[0]["official_price"], 10.0)
        self.assertAlmostEqual(astra[0]["discount_pct"], 99.0)
        r = {x["route"]: x for x in routes}["cb/gpt-6-astra"]
        self.assertEqual((r["min_ask_in"], r["min_tier_avail_in"], r["min_ask_out"]), (0.1, 1, 0.5))
        self.assertEqual(r["tiers_in"], 3)

    def test_documented_asks_shape_is_grouped(self) -> None:
        _, _, tiers = T.catalog_rows(CATALOG, TS)
        legacy = [(t["tier_price"], t["avail_count"]) for t in tiers if t["route"] == "cb/legacy"]
        self.assertEqual(legacy, [(0.2, 2), (0.3, 1)])

    def test_book_hash_change_detection(self) -> None:
        _, routes, _ = T.catalog_rows(CATALOG, TS)
        last = {r["route"]: r["book_hash"] for r in routes}
        self.assertEqual(T.changed_routes(routes, last), set())
        cat2 = json.loads(json.dumps(CATALOG))
        cat2[0]["models"][0]["pricePointsIn"][0][1] = 2  # one more seller at $0.11
        _, routes2, _ = T.catalog_rows(cat2, TS)
        self.assertEqual(T.changed_routes(routes2, last), {"cb/gpt-6-astra"})


class LogTests(unittest.TestCase):
    ROW = {
        "id": "3aeb7207-66dd-45ab-82f3-d4a24fdfe484",
        "ts": "2026-09-24T22:50:15.218Z",
        "status": "ok",
        "http_status": 200,
        "prompt_tokens": 11523,
        "completion_tokens": 91,
        "cached_tokens": None,
        "cache_write_tokens": None,
        "cost_consumer_usdc": "0.000018",
        "ask_input_per_mtok": "0.0015",
        "ask_output_per_mtok": "0.006",
        "region": "",
        "model": "cb/deepseek-v4.1-flash",
        "upstream_label": "CodeBuddy",
        "ttft_ms": 1906,
        "duration_ms": 2293,
        "routing_ms": 447,
    }

    def test_log_row_exact_decimals_and_extras(self) -> None:
        r = T.log_row(dict(self.ROW, seller_id="s-1"), TS)
        self.assertEqual(r["cost_consumer_usdc"], "0.000018")
        self.assertEqual(r["ask_input_per_mtok"], "0.00150000")
        self.assertEqual(r["day"], "2026-09-24")
        self.assertEqual(json.loads(r["extras_json"]), {"seller_id": "s-1"})
        self.assertIsNone(T.log_row(self.ROW, TS)["extras_json"])

    def test_content_hash_is_stable_and_sensitive(self) -> None:
        a = T.log_row(self.ROW, TS)["content_hash"]
        self.assertEqual(a, T.log_row(dict(self.ROW), "2026-09-25T00:00:00Z")["content_hash"])
        self.assertNotEqual(a, T.log_row(dict(self.ROW, status="error"), TS)["content_hash"])

    def test_page_stop_and_range(self) -> None:
        since = T.parse_ts("2026-09-24T22:00:00Z")
        self.assertFalse(T.page_stop([self.ROW], since))
        self.assertTrue(T.page_stop([self.ROW, dict(self.ROW, ts="2026-09-24T21:59:59Z")], since))
        now = T.parse_ts("2026-09-25T02:00:00Z")
        self.assertEqual(T.pick_range(since, now), "24h")
        self.assertEqual(T.pick_range(T.parse_ts("2026-09-20T00:00:00Z"), now), "7d")


class SnapshotCsvTests(unittest.TestCase):
    def test_pricing_csv_matches_sep22_format(self) -> None:
        models = {
            "data": [
                {"id": "cb/gpt-6-astra", "owned_by": "cb"},
                {"id": "gpt-6-astra", "owned_by": "alias"},
            ]
        }
        rows = T.pricing_csv_rows(models, CATALOG)
        self.assertEqual(len(T.PRICING_CSV_COLUMNS), 22)
        buf = io.StringIO()
        csv.writer(buf).writerows([list(T.PRICING_CSV_COLUMNS), *rows])
        parsed = list(csv.DictReader(io.StringIO(buf.getvalue())))
        astra = parsed[0]
        self.assertEqual(astra["model_id"], "cb/gpt-6-astra")
        self.assertEqual(astra["official_input_usdc_per_1m"], "10")
        self.assertEqual(astra["minimum_input_ask_usdc_per_1m"], "0.1")
        self.assertEqual(json.loads(astra["input_price_points_json"])[0], [0.1, 1])
        self.assertEqual(json.loads(astra["input_discount_points_json"])[0], [0.1, 1, 99])
        self.assertEqual(astra["maximum_input_available_count"], "3296")
        self.assertEqual(astra["supports_cache"], "True")
        self.assertEqual(astra["reasoning_levels"], "low|medium|high")
        alias = parsed[1]
        self.assertEqual((alias["provider"], alias["input_price_points_json"]), ("alias", "[]"))
        self.assertEqual(parsed[2]["model_id"], "cb/legacy")  # catalog-only routes are appended

    def test_providers_csv(self) -> None:
        self.assertEqual(
            T.providers_csv_rows(CATALOG)[0],
            ["codebuddy", "cb", "CodeBuddy", "available", "True", "14930", "2"],
        )


class StatusBalanceTests(unittest.TestCase):
    def test_status_and_balance(self) -> None:
        st = {
            "updatedAt": TS,
            "overall": {
                "state": "operational",
                "currentAvailabilityPct": 99.1,
                "history": [{"start": TS, "availabilityPct": 98, "state": "operational"}],
            },
            "families": [
                {
                    "prefix": "zai",
                    "slug": "z-ai",
                    "family": "Z.ai",
                    "state": "major_outage",
                    "currentAvailabilityPct": 72,
                }
            ],
        }
        cur, hist = T.status_rows(st, TS)
        self.assertEqual([c["rail"] for c in cur], ["*", "zai"])
        self.assertEqual(cur[1]["state"], "major_outage")
        self.assertEqual(len(hist), 1)
        b = T.balance_row(
            {
                "balance": {"amount_usdc": "2.702399"},
                "window": {"spend_usdc": "1.3", "requests": 5},
            },
            TS,
        )
        self.assertEqual((b["balance_usdc"], b["window_spend_usdc"]), ("2.702399", "1.300000"))


if __name__ == "__main__":
    unittest.main()
