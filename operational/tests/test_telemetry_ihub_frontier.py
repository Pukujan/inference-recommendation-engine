"""Frontier list (IRE #67): schema, ranking order, generation gate, Claude exception, cx caveat.

Offline: builds from synthetic catalog/status/market bodies shaped like the live GET
responses, plus randomized property checks (seeded, standard library only). The committed list
in ihub/lists/ is checked against the same schema and invariants.
"""

from __future__ import annotations

import csv
import hashlib
import importlib.util
import io
import json
import random
import unittest
from pathlib import Path
from typing import Any

import jsonschema

IHUB = Path(__file__).resolve().parents[1] / "telemetry" / "gravebuster" / "pipeline" / "ihub"
_spec = importlib.util.spec_from_file_location("ihub_frontier", IHUB / "frontier.py")
assert _spec and _spec.loader
F = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(F)
SCHEMA = json.loads((IHUB / F.SCHEMA_FILE).read_text(encoding="utf-8"))
PRIOR = F.load_prior()
LISTS = IHUB / "lists"
CX_NOTE = (
    "Your system prompt is sent as a developer message, and the upstream's required instructions "
    'field is set to "You are a helpful assistant.". Native /v1/responses requests keep your '
    "instructions as sent."
)


def model(mid: str, label: str | None, off: tuple[float, float], asks: list[tuple[float, int]],
          enabled: bool = True) -> dict[str, Any]:  # fmt: skip
    ratio = off[1] / off[0]
    return {
        "id": mid,
        "upstreamModelId": mid,
        "label": label,
        "officialIn": f"{off[0]:.8f}",
        "officialOut": f"{off[1]:.8f}",
        "pricePointsIn": [[p, n] for p, n in asks],
        "pricePointsOut": [[round(p * ratio, 6), n] for p, n in asks],
        "enabled": enabled,
        "modelDisabled": False,
        "outputModality": "text",
    }


def rail(prefix: str, models: list[dict[str, Any]], note: str | None = None) -> dict[str, Any]:
    return {"slug": prefix, "prefix": prefix, "label": prefix, "status": "available",
            "enabled": True, "upstreamDisabled": False, "activeProviders": 10,
            "systemPromptNote": note, "models": models}  # fmt: skip


def status(prefixes: list[str], aliases: dict[str, tuple[str, float | None]],
           rail_state: dict[str, str] | None = None) -> dict[str, Any]:  # fmt: skip
    rs = rail_state or {}
    return {
        "families": [
            {"slug": p, "prefix": p, "state": rs.get(p, "operational"), "availability24hPct": 99.0}
            for p in prefixes
        ],  # fmt: skip
        "autoRouted": [
            {"alias": a, "state": s, "availability24hPct": v} for a, (s, v) in aliases.items()
        ],  # fmt: skip
        "performance": {"families": []},
    }


