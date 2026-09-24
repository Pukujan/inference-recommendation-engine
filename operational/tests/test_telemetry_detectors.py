"""P4 incident detectors, signature catalog and experiment manifests (issue #41 P4, #40 detectors).

Pure-function tests over synthetic fact rows: no DuckDB, no host paths, no private receipts.
"""

from __future__ import annotations

import copy
import importlib.util
import json
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PIPE = REPO_ROOT / "operational" / "telemetry" / "gravebuster" / "pipeline"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(f"atpipe_{name}", PIPE / "atpipe" / f"{name}.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


detectors = _load("detectors")
experiments = _load("experiments")
cmdclass = _load("cmdclass")
CATALOG = detectors.load_catalog(str(PIPE / "detectors" / "catalog.json"))


def run(run_id, **kw):
    base = {
        "run_id": run_id,
        "role": "launcher",
        "parent_run_id": None,
        "root_run_id": None,
        "harness": "astra-launcher",
        "task_id": "pcm-owner",
        "route_id": "cb/model-a",
        "wall_s": 300.0,
        "outcome": "completed",
        "exit_code": 0,
        "killed": False,
        "timed_out": False,
        "kill_signal": None,
        "provider_error_code": None,
        "n_tool_calls": 5,
        "receipt_stamp": None,
        "root_trace_id": "t-" + run_id,
        "trace_ids": ["t-" + run_id],
        "ended_at_utc": "2026-09-24T20:00:00",
        "stderr_tail_text": None,
    }
    base.update(kw)
    return base


def receipt(stamp, **kw):
    base = {
        "stamp": stamp,
        "run_id": f"astra-{stamp}",
        "outcome": "completed",
        "killed": False,
        "exit_code_raw": "0",
        "session_id": None,
        "prior_session_id": None,
        "action": None,
        "n_commands": 3,
        "n_commands_started": 3,
        "has_launcher_error": False,
        "provider_error_code": None,
        "stderr_first_line": None,
        "duration_s": 120.0,
        "ended_at_utc": "2026-09-24T20:00:00",
        "has_summary": True,
    }
    base.update(kw)
    return base


def rej_event(stamp, line_no, request_id, shape="s1"):
    return {
        "stamp": stamp,
        "seq": line_no - 1,
        "line_no": line_no,
        "type": "turn.failed",
        "provider_error_code": 11133,
        "http_status": 400,
        "error_type": "model_param_invalid",
        "request_id": request_id,
        "msgs": 40,
        "tools": 12,
        "empty_content": 7,
        "additional_properties": 16,
        "max_tool_desc_bytes": 16000,
        "request_shape_hash": shape,
    }


def detect(inputs):
    return detectors.detect_all(
        inputs, CATALOG, snapshot_id="snap-test", detected_at="2026-09-24T23:00:00"
    )


def by_type(rows):
    out: dict[str, list[dict]] = {}
    for r in rows:
        out.setdefault(r["incident_type"], []).append(r)
    return out


class CatalogTests(unittest.TestCase):
    def test_catalog_is_versioned_and_matches_detectors(self) -> None:
        self.assertEqual(CATALOG["catalog_version"], "ire-incident-signatures/v1")
        self.assertEqual(detectors.validate_catalog(CATALOG), [])
        self.assertEqual(
            {s["incident_type"] for s in CATALOG["signatures"]}, set(detectors.DETECTORS)
        )
        self.assertEqual(len(CATALOG["_sha256"]), 64)

    def test_every_fix_starts_proposed(self) -> None:
        for s in CATALOG["signatures"]:
            self.assertEqual(s["proposed_fix"]["status"], "proposed", s["incident_type"])
            self.assertTrue(s["detector_version"].startswith(s["incident_type"] + "/"))

    def test_invalid_catalog_is_rejected(self) -> None:
        bad = copy.deepcopy(CATALOG)
        bad["signatures"][0]["proposed_fix"]["status"] = "done"
        bad["signatures"].pop()
        with self.assertRaises(ValueError):
            detectors.detect_all({}, bad)

    def test_empty_inputs_give_no_incidents(self) -> None:
        self.assertEqual(detect({}), [])


class ProviderRejectionTests(unittest.TestCase):
    def inputs(self):
        return {
            "runs": [
                run(
                    "astra-A",
                    outcome="failed",
                    exit_code=1,
                    provider_error_code=11133,
                    receipt_stamp="A",
                ),
                run(
                    "astra-A/child",
                    role="agent_child",
                    parent_run_id="astra-A",
                    root_run_id="astra-A",
                    harness="codex",
                ),
                run("astra-B", outcome="failed", exit_code=1, receipt_stamp="B"),
            ],
            "model_requests": [
                {
                    "run_id": "astra-A/child",
                    "http_status": 400,
                    "provider_error_code": None,
                    "trace_id": "tA",
                    "span_id": "s1",
                },
                {
                    "run_id": "astra-B",
                    "http_status": 429,
                    "provider_error_code": 1302,
                    "trace_id": "tB",
                    "span_id": "s9",
                },
            ],
            "receipt_runs": [
                receipt("A", outcome="failed", provider_error_code=11133),
                receipt("B", outcome="failed"),
            ],
            "receipt_events": [rej_event("A", 54, "req-a")],
            "root_errors": [
                {
                    "run_id": "astra-B",
                    "trace_id": "tB",
                    "span_id": "root",
                    "code": 11133,
                    "http_status": 400,
                    "type": "model_param_invalid",
                    "request_ids": ["req-b"],
                    "request_shape": "msgs=40",
                },
            ],
        }

    def test_one_incident_per_root_run_with_merged_evidence(self) -> None:
        rows = by_type(detect(self.inputs()))["provider_param_rejection"]
        self.assertEqual(sorted(r["run_id"] for r in rows), ["astra-A", "astra-B"])
        a = next(r for r in rows if r["run_id"] == "astra-A")
        self.assertIn("receipt:A/codex-events.jsonl:L54", a["evidence_refs"])
        self.assertIn("span:tA/s1", a["evidence_refs"])
        m = json.loads(a["metrics_json"])
        self.assertEqual(m["provider_request_ids"], ["req-a"])
        self.assertEqual(m["request_shape"]["additional_properties"], 16)
        b = next(r for r in rows if r["run_id"] == "astra-B")
        self.assertIn("span:tB/root", b["evidence_refs"])
        self.assertEqual(json.loads(b["metrics_json"])["provider_request_ids"], ["req-b"])

    def test_rate_limit_is_not_a_param_rejection(self) -> None:
        rows = detect(self.inputs())
        self.assertFalse(any("1302" in r["summary"] for r in rows))

    def test_recurring_failure_shares_fingerprint_but_not_id(self) -> None:
        rows = by_type(detect(self.inputs()))["provider_param_rejection"]
        self.assertEqual(len({r["fingerprint"] for r in rows}), 1)
        self.assertEqual(len({r["incident_id"] for r in rows}), 2)
        self.assertEqual(rows[0]["fingerprint_v"], "fp/v1")
        self.assertEqual(rows[0]["proposed_fix_status"], "proposed")
        self.assertIsNone(rows[0]["justified"])

    def test_ids_are_deterministic(self) -> None:
        a = detect(self.inputs())
        b = detectors.detect_all(
            self.inputs(), CATALOG, snapshot_id="other", detected_at="2030-01-01T00:00:00"
        )
        self.assertEqual([r["incident_id"] for r in a], [r["incident_id"] for r in b])
        self.assertEqual([r["fingerprint"] for r in a], [r["fingerprint"] for r in b])


class KillAndLauncherTests(unittest.TestCase):
    def test_kill_while_live_vs_kill_after_idle(self) -> None:
        inputs = {
            "runs": [
                run(
                    "live",
                    killed=True,
                    outcome="killed",
                    kill_signal="terminated (-1)",
                    exit_code=-1,
                ),
                run("idle", killed=True, outcome="killed", exit_code=-1),
            ],
            "idle_gaps": [
                {
                    "run_id": "live",
                    "gap_id": "g1",
                    "gap_s": 4.0,
                    "ended_by": "run_end",
                    "before_trace_id": "t",
                    "before_span_id": "s",
                },
                {"run_id": "idle", "gap_id": "g2", "gap_s": 400.0, "ended_by": "run_end"},
            ],
        }
        rows = by_type(detect(inputs))
        self.assertEqual([r["run_id"] for r in rows["unjustified_kill"]], ["live"])
        self.assertIn("gap:g1", rows["unjustified_kill"][0]["evidence_refs"])
        self.assertNotIn("long_idle_gap", rows)  # a gap ended by the run end is not a stall

    def test_receipt_kill_with_command_in_flight(self) -> None:
        inputs = {
            "runs": [
                run(
                    "astra-K",
                    killed=True,
                    outcome="killed",
                    kill_signal="interrupted",
                    receipt_stamp="K",
                )
            ],
            "receipt_runs": [
                receipt(
                    "K",
                    outcome="killed",
                    killed=True,
                    exit_code_raw="interrupted",
                    n_commands=5,
                    n_commands_started=6,
                )
            ],
            "receipt_events": [
                {"stamp": "K", "seq": 0, "line_no": 1, "type": "item.started", "item_id": "i1"},
                {"stamp": "K", "seq": 1, "line_no": 2, "type": "item.completed", "item_id": "i1"},
                {"stamp": "K", "seq": 2, "line_no": 3, "type": "item.started", "item_id": "i2"},
            ],
        }
        rows = by_type(detect(inputs))["unjustified_kill"]
        self.assertEqual(len(rows), 1)
        self.assertIn("receipt:K/codex-events.jsonl:L3", rows[0]["evidence_refs"])

    def test_launcher_error_early_exit_and_unattributed_failure(self) -> None:
        inputs = {
            "runs": [
                run(
                    "astra-E",
                    outcome="launcher_error",
                    exit_code=None,
                    receipt_stamp="E",
                    n_tool_calls=0,
                ),
                run(
                    "astra-F",
                    outcome="failed",
                    exit_code=1,
                    wall_s=7.0,
                    n_tool_calls=0,
                    stderr_tail_text='{"id":"m","object":"model","owned_by":"alias"}',
                ),
                run("astra-G", outcome="failed", exit_code=1, wall_s=250.0),
                run(
                    "astra-H",
                    outcome="failed",
                    exit_code=1,
                    wall_s=250.0,
                    provider_error_code=11133,
                ),
            ],
            "receipt_runs": [
                receipt(
                    "E",
                    outcome="launcher_error",
                    has_launcher_error=True,
                    exit_code_raw=None,
                    stderr_first_line="powershell : ERROR failed to refresh available models",
                )
            ],
        }
        rows = by_type(detect(inputs))
        self.assertEqual(
            sorted(r["run_id"] for r in rows["launcher_error_or_early_exit"]),
            ["astra-E", "astra-F"],
        )
        e = next(r for r in rows["launcher_error_or_early_exit"] if r["run_id"] == "astra-E")
        self.assertIn("receipt:E/launcher-error.txt", e["evidence_refs"])
        self.assertEqual(json.loads(e["metrics_json"])["stderr_class"], "models_refresh_warning")
        self.assertEqual([r["run_id"] for r in rows["unattributed_failure"]], ["astra-G"])

    def test_fast_provider_rejection_is_not_also_an_early_exit(self) -> None:
        # Launcher root span carries provider.error.* (fact_runs has no code): one incident, not two.
        inputs = {
            "runs": [run("astra-R", outcome="failed", exit_code=1, wall_s=6.0, n_tool_calls=0)],
            "root_errors": [
                {
                    "run_id": "astra-R",
                    "trace_id": "tR",
                    "span_id": "root",
                    "code": 11133,
                    "http_status": 400,
                    "type": "model_param_invalid",
                    "request_ids": ["req-r"],
                }
            ],
        }
        rows = by_type(detect(inputs))
        self.assertEqual(sorted(rows), ["provider_param_rejection"])

    def test_terminated_without_closeout(self) -> None:
        inputs = {
            "runs": [
                run("astra-T", outcome="no_terminal_event", exit_code=None, receipt_stamp="T")
            ],
            "receipt_runs": [
                receipt(
                    "T",
                    outcome="no_terminal_event",
                    exit_code_raw=None,
                    killed=None,
                    has_summary=False,
                )
            ],
            "receipt_events": [
                {"stamp": "T", "seq": 0, "line_no": 1, "type": "thread.started"},
                {"stamp": "T", "seq": 1, "line_no": 2, "type": "item.completed"},
            ],
        }
        rows = by_type(detect(inputs))["terminated_without_closeout"]
        self.assertEqual(
            rows[0]["evidence_refs"], ["receipt:T/codex-events.jsonl:L2", "run:astra-T"]
        )


class SessionChainTests(unittest.TestCase):
    def test_blind_resend_and_resume_churn(self) -> None:
        stamps = ["20260924T190000Z", "20260924T191000Z", "20260924T192000Z", "20260924T193000Z"]
        rr = [
            receipt(stamps[0], session_id="s1", outcome="failed", provider_error_code=11133),
            receipt(
                stamps[1],
                session_id="s2",
                prior_session_id="s1",
                outcome="failed",
                provider_error_code=11133,
                action="codex-exec-fresh-launch",
            ),
            receipt(
                stamps[2],
                session_id="s2",
                outcome="killed",
                killed=True,
                action="codex-exec-resume",
            ),
            receipt(stamps[3], session_id="s9", outcome="completed"),
        ]
        inputs = {
            "runs": [run(f"astra-{s}", receipt_stamp=s) for s in stamps],
            "receipt_runs": rr,
            "receipt_events": [rej_event(stamps[0], 10, "r0"), rej_event(stamps[1], 12, "r1")],
        }
        rows = by_type(detect(inputs))
        br = rows["blind_resend"]
        self.assertEqual([r["run_id"] for r in br], [f"astra-{stamps[1]}"])
        self.assertTrue(json.loads(br[0]["metrics_json"])["same_request_shape"])
        churn = rows["resume_retry_churn"]
        self.assertEqual(len(churn), 1)
        self.assertEqual(json.loads(churn[0]["metrics_json"])["n_failures"], 3)


class PollingTests(unittest.TestCase):
    def test_repeated_and_poll_commands(self) -> None:
        evs = []
        for i in range(8):
            evs.append(
                {
                    "stamp": "P",
                    "seq": i,
                    "line_no": i + 1,
                    "type": "item.completed",
                    "command_sha256": f"h{i % 2}",
                    "command_class": "status_poll" if i % 2 else "file_read",
                }
            )
        inputs = {
            "runs": [run("astra-P", receipt_stamp="P")],
            "receipt_runs": [receipt("P")],
            "receipt_events": evs,
        }
        rows = by_type(detect(inputs))["supervisor_polling_loop"]
        self.assertEqual(len(rows), 1)
        m = json.loads(rows[0]["metrics_json"])
        self.assertEqual(m["max_repeats"], 4)
        self.assertEqual(m["n_poll_commands"], 4)

    def test_command_classes(self) -> None:
        c = cmdclass.command_class
        ps = '"C:\\\\Windows\\\\System32\\\\WindowsPowerShell\\\\v1.0\\\\powershell.exe" -Command '
        self.assertEqual(c(ps + '"Start-Sleep -Seconds 30"'), "sleep_wait")
        self.assertEqual(c(ps + '"gh pr checks 12"'), "status_poll")
        self.assertEqual(c(ps + '"Get-Content -Raw notes.md"'), "file_read")
        self.assertEqual(c("/bin/bash -lc 'git status --short'"), "vcs_read")
        self.assertEqual(
            c('powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "gh issue view 3"'),
            "forge_read",
        )
        self.assertEqual(c("$env:PYTHONPATH='src'; python -m unittest"), "build_test")
        self.assertEqual(c("git push origin main"), "vcs_write")
        self.assertIsNone(c(None))


class HarnessNeutralTests(unittest.TestCase):
    def test_kilo_runs_are_detected_like_any_harness(self) -> None:
        inputs = {
            "runs": [
                run("kilo:1", role="agent", harness="kilo", route_id="byok/model-b"),
                run(
                    "kilo:1/staff",
                    role="agent_child",
                    harness="kilo",
                    parent_run_id="kilo:1",
                    root_run_id="kilo:1",
                    wall_s=120.0,
                ),
            ],
            "idle_gaps": [
                {
                    "run_id": "kilo:1/staff",
                    "gap_id": "g",
                    "gap_s": 900.0,
                    "ended_by": "event",
                    "before_event": "tool_start",
                    "after_event": "tool_end",
                }
            ],
            "tool_calls": [
                {
                    "run_id": "kilo:1/staff",
                    "tool_call_id": "tc1",
                    "tool_name": "bash",
                    "permission_denied": True,
                    "decision": "deny",
                    "trace_id": "tk",
                    "span_id": "sk",
                    "source": "telemetry",
                }
            ],
            "hops": [
                {
                    "hop_id": "h1",
                    "parent_run_id": "kilo:1",
                    "child_run_id": "kilo:1/staff",
                    "requested_route": "byok/model-b",
                    "verified_route": "byok/fallback",
                    "streaming_observed": False,
                }
            ],
            "model_requests": [
                {
                    "run_id": "kilo:1/staff",
                    "http_status": 400,
                    "provider_error_code": 11133,
                    "error_type": "model_param_invalid",
                    "provider_request_id": "rk",
                    "trace_id": "tk",
                    "span_id": "m1",
                    "provider": "byok",
                }
            ],
        }
        rows = by_type(detect(inputs))
        for t in ("long_idle_gap", "permission_denied", "unverified_hop", "one_shot_capture"):
            self.assertEqual(rows[t][0]["harness"], "kilo", t)
            self.assertEqual(rows[t][0]["run_role"], "agent_child", t)
            self.assertEqual(rows[t][0]["root_run_id"], "kilo:1", t)
        self.assertEqual(
            rows["provider_param_rejection"][0]["run_id"], "kilo:1"
        )  # attributed to the root

    def test_kilo_mart_rows_as_written_by_the_kilo_ingester(self) -> None:
        # kilo_fact_* rows: roles kilo_session/kilo_subagent, evidence ids instead of span ids.
        self.assertIn("kilo_", detectors.HARNESS_MART_PREFIXES)
        self.assertEqual(
            set(detectors.FACT_TABLES),
            {"runs", "model_requests", "idle_gaps", "hops", "tool_calls"},
        )
        inputs = {
            "runs": [
                run("kilo:s1", role="kilo_session", harness="kilo", task_id="proj"),
                run(
                    "kilo:s2",
                    role="kilo_subagent",
                    harness="kilo",
                    parent_run_id="kilo:s1",
                    root_run_id="kilo:s1",
                    killed=True,
                    outcome="killed",
                ),
            ],
            "idle_gaps": [
                {
                    "run_id": "kilo:s2",
                    "gap_id": "kg",
                    "gap_s": 2.0,
                    "ended_by": "run_end",
                    "before_evidence_id": "s2#m7.p3",
                }
            ],
        }
        rows = by_type(detect(inputs))["unjustified_kill"]
        self.assertEqual(rows[0]["run_id"], "kilo:s2")
        self.assertEqual(rows[0]["run_role"], "kilo_subagent")
        self.assertIn("evidence:s2#m7.p3", rows[0]["evidence_refs"])

    def test_verification_traces_are_excluded(self) -> None:
        inputs = {
            "runs": [
                run(
                    "astra-verify",
                    task_id="telemetry-verify-11133-capture",
                    outcome="failed",
                    exit_code=1,
                    wall_s=5.0,
                    n_tool_calls=0,
                ),
                run(
                    "astra-verify/child",
                    parent_run_id="astra-verify",
                    root_run_id="astra-verify",
                    killed=True,
                ),
                run("trace:x/telemetry-verify", harness="verify", outcome="failed", exit_code=1),
            ],
            "idle_gaps": [
                {"run_id": "astra-verify/child", "gap_id": "g", "gap_s": 1.0, "ended_by": "run_end"}
            ],
        }
        self.assertEqual(detect(inputs), [])


class ExperimentManifestTests(unittest.TestCase):
    def manifest(self, **kw):
        m = json.loads(
            (PIPE / "experiments" / "example.trial-manifest.json").read_text(encoding="utf-8")
        )
        m.update(kw)
        return m

    def test_example_manifest_and_schema_are_valid(self) -> None:
        self.assertEqual(experiments.validate_manifest(self.manifest()), [])
        schema = json.loads(
            (PIPE / "experiments" / "trial-manifest.schema.json").read_text(encoding="utf-8")
        )
        self.assertEqual(schema["$id"], experiments.MANIFEST_VERSION)
        self.assertTrue(set(schema["required"]) <= set(self.manifest()))

    def test_trials_link_to_runs_and_count_kills(self) -> None:
        m = self.manifest(
            trials=[
                {
                    "trial_id": "C-01",
                    "cell": "C",
                    "receipt_stamp": "20260924T190924Z",
                    "randomization_seed": 7,
                },
                {"trial_id": "D-01", "cell": "D", "run_id": "astra-D", "success": False},
                {"trial_id": "D-02", "cell": "D", "planned": True},
            ]
        )
        runs = [
            run("astra-20260924T190924Z", receipt_stamp="20260924T190924Z", outcome="killed"),
            run("astra-D", outcome="completed"),
        ]
        incidents = [
            {
                "incident_type": "unjustified_kill",
                "run_id": "astra-20260924T190924Z",
                "root_run_id": "astra-20260924T190924Z",
            }
        ]
        dims, trials, errors = experiments.build_rows(
            [("m.json", "abc", m)], runs, incidents, "snap"
        )
        self.assertEqual(errors, {})
        self.assertEqual(sorted(d["cell"] for d in dims), ["C", "D"])
        t = {x["trial_id"]: x for x in trials}
        self.assertEqual(t["C-01"]["run_id"], "astra-20260924T190924Z")
        self.assertEqual(t["C-01"]["kills_at_cap"], 1)
        self.assertFalse(t["C-01"]["success"])
        self.assertFalse(t["D-01"]["success"])  # manifest judgment wins over run outcome
        self.assertFalse(t["D-02"]["run_linked"])
        self.assertIsNone(t["D-02"]["kills_at_cap"])
        self.assertEqual(
            t["C-01"]["experiment_key"],
            dims[0]["experiment_key"] if dims[0]["cell"] == "C" else dims[1]["experiment_key"],
        )

    def test_invalid_manifest_is_reported_not_loaded(self) -> None:
        bad = self.manifest(trials=[{"trial_id": "X", "cell": "Z"}])
        dims, trials, errors = experiments.build_rows([("bad.json", "x", bad)], [], [], "snap")
        self.assertEqual((dims, trials), ([], []))
        self.assertIn("bad.json", errors)

    def test_no_manifests_no_rows(self) -> None:
        self.assertEqual(experiments.build_rows([], [run("a")], [], "snap"), ([], [], {}))


if __name__ == "__main__":
    unittest.main()
