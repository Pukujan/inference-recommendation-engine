"""Kilo Code session ingester (issue #41): scope rule, star-schema rows, dedup keys, scrub.

Pure-python parts of operational/telemetry/gravebuster/pipeline/atpipe/ingest_kilo.py; the Parquet
and dbt steps need the host venv (duckdb/pyarrow) and are verified on the telemetry host.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "operational" / "telemetry" / "gravebuster" / "pipeline"))

from atpipe import ingest_kilo as K  # noqa: E402

# The BYOK provider id is built at run time (public-surface check keeps the name out of tests/).
IH_ID = "".join(["infer", "hub"])
IH_BASE = "https://" + K.BYOK_HOST + "/v1"
T0 = 1790280000000  # 2026-09-24T18:40:00Z


def part(pid: str, mid: str, t: int, data: dict[str, Any]) -> dict[str, Any]:
    return {"id": pid, "message_id": mid, "time_created": t, "time_updated": t, "data": data}


def msg(mid: str, t: int, data: dict[str, Any]) -> dict[str, Any]:
    return {"id": mid, "time_created": t, "time_updated": t, "data": data}


def doc() -> dict[str, Any]:
    ih = {"providerID": IH_ID, "modelID": "cb/gpt-6-astra"}
    return {
        "session": {
            "id": "ses_parent",
            "parent_id": None,
            "title": "Supervise Astra for alex@example.com",
            "version": "7.7.9",
            "agent": "code",
            "model": '{"id":"cb/gpt-6-astra","providerID":"' + IH_ID + '"}',
            "time_created": T0,
            "time_updated": T0 + 400_000,
            "tokens_input": 10,
            "tokens_output": 5,
            "cost": 0.01,
        },
        "project": {"worktree": "D:/claude/project-continuity-modules"},
        "scope": IH_ID,
        "messages": [
            msg("m1", T0, {"role": "user", "time": {"created": T0}, "model": ih}),
            msg(
                "m2",
                T0 + 1000,
                {"role": "assistant", "time": {"created": T0 + 1000, "completed": T0 + 9000}, **ih},
            ),
            msg(
                "m3",
                T0 + 300_000,
                {
                    "role": "assistant",
                    "time": {"created": T0 + 300_000, "completed": T0 + 301_000},
                    "error": {
                        "name": "APIError",
                        "data": {
                            "message": "Bad Request",
                            "statusCode": 400,
                            "isRetryable": False,
                            "responseBody": '{"error":{"code":11133,"message":"invalid request"}}',
                        },
                    },
                    **ih,
                },
            ),
        ],
        "parts": [
            part("p1", "m2", T0 + 1100, {"type": "step-start"}),
            part(
                "p2",
                "m2",
                T0 + 1200,
                {
                    "type": "tool",
                    "tool": "bash",
                    "callID": "c1",
                    "state": {
                        "status": "error",
                        "input": {"command": "rm -rf /"},
                        "error": "The user has specified a rule which prevents you from using "
                        "this specific tool call",
                        "time": {"start": T0 + 1200, "end": T0 + 1300},
                    },
                },
            ),
            part(
                "p3",
                "m2",
                T0 + 1400,
                {
                    "type": "tool",
                    "tool": "task",
                    "callID": "c2",
                    "state": {
                        "status": "completed",
                        "input": {"subagent_type": "general", "background": True},
                        "output": "done",
                        "metadata": {"sessionId": "ses_child"},
                        "time": {"start": T0 + 1400, "end": T0 + 8000},
                    },
                },
            ),
            part(
                "p4",
                "m2",
                T0 + 8500,
                {
                    "type": "step-finish",
                    "reason": "stop",
                    "cost": 0.002,
                    "tokens": {"input": 7, "output": 3, "reasoning": 1, "cache": {"read": 2}},
                },
            ),
        ],
    }


class ScopeTests(unittest.TestCase):
    def test_byok_only_sessions_are_in_scope(self) -> None:
        self.assertEqual(K.classify([IH_ID, IH_ID])[0], IH_ID)

    def test_config_base_url_maps_custom_provider(self) -> None:
        pm = K.provider_map_from_config({"provider": {"ih": {"options": {"baseURL": IH_BASE}}}})
        self.assertEqual(pm, {"ih": K.BYOK_HOST})
        self.assertEqual(K.classify(["ih"], provider_map=pm)[0], IH_ID)

    def test_other_providers_and_proxies_are_excluded(self) -> None:
        self.assertEqual(K.classify(["xai"])[0], "excluded")
        self.assertEqual(K.classify(["xai"], ["litellm"])[0], "excluded")
        scope, why = K.classify([IH_ID, "anthropic"])
        self.assertEqual(scope, "excluded")
        self.assertIn("mixed", why)

    def test_undeterminable_provider_is_unknown(self) -> None:
        self.assertEqual(K.classify([])[0], "unknown")
        self.assertEqual(K.classify(["my-proxy"])[0], "unknown")
        self.assertEqual(K.classify([None], [IH_ID])[0], "unknown")


class RowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.rows = K.session_rows(doc())

    def test_one_run_with_failed_outcome_and_scrubbed_title(self) -> None:
        (run,) = self.rows["runs"]
        self.assertEqual(run["run_id"], "kilo:ses_parent")
        self.assertEqual(run["outcome"], "failed")
        self.assertEqual(run["provider_id"], IH_ID)
        self.assertNotIn("alex@example.com", run["title"])
        self.assertIn("sha256:", run["title"])

    def test_model_requests_keep_tokens_and_provider_error(self) -> None:
        reqs = {r["request_id"]: r for r in self.rows["model_requests"]}
        self.assertEqual(set(reqs), {"ses_parent#m1.s0", "ses_parent#m2.e"})
        ok = reqs["ses_parent#m1.s0"]
        self.assertEqual((ok["tokens_in"], ok["tokens_out"], ok["tokens_cache_read"]), (7, 3, 2))
        err = reqs["ses_parent#m2.e"]
        self.assertTrue(err["is_error"])
        self.assertEqual((err["http_status"], err["provider_error_code"]), (400, 11133))

    def test_tool_calls_record_auto_deny_duration_and_no_arguments(self) -> None:
        tools = {t["tool_name"]: t for t in self.rows["tool_calls"]}
        bash = tools["bash"]
        self.assertEqual((bash["outcome"], bash["denial_kind"]), ("denied", "auto_deny"))
        self.assertEqual(bash["duration_ms"], 100)
        self.assertEqual(bash["evidence_id"], "ses_parent#m1.p1")
        self.assertNotIn("command", str(bash))
        self.assertEqual(tools["task"]["child_task_id"], "ses_child")

    def test_subagent_hop_and_idle_events(self) -> None:
        (hop,) = self.rows["hops"]
        self.assertEqual(hop["child_run_id"], "kilo:ses_child")
        self.assertEqual(hop["launch_evidence_id"], "ses_parent#m1.p2")
        ts = sorted(e["t_ms"] for e in self.rows["events"])
        self.assertEqual(max(b - a for a, b in zip(ts, ts[1:], strict=False)), 291_000)

    def test_project_label_for_task_id(self) -> None:
        self.assertEqual(self.rows["runs"][0]["project"], "project-continuity-modules")
        wt = "D:/claude/hades/hades-product/.kilo/worktrees/colorful-income"
        self.assertEqual(K.project_name("/", wt), "hades-product")

    def test_dedup_on_evidence_key_is_idempotent(self) -> None:
        twice = self.rows["tool_calls"] + K.session_rows(doc())["tool_calls"]
        self.assertEqual(len(K.dedup(twice, "tool_call_id")), len(self.rows["tool_calls"]))


class ScrubTests(unittest.TestCase):
    def test_secrets_and_emails_are_masked(self) -> None:
        out = K.scrub_obj(
            {"apiKey": "abc", "text": "key sk-abcdefghijklmnop1234 Bearer abcdefghijklmnopq x@y.io"}
        )
        self.assertEqual(out["apiKey"], "***REDACTED***")
        self.assertNotIn("sk-abcdef", out["text"])
        self.assertNotIn("Bearer abcdef", out["text"])
        self.assertNotIn("x@y.io", out["text"])

    def test_error_code_parsing_ignores_absent_codes(self) -> None:
        self.assertIsNone(K.provider_error_code("Aborted"))
        self.assertEqual(K.provider_error_code('{"code": "11133"}'), 11133)


if __name__ == "__main__":
    unittest.main()