def sample() -> dict[str, Any]:
    catalog = [
        rail("cx", [
            model("gpt-6.1-sol", "GPT 6.1 Sol", (2, 10), [(0.016, 1), (0.5, 20)]),
            model("gpt-6-astra", "GPT 6 Astra", (10, 50), [(0.05, 80)]),
            model("gpt-5.6-sol", "GPT 5.6 Sol", (5, 30), [(0.025, 3)]),
            model("gpt-5.5", "GPT 5.5", (5, 30), [(0.045, 14)]),
        ], note=CX_NOTE),
        rail("cb", [
            model("gpt-6-astra", "GPT 6 Astra", (10, 50), [(0.05, 4955)]),
            model("gpt-5.6-terra", "GPT 5.6 Terra", (2, 12), [(0.01, 900)]),
            model("claude-opus-5", "Claude Opus 5", (5, 25), [(0.025, 4600)]),
            model("claude-opus-4.6", "Claude Opus 4.6", (5, 25), [(0.025, 4600)]),
        ], note="Adds a short system note ahead of your system prompt."),
        rail("cc", [
            model("claude-opus-5-5", "Claude Opus 5.5", (4, 20), [(0.4, 1)]),
            model("claude-opus-4-8", "Claude Opus 4.8", (5, 25), [(0.5, 1)]),
            model("claude-sonnet-5-5", "Claude Sonnet 5.5", (2, 10), [(0.2, 1)]),
            model("claude-sonnet-4-6", "Claude Sonnet 4.6", (3, 15), [(0.3, 1)]),
            model("claude-haiku-4-5", None, (1, 5), [(0.099, 1)]),
        ], note="A one-line Claude Code client header is added as the first system block."),
        rail("ag", [
            model("claude-opus-4-6-thinking", "Claude Opus 4.6", (5, 25), [(0.005, 4)]),
            model("gemini-pro-agent", "Gemini 3.1 Pro", (2, 12), [(0.006, 2)]),
        ]),
        rail("ocg", [model("some-unknown-model", "X", (1, 2), [(0.1, 1)])]),
    ]  # fmt: skip
    aliases = {
        "gpt-6.1-sol": ("operational", 99.9), "gpt-6-astra": ("operational", 99.7),
        "gpt-5.6-sol": ("operational", 99.0), "gpt-5.5": ("low_traffic", 99.6),
        "gpt-5.6-terra": ("operational", 97.6), "claude-opus-5": ("major_outage", 9.7),
        "claude-opus-4.6": ("operational", 99.8), "claude-opus-5.5": ("operational", 95.6),
        "claude-opus-4.8": ("low_traffic", 87.9), "claude-sonnet-5.5": ("operational", 99.0),
        "claude-sonnet-4.6": ("low_traffic", 95.4), "claude-haiku-4.5": ("low_traffic", 94.0),
        "gemini-3.1-pro": ("operational", 99.9),
    }  # fmt: skip
    st = status(["cx", "cb", "cc", "ag", "ocg"], aliases)
    market = {"models": [{"slug": "cx/gpt-6.1-sol", "lastRate": 0.02}]}
    meta = {"generated_at": "2026-10-03T23:50:00Z", "code_commit": "0" * 40,
            "snapshot_sha256": "a" * 64, "sources": []}  # fmt: skip
    return F.build(catalog, st, market, PRIOR, meta)


def check_invariants(tc: unittest.TestCase, doc: dict[str, Any]) -> None:
    """Properties every frontier document must satisfy, synthetic or live."""
    models = doc["models"]
    tc.assertEqual([m["frontier_rank"] for m in models], list(range(1, len(models) + 1)))
    elig = [m["recommendation_eligible"] for m in models]
    tc.assertEqual(elig, sorted(elig, reverse=True), "eligible rows come first")
    for group in (True, False):
        caps = [m["capability_score_100"] for m in models if m["recommendation_eligible"] is group]
        tc.assertEqual(caps, sorted(caps, reverse=True), "capability first within a group")
    routes = {r["route"]: r for r in doc["routes"]}
    for m in models:
        tc.assertEqual(m["recommendation_eligible"], not m["gate_reasons"])
        if m["recommendation_eligible"]:
            tc.assertEqual(m["best_route_health"], "healthy")
        # cheapest healthy route: nothing healthier, nothing cheaper at the same health
        best = routes[m["best_route"]]
        bk = F._route_sort_key(best)
        for rid in m["routes"]:
            tc.assertLessEqual(bk, F._route_sort_key(routes[rid]))
        # price policy is a field, consistent with the live ask
        if m["best_route_min_ask_in"] is not None:
            under = m["best_route_min_ask_in"] < m["price_policy_threshold_usd_per_mtok"]
            tc.assertEqual(m["price_policy_status"], "under_policy" if under else "over_policy")
        if m["generation_status"] == "older_gated":
            tc.assertIn("older_generation", m["gate_reasons"])
        if "older_gen_allowed_cheaper" in m["gate_exceptions"]:
            tc.assertIn(m["line"], ("claude-opus", "claude-sonnet"))
            tc.assertNotIn("older_generation", m["gate_reasons"])
    # GPT 5.x is never eligible while a GPT 6.x route is live
    gpt_majors = {r["version"].split(".")[0] for r in doc["routes"]
                  if r["vendor"] == "OpenAI" and r["health"]["status"] != "unavailable"}  # fmt: skip
    if "6" in gpt_majors:
        for m in models:
            if m["vendor"] == "OpenAI" and m["major_version"] < 6:
                tc.assertFalse(m["recommendation_eligible"], m["model_family"])
                tc.assertIn("older_generation", m["gate_reasons"])
    for r in doc["routes"]:
        if r["rail"] == "cx":
            tc.assertEqual(r["system_prompt_handling"], "developer_message")
            tc.assertEqual(r["preferred_endpoint"], "/v1/responses")
            tc.assertIn(F.CX_CAVEAT, r["caveats"])
            tc.assertNotIn("cache", r["route"].lower())
        else:
            tc.assertNotEqual(r["system_prompt_handling"], "developer_message")
            tc.assertIsNone(r["preferred_endpoint"])
            tc.assertNotIn(F.CX_CAVEAT, r["caveats"])


class FrontierBuildTests(unittest.TestCase):
    def test_schema_valid(self) -> None:
        jsonschema.Draft202012Validator.check_schema(SCHEMA)
        doc = json.loads(json.dumps(sample()))
        jsonschema.validate(doc, SCHEMA, cls=jsonschema.Draft202012Validator)

    def test_invariants(self) -> None:
        check_invariants(self, sample())

    def test_ranking_order(self) -> None:
        doc = sample()
        fams = [m["model_family"] for m in doc["models"] if m["recommendation_eligible"]]
        self.assertEqual(fams[0], "GPT 6 Astra")
        self.assertLess(fams.index("Claude Opus 5.5"), fams.index("GPT 6.1 Sol"))
        self.assertLess(fams.index("GPT 6.1 Sol"), fams.index("Claude Sonnet 5.5"))

    def test_generation_gate_gpt5(self) -> None:
        m = {x["model_family"]: x for x in sample()["models"]}
        for fam in ("GPT 5.6 Sol", "GPT 5.5", "GPT 5.6 Terra"):
            self.assertEqual(m[fam]["recommendation_status"], "gated")
            self.assertIn("older_generation", m[fam]["gate_reasons"])
            self.assertEqual(m[fam]["vendor_current_major"], 6)
        self.assertTrue(m["GPT 6.1 Sol"]["recommendation_eligible"])
        self.assertEqual(m["GPT 6.1 Sol"]["generation_status"], "current")

    def test_claude_older_gen_allowed_when_cheaper(self) -> None:
        m = {x["model_family"]: x for x in sample()["models"]}
        # Opus 4.6 best route 0.005/0.025 beats the cheapest healthy current Opus (5.5 at 0.4)
        o46 = m["Claude Opus 4.6"]
        self.assertTrue(o46["recommendation_eligible"])
        self.assertEqual(o46["gate_exceptions"], ["older_gen_allowed_cheaper"])
        self.assertEqual(o46["generation_status"], "older_allowed_cheaper")
        self.assertEqual(o46["best_route"], "ag/claude-opus-4-6-thinking")
        # Opus 4.8 at 0.5 is dearer than Opus 5.5 at 0.4 -> stays gated
        self.assertIn("older_generation", m["Claude Opus 4.8"]["gate_reasons"])
        # Sonnet 4.6 at 0.3 is dearer than Sonnet 5.5 at 0.2 -> gated
        self.assertIn("older_generation", m["Claude Sonnet 4.6"]["gate_reasons"])
        # Haiku has no exception and is below frontier capability
        self.assertIn("older_generation", m["Claude Haiku 4.5"]["gate_reasons"])
        self.assertIn("below_frontier_capability", m["Claude Haiku 4.5"]["gate_reasons"])
        # model-wide outage gates current-gen Opus 5 on health, visibly
        self.assertEqual(m["Claude Opus 5"]["gate_reasons"], ["no_healthy_route"])

    def test_cx_caveat_fields(self) -> None:
        r = {x["route"]: x for x in sample()["routes"]}
        cx = r["cx/gpt-6.1-sol"]
        self.assertEqual(cx["system_prompt_handling"], "developer_message")
        self.assertEqual(cx["preferred_endpoint"], "/v1/responses")
        self.assertEqual(cx["required_instructions_value"], "You are a helpful assistant.")
        self.assertIn(F.CX_CAVEAT, cx["caveats"])
        cb = r["cb/gpt-6-astra"]
        self.assertEqual(cb["system_prompt_handling"], "upstream_note")
        self.assertTrue(cb["is_astra_owner_route"])
        self.assertEqual(r["ag/gemini-pro-agent"]["system_prompt_handling"], "unspecified")

    def test_route_prices_and_unclassified(self) -> None:
        doc = sample()
        r = {x["route"]: x for x in doc["routes"]}["cx/gpt-6.1-sol"]
        self.assertEqual(r["price"]["min_ask_in"], 0.016)
        self.assertEqual(r["price"]["official_in"], 2.0)
        self.assertAlmostEqual(r["price"]["discount_vs_official_in_pct"], 99.2)
        self.assertEqual(r["sellers"]["sellers_at_min_ask_in"], 1)
        self.assertEqual(r["sellers"]["listings_in"], 21)
        self.assertEqual(r["price"]["last_trade_rate"], 0.02)
        self.assertEqual(doc["unclassified_text_routes"], ["ocg/some-unknown-model"])

    def test_csv_columns_match_schema(self) -> None:
        doc = sample()
        for name, fn, n in (("models", F.models_csv, len(doc["models"])),
                            ("routes", F.routes_csv, len(doc["routes"]))):  # fmt: skip
            rows = list(csv.reader(io.StringIO(fn(doc).decode("utf-8"))))
            self.assertEqual(rows[0], SCHEMA["x-csv"][name]["columns"])
            self.assertEqual(len(rows) - 1, n)
            self.assertTrue(all(len(x) == len(rows[0]) for x in rows))

    def test_version_parsing(self) -> None:
        cases = {
            "claude-opus-4-6-thinking": ("claude-opus", "4.6", "thinking"),
            "claude-opus-4.7-1m": ("claude-opus", "4.7", "1m"),
            "claude-fable-5-1": ("claude-fable", "5.1", None),
            "gpt-6.1-sol": ("gpt-sol", "6.1", None),
            "xai/grok-4.7": ("grok", "4.7", None),
            "Kimi-K3": ("kimi-k", "3", None),
        }
        for mid, (line, v, variant) in cases.items():
            c = F.classify_model(mid, None, PRIOR)
            self.assertIsNotNone(c, mid)
            assert c is not None
            self.assertEqual((c["line"], c["version"], c["variant"]), (line, v, variant), mid)
        self.assertEqual(F.classify_model("gemini-pro-agent", "Gemini 3.1 Pro", PRIOR)["version"],
                         "3.1")  # fmt: skip
        self.assertIsNone(F.classify_model("glm-5.3-flash", "GLM 5.3 Flash", PRIOR))


class FrontierPropertyTests(unittest.TestCase):
    """Seeded random catalogs: invariants hold for any prices, seller counts and states."""

    IDS = ["gpt-6-astra", "gpt-6.1-sol", "gpt-6-sol", "gpt-5.6-sol", "gpt-5.5", "gpt-5.6-terra",
           "claude-opus-5-5", "claude-opus-5", "claude-opus-4-8", "claude-opus-4-6",
           "claude-sonnet-5-5", "claude-sonnet-4-6", "claude-fable-5-1", "gemini-3.1-pro",
           "kimi-k3", "kimi-k2.6", "grok-4.7"]  # fmt: skip
    STATES = ["operational", "low_traffic", "degraded", "major_outage"]

    def random_doc(self, rng: random.Random) -> dict[str, Any]:
        cat = []
        for pre in ("cx", "cb", "cc", "ag"):
            ms = []
            for mid in rng.sample(self.IDS, rng.randint(1, len(self.IDS))):
                asks = [(round(rng.uniform(0.0005, 3.0), 5), rng.randint(0, 50))
                        for _ in range(rng.randint(1, 4))]  # fmt: skip
                ms.append(model(mid, None, (2, 10), asks, enabled=rng.random() > 0.1))
            cat.append(rail(pre, ms, note=CX_NOTE if pre == "cx" else None))
        aliases = {F.alias_key(i): (rng.choice(self.STATES), rng.choice([None, 80.0, 99.0]))
                   for i in self.IDS}  # fmt: skip
        st = status(["cx", "cb", "cc", "ag"], aliases,
                    {p: rng.choice(["operational", "degraded"]) for p in ("cx", "cb")})  # fmt: skip
        meta = {"generated_at": "x", "code_commit": None, "snapshot_sha256": None, "sources": []}
        return F.build(cat, st, {"models": []}, PRIOR, meta)

    def test_random_catalogs(self) -> None:
        rng = random.Random(67)
        for _ in range(150):
            doc = json.loads(json.dumps(self.random_doc(rng)))
            check_invariants(self, doc)
            jsonschema.validate(doc, SCHEMA, cls=jsonschema.Draft202012Validator)

    def test_cheaper_older_claude_never_gated_on_generation_alone(self) -> None:
        rng = random.Random(6701)
        for _ in range(150):
            doc = self.random_doc(rng)
            cur: dict[str, list[float]] = {}
            for m in doc["models"]:
                if (m["generation_status"] == "current" and m["best_route_health"] == "healthy"
                        and m["best_route_blended_min_ask_3to1"] is not None):  # fmt: skip
                    cur.setdefault(m["line"], []).append(m["best_route_blended_min_ask_3to1"])
            for m in doc["models"]:
                b = m["best_route_blended_min_ask_3to1"]
                if (m["line"] in ("claude-opus", "claude-sonnet") and m["major_version"] < 5
                        and cur.get(m["line"]) and b is not None and b < min(cur[m["line"]])):  # fmt: skip
                    self.assertNotIn("older_generation", m["gate_reasons"])


class CommittedListTests(unittest.TestCase):
    def setUp(self) -> None:
        self.json_path = LISTS / F.OUT_JSON
        if not self.json_path.exists():
            self.skipTest("frontier list not generated yet")

    def test_committed_list_is_valid_and_consistent(self) -> None:
        doc = json.loads(self.json_path.read_text(encoding="utf-8"))
        jsonschema.validate(doc, SCHEMA, cls=jsonschema.Draft202012Validator)
        check_invariants(self, doc)
        self.assertEqual(LISTS.joinpath(F.OUT_MODELS_CSV).read_bytes(), F.models_csv(doc))
        self.assertEqual(LISTS.joinpath(F.OUT_ROUTES_CSV).read_bytes(), F.routes_csv(doc))
        srcs = {s["endpoint"]: s for s in doc["provenance"]["sources"]}
        self.assertEqual(set(srcs), {"catalog", "status", "market"})
        self.assertTrue(all(s["method"] == "GET" for s in srcs.values()))
        self.assertRegex(doc["code_commit"] or "", r"^[0-9a-f]{40}$")
        prior_sha = hashlib.sha256((IHUB / "frontier_prior.v1.json").read_bytes()).hexdigest()
        self.assertEqual(doc["provenance"]["capability_prior"]["sha256"], prior_sha)

    def test_manifest_entry_and_top20_untouched(self) -> None:
        man = json.loads((LISTS / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual([x["list"] for x in man["lists"]], ["top20", "daily_shortlist"])
        for spec in man["lists"]:
            raw = (LISTS / spec["file"]).read_bytes()
            self.assertEqual(hashlib.sha256(raw).hexdigest(), spec["sha256"])
        gen = {x["list"]: x for x in man["generated_lists"]}["frontier"]
        for name, sha in gen["sha256"].items():
            self.assertEqual(hashlib.sha256((LISTS / name).read_bytes()).hexdigest(), sha)


if __name__ == "__main__":
    unittest.main()
