"""Contract-level tests for the operational Issue Ledger.

These tests intentionally do not implement the ledger.  They make the first
boundary executable: schemas, evidence gates, and the documented metamorphic
properties must remain present before a runtime projection is added.
"""

from __future__ import annotations

import importlib.util
import itertools
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_ROOT = REPO_ROOT / "schemas" / "issue-ledger"
SPEC = REPO_ROOT / "docs" / "ISSUE-LEDGER-SPEC.md"
INVARIANTS = REPO_ROOT / "docs" / "ISSUE-LEDGER-INVARIANTS.md"
REPORT_FIXTURE = REPO_ROOT / "operational" / "examples" / "issue-ledger-report-v1.json"
SCRIPT_DIR = REPO_ROOT / "operational" / "scripts"
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
LEDGER_SPEC = importlib.util.spec_from_file_location("issue_ledger", SCRIPT_DIR / "issue_ledger.py")
ledger = importlib.util.module_from_spec(LEDGER_SPEC)
LEDGER_SPEC.loader.exec_module(ledger)
from issue_ledger_actions import main as actions_main  # noqa: E402
from issue_ledger_agent import main as agent_main  # noqa: E402
from issue_ledger_store import AppendOnlyEventStore, StoreError  # noqa: E402
from issue_ledger_store import main as store_main  # noqa: E402
from run_with_issue_ledger import main as wrapper_main  # noqa: E402
from telemetry_to_issue_events import convert_record  # noqa: E402


def load_schema(name: str) -> dict:
    return json.loads((SCHEMA_ROOT / name).read_text(encoding="utf-8"))


def valid_report_packet() -> dict:
    return {
        "schema_version": "issue-ledger/report/v1",
        "actor": {"id": "agent:fixture", "kind": "agent", "harness": "fixture"},
        "subject": {
            "provider": "provider:test",
            "route": "route:test",
            "model": "model:test",
            "operation": "completion",
            "workload_class": "long_horizon",
            "stream_mode": "sse",
        },
        "execution_id": "run:fixture-001",
        "correlation_id": "turn:fixture-001",
        "summary": "the stream ended without a terminal marker",
        "classification": "harness",
        "outcome": "partial",
        "observed_at": "2026-09-22T12:00:00Z",
        "recorded_at": "2026-09-22T12:00:01Z",
        "receipt_refs": ["sha256:fixture-receipt"],
        "runbook_refs": ["runbook:stream-capture"],
        "attempted_recovery": {
            "status": "failed",
            "action": "reconnect the stream",
            "result": "no terminal event arrived",
        },
        "proposed_next_action": "replay with the same fixture under the verifier",
        "observation": {
            "failure_phase": "capture",
            "stream_mode": "sse",
            "finish_reason_capture_status": "partial",
            "timeout_policy": "inactivity-20m",
        },
    }


def valid_event() -> dict:
    return {
        "schema_version": "issue-ledger/v1",
        "event_id": "ILE-test-001",
        "event_type": "report_submitted",
        "recorded_at": "2026-09-22T12:00:00Z",
        "actor": {"id": "agent:test", "kind": "agent", "harness": "fixture"},
        "subject_refs": ["route:test/model:test"],
        "correlation_id": "run:test-001",
        "idempotency_key": "fixture:test-001",
        "payload": {"summary": "capture was incomplete"},
    }


def valid_issue() -> dict:
    return {
        "schema_version": "issue-ledger/issue/v1",
        "issue_id": "ISSUE-test-001",
        "lifecycle": "CANDIDATE",
        "claim": {
            "summary": "A runner reported incomplete capture",
            "classification": "harness",
            "reported_by": "agent:test",
        },
        "subject": {
            "provider": "provider:test",
            "route": "route:test",
            "operation": "completion",
            "workload_class": "long_horizon",
        },
        "fingerprint": {
            "version": "fp-v1",
            "value": "sha256:test",
            "components": {"failure_phase": "capture", "stream_mode": "sse"},
        },
        "evidence_summary": {
            "level": "reported_only",
            "report_count": 1,
            "distinct_execution_count": 1,
            "distinct_actor_count": 1,
            "distinct_correlation_count": 1,
            "independent_reproduction_count": 0,
            "deterministic_receipt_count": 0,
            "counterexample_count": 0,
            "accepted_for_recommendation": False,
        },
        "occurrences": [
            {
                "occurrence_id": "occ:test-001",
                "event_ref": "ILE-test-001",
                "execution_id": "run:test-001",
                "correlation_id": "run:test-001",
                "actor_id": "agent:test",
                "observed_at": "2026-09-22T12:00:00Z",
                "outcome": "unknown",
                "evidence_level": "reported_only",
                "receipt_ref": None,
            }
        ],
        "reproduction": {"status": "not_requested", "attempt_count": 0},
        "resolution": {"status": "unknown", "fix_refs": [], "verification_refs": []},
        "diagnostics": {"next_action": None, "receipt_refs": [], "runbook_refs": []},
        "timestamps": {
            "known_at": "2026-09-22T12:00:00Z",
            "last_updated_at": "2026-09-22T12:00:00Z",
            "valid_from": "2026-09-22T12:00:00Z",
            "valid_to": None,
        },
        "provenance": {
            "event_refs": ["ILE-test-001"],
            "projection_policy_hash": "policy:test",
            "source_snapshot_ref": None,
        },
    }


def report_event(
    event_id: str, execution_id: str, recorded_at: str = "2026-09-22T12:00:00Z"
) -> dict:
    event = valid_event()
    event.update({"event_id": event_id, "recorded_at": recorded_at})
    event["correlation_id"] = execution_id
    event["idempotency_key"] = event_id
    event["payload"] = {
        "summary": "stream capture was incomplete",
        "classification": "harness",
        "execution_id": execution_id,
        "observed_at": recorded_at,
        "outcome": "partial",
        "subject": {
            "provider": "provider:test",
            "route": "route:test",
            "model": "model:test",
            "operation": "completion",
            "workload_class": "long_horizon",
            "stream_mode": "sse",
        },
        "observation": {
            "failure_phase": "capture",
            "stream_mode": "sse",
            "finish_reason_capture_status": "partial",
        },
    }
    return event


class IssueLedgerContractTests(unittest.TestCase):
    def test_schemas_are_versioned_and_closed(self):
        event = load_schema("v1.event.schema.json")
        issue = load_schema("v1.issue.schema.json")
        report = load_schema("v1.report.schema.json")
        ire = load_schema("v1.ire.schema.json")
        for schema in (event, issue, report, ire):
            self.assertTrue(
                schema["$id"].startswith(
                    "https://inference-recommendation-engine.local/schemas/issue-ledger/"
                )
            )
        self.assertEqual(event["$id"].rsplit("/", 1)[-1], "v1.event.schema.json")
        self.assertEqual(issue["$id"].rsplit("/", 1)[-1], "v1.issue.schema.json")
        self.assertEqual(event["additionalProperties"], False)
        self.assertEqual(issue["additionalProperties"], False)
        self.assertEqual(report["additionalProperties"], False)
        self.assertEqual(ire["additionalProperties"], False)
        self.assertEqual(event["properties"]["schema_version"]["const"], "issue-ledger/v1")
        self.assertEqual(issue["properties"]["schema_version"]["const"], "issue-ledger/issue/v1")
        self.assertEqual(report["properties"]["schema_version"]["const"], "issue-ledger/report/v1")
        self.assertEqual(ire["properties"]["schema_version"]["const"], "ire/issue-ledger/v1")

    def test_report_packet_fixture_is_closed_and_normalizable(self):
        report = load_schema("v1.report.schema.json")
        packet = valid_report_packet()
        fixture = json.loads(REPORT_FIXTURE.read_text(encoding="utf-8"))
        self.assertTrue(set(report["required"]).issubset(packet))
        self.assertEqual(fixture, packet)
        self.assertEqual(ledger.validate_report_packet(packet), packet)
        event = ledger.make_report_event(packet)
        self.assertEqual(event["event_type"], "report_submitted")
        self.assertEqual(event["payload"]["receipt_refs"], packet["receipt_refs"])
        self.assertEqual(event["payload"]["attempted_recovery"], packet["attempted_recovery"])
        self.assertEqual(event["payload"]["proposed_next_action"], packet["proposed_next_action"])
        issue = ledger.reduce_events([event])[0]
        self.assertEqual(issue["diagnostics"]["next_action"], packet["proposed_next_action"])
        self.assertIn("sha256:fixture-receipt", issue["diagnostics"]["receipt_refs"])
        self.assertEqual(issue["diagnostics"]["runbook_refs"], ["runbook:stream-capture"])
        invalid = dict(packet)
        invalid["unbounded_agent_field"] = "must be rejected"
        with self.assertRaises(ledger.LedgerError):
            ledger.validate_report_packet(invalid)

    def test_report_packet_requires_explicit_schema_version(self):
        packet = valid_report_packet()
        packet.pop("schema_version")
        with self.assertRaises(ledger.LedgerError):
            ledger.make_report_event(packet)

    def test_minimal_fixtures_cover_required_schema_keys(self):
        event = load_schema("v1.event.schema.json")
        issue = load_schema("v1.issue.schema.json")
        event_fixture = valid_event()
        issue_fixture = valid_issue()
        self.assertTrue(set(event["required"]).issubset(event_fixture))
        self.assertTrue(set(issue["required"]).issubset(issue_fixture))
        self.assertEqual(issue_fixture["evidence_summary"]["accepted_for_recommendation"], False)

    def test_report_only_fixture_cannot_be_recommendation_accepted(self):
        candidate = valid_issue()
        evidence = candidate["evidence_summary"]
        self.assertEqual(evidence["level"], "reported_only")
        self.assertEqual(evidence["independent_reproduction_count"], 0)
        self.assertFalse(evidence["accepted_for_recommendation"])
        self.assertIn("reported_only", SPEC.read_text(encoding="utf-8").lower())

    def test_required_invariants_and_metamorphic_cases_are_documented(self):
        invariants = INVARIANTS.read_text(encoding="utf-8")
        for marker in (
            "duplicated events",
            "reorder event delivery",
            "retry chain",
            "absence of an issue report",
            "counterexample",
            "bitemporal",
            "read-only",
        ):
            self.assertIn(marker.lower(), invariants.lower(), marker)
        for marker in (
            "append",
            "fingerprint",
            "independent",
            "rolling",
            "resolution",
        ):
            self.assertIn(marker.lower(), SPEC.read_text(encoding="utf-8").lower(), marker)

    def test_event_types_are_explicit_and_not_free_form(self):
        event_types = load_schema("v1.event.schema.json")["properties"]["event_type"]["enum"]
        self.assertIn("report_submitted", event_types)
        self.assertIn("reproduction_completed", event_types)
        self.assertIn("resolution_verified", event_types)
        self.assertNotIn("arbitrary", event_types)

    def test_event_runtime_validator_matches_closed_schema_boundary(self):
        unknown = valid_event()
        unknown["unbounded_adapter_field"] = "must be rejected"
        with self.assertRaises(ledger.LedgerError):
            ledger.validate_event(unknown)
        actor_unknown = valid_event()
        actor_unknown["actor"]["unbounded_actor_field"] = "must be rejected"
        with self.assertRaises(ledger.LedgerError):
            ledger.validate_event(actor_unknown)

    def test_operational_guidance_points_to_the_ledger_contract(self):
        operational_reference = (
            REPO_ROOT / "operational" / "references" / "issue-ledger.md"
        ).read_text(encoding="utf-8")
        operations = (REPO_ROOT / "docs" / "ISSUE-LEDGER-OPERATIONS.md").read_text(encoding="utf-8")
        self.assertIn("Operational Issue Ledger", operational_reference)
        for text in (operational_reference, operations):
            self.assertIn("reported_only", text)
            self.assertIn("execution", text.lower())
        self.assertIn("report_submitted", operational_reference)
        self.assertIn("issue_ledger_store.py", operational_reference)
        self.assertIn("diagnose", operational_reference)

    def test_fingerprint_is_stable_for_prose_and_author_changes(self):
        subject = {
            "provider": "P",
            "route": "R",
            "operation": "completion",
            "workload_class": "long",
        }
        observation = {"failure_phase": "capture", "stream_mode": "sse", "error_code": "partial"}
        first = ledger.make_fingerprint(subject, observation)
        second = ledger.make_fingerprint(
            subject, {**observation, "summary": "different words", "actor": "another"}
        )
        changed = ledger.make_fingerprint(subject, {**observation, "stream_mode": "buffered"})
        self.assertEqual(first, second)
        self.assertNotEqual(first["value"], changed["value"])

    def test_adapter_report_packet_gets_stable_identity(self):
        packet = valid_report_packet()
        packet["execution_id"] = "run-packet-1"
        first = ledger.make_report_event(packet)
        second = ledger.make_report_event(packet)
        self.assertEqual(first["event_id"], second["event_id"])
        self.assertEqual(first["idempotency_key"], second["idempotency_key"])
        self.assertEqual(first["valid_at"]["from"], packet["observed_at"])
        self.assertEqual(ledger.reduce_events([first])[0]["lifecycle"], "CANDIDATE")

    def test_duplicate_adapter_delivery_and_reordering_are_idempotent(self):
        first = report_event("ILE-report-1", "run-1")
        duplicate = report_event("ILE-adapter-copy", "run-1")
        duplicate["idempotency_key"] = first["idempotency_key"]
        second = report_event("ILE-report-2", "run-2", "2026-09-22T12:01:00Z")
        left = ledger.reduce_events([first, duplicate, second])
        right = ledger.reduce_events([second, first, duplicate])
        self.assertEqual(left, right)
        self.assertEqual(left[0]["evidence_summary"]["report_count"], 2)
        self.assertEqual(left[0]["evidence_summary"]["distinct_execution_count"], 2)
        self.assertEqual(left[0]["evidence_summary"]["distinct_correlation_count"], 2)

    def test_shared_execution_preserves_multiple_reporting_agents(self):
        first = report_event("ILE-shared-execution-a", "run-shared")
        second = report_event("ILE-shared-execution-b", "run-shared", "2026-09-22T12:01:00Z")
        second["actor"] = {
            "id": "agent:independent-observer",
            "kind": "agent",
            "harness": "fixture",
        }
        issue = ledger.reduce_events([first, second])[0]
        self.assertEqual(issue["evidence_summary"]["report_count"], 2)
        self.assertEqual(issue["evidence_summary"]["distinct_execution_count"], 1)
        self.assertEqual(issue["evidence_summary"]["distinct_correlation_count"], 1)
        self.assertEqual(issue["evidence_summary"]["distinct_actor_count"], 2)

    def test_retry_chain_is_one_correlation_but_multiple_executions(self):
        first = report_event("ILE-retry-a", "run-retry-a")
        second = report_event("ILE-retry-b", "run-retry-b", "2026-09-22T12:01:00Z")
        first["correlation_id"] = "retry-chain-001"
        second["correlation_id"] = "retry-chain-001"
        issue = ledger.reduce_events([first, second])[0]
        self.assertEqual(issue["evidence_summary"]["distinct_execution_count"], 2)
        self.assertEqual(issue["evidence_summary"]["distinct_correlation_count"], 1)

    def test_conflicting_correlation_for_one_execution_fails_closed(self):
        first = report_event("ILE-correlation-conflict-a", "run-correlation-conflict")
        second = report_event(
            "ILE-correlation-conflict-b", "run-correlation-conflict", "2026-09-22T12:01:00Z"
        )
        second["correlation_id"] = "different-correlation"
        with self.assertRaises(ledger.LedgerError):
            ledger.reduce_events([first, second])

    def test_generated_event_orders_and_duplicate_delivery_are_projection_invariant(self):
        events = [
            report_event("ILE-generated-1", "run-generated-1", "2026-09-22T12:00:00Z"),
            report_event("ILE-generated-2", "run-generated-2", "2026-09-22T12:01:00Z"),
            report_event("ILE-generated-3", "run-generated-3", "2026-09-22T12:02:00Z"),
            report_event("ILE-generated-4", "run-generated-4", "2026-09-22T12:03:00Z"),
        ]
        baseline = ledger.reduce_events(events + [events[1]])
        for ordering in itertools.permutations(events):
            self.assertEqual(ledger.reduce_events(list(ordering) + [ordering[1]]), baseline)

    def test_report_only_is_not_exported_to_ire(self):
        projections = ledger.reduce_events([report_event("ILE-report-only", "run-only")])
        self.assertEqual(projections[0]["lifecycle"], "CANDIDATE")
        self.assertEqual(ledger.export_ire(projections)["issues"], [])

    def test_verified_deterministic_receipt_can_be_exported(self):
        report = report_event("ILE-report-verified", "run-verified")
        receipt = report_event("ILE-receipt-verified", "run-verified", "2026-09-22T12:01:00Z")
        receipt["event_type"] = "evidence_attached"
        receipt["actor"] = {"id": "system:issue-ledger-verifier", "kind": "system"}
        receipt["provenance"] = {"producer": "fixture-verifier", "policy_hash": "policy:test"}
        receipt["payload"].update(
            {
                "receipt_ref": "sha256:receipt",
                "deterministic_verifier": True,
                "verified": True,
                "accept_for_recommendation": True,
            }
        )
        projections = ledger.reduce_events([report, receipt])
        issue = projections[0]
        self.assertTrue(issue["evidence_summary"]["accepted_for_recommendation"])
        self.assertEqual(issue["evidence_summary"]["deterministic_receipt_count"], 1)
        exported = ledger.export_ire(projections)
        self.assertEqual(len(exported["issues"]), 1)
        self.assertEqual(exported["issues"][0]["lifecycle"], "ACCEPTED")
        self.assertIn("diagnostics", exported["issues"][0])
        self.assertIn("sha256:receipt", exported["issues"][0]["diagnostics"]["receipt_refs"])
        self.assertIn("reproduction", exported["issues"][0])
        self.assertIn("resolution", exported["issues"][0])
        from jsonschema import Draft202012Validator

        Draft202012Validator(load_schema("v1.ire.schema.json")).validate(exported)
        exported["issues"].clear()
        self.assertEqual(len(ledger.export_ire(projections)["issues"]), 1)

    def test_untrusted_verifier_identity_cannot_promote_a_report(self):
        report = report_event("ILE-untrusted-report", "run-untrusted")
        receipt = report_event("ILE-untrusted-receipt", "run-untrusted", "2026-09-22T12:01:00Z")
        receipt["event_type"] = "evidence_attached"
        receipt["actor"] = {"id": "agent:pretending-to-verify", "kind": "system"}
        receipt["payload"].update(
            {
                "receipt_ref": "sha256:untrusted",
                "deterministic_verifier": True,
                "verified": True,
                "accept_for_recommendation": True,
            }
        )
        issue = ledger.reduce_events([report, receipt])[0]
        self.assertFalse(issue["evidence_summary"]["accepted_for_recommendation"])
        self.assertEqual(issue["evidence_summary"]["deterministic_receipt_count"], 0)

    def test_allowlisted_verifier_without_provenance_cannot_promote(self):
        report = report_event("ILE-missing-provenance-report", "run-missing-provenance")
        receipt = report_event(
            "ILE-missing-provenance-receipt", "run-missing-provenance", "2026-09-22T12:01:00Z"
        )
        receipt["event_type"] = "evidence_attached"
        receipt["actor"] = {"id": "system:issue-ledger-verifier", "kind": "system"}
        receipt["payload"].update(
            {
                "receipt_ref": "sha256:missing-provenance",
                "deterministic_verifier": True,
                "verified": True,
                "accept_for_recommendation": True,
            }
        )
        issue = ledger.reduce_events([report, receipt])[0]
        self.assertFalse(issue["evidence_summary"]["accepted_for_recommendation"])
        self.assertEqual(issue["evidence_summary"]["deterministic_receipt_count"], 0)

    def test_explicit_rolling_window_changes_export_not_history(self):
        report = report_event("ILE-window-report", "run-window", "2026-09-22T12:00:00Z")
        receipt = report_event("ILE-window-receipt", "run-window", "2026-09-22T12:01:00Z")
        receipt["event_type"] = "evidence_attached"
        receipt["actor"] = {"id": "system:issue-ledger-verifier", "kind": "system"}
        receipt["provenance"] = {"producer": "fixture-verifier", "policy_hash": "policy:test"}
        receipt["payload"].update(
            {
                "receipt_ref": "sha256:window",
                "deterministic_verifier": True,
                "verified": True,
                "accept_for_recommendation": True,
            }
        )
        projections = ledger.reduce_events([report, receipt])
        self.assertEqual(len(projections[0]["occurrences"]), 1)
        current = ledger.export_ire(projections, window_days=7, as_of="2026-09-22T23:59:59Z")
        self.assertEqual(current["issues"][0]["window"]["occurrence_count"], 1)
        self.assertEqual(current["issues"][0]["window"]["distinct_correlation_count"], 1)
        later = ledger.export_ire(projections, window_days=7, as_of="2026-10-01T00:00:00Z")
        self.assertEqual(later["issues"], [])
        self.assertEqual(len(projections[0]["occurrences"]), 1)

    def test_counterexample_removes_issue_from_recommendation_export(self):
        report = report_event("ILE-report-counterexample", "run-counterexample")
        receipt = report_event(
            "ILE-receipt-counterexample", "run-counterexample", "2026-09-22T12:01:00Z"
        )
        receipt["event_type"] = "evidence_attached"
        receipt["actor"] = {"id": "system:issue-ledger-verifier", "kind": "system"}
        receipt["provenance"] = {"producer": "fixture-verifier", "policy_hash": "policy:test"}
        receipt["payload"].update(
            {
                "receipt_ref": "sha256:receipt-2",
                "deterministic_verifier": True,
                "verified": True,
                "accept_for_recommendation": True,
            }
        )
        counter = report_event("ILE-counterexample", "run-counterexample-2", "2026-09-22T12:02:00Z")
        counter["event_type"] = "issue_retracted"
        counter["actor"] = {"id": "system:issue-ledger-verifier", "kind": "system"}
        counter["provenance"] = {"producer": "fixture-verifier", "policy_hash": "policy:test"}
        counter["payload"].update({"counterexample": True, "verified": True})
        issue = ledger.reduce_events([report, receipt, counter])[0]
        self.assertEqual(issue["lifecycle"], "REJECTED")
        self.assertEqual(ledger.export_ire([issue])["issues"], [])

    def test_agent_fix_requires_verification_and_later_failure_reopens(self):
        report = report_event("ILE-resolution-report", "run-resolution")
        proposed = report_event("ILE-resolution-proposal", "run-resolution", "2026-09-22T12:01:00Z")
        proposed["event_type"] = "resolution_proposed"
        proposed["payload"].update(
            {"fix_ref": "commit:fixture-fix", "summary": "proposed a capture fix"}
        )
        verified = report_event("ILE-resolution-verified", "run-resolution", "2026-09-22T12:02:00Z")
        verified["event_type"] = "resolution_verified"
        verified["actor"] = {"id": "system:issue-ledger-verifier", "kind": "system"}
        verified["evidence_refs"] = [{"ref_id": "sha256:regression", "role": "reproduction"}]
        verified["provenance"] = {"producer": "fixture-verifier", "policy_hash": "policy:test"}
        verified["payload"].update(
            {
                "fix_ref": "commit:fixture-fix",
                "verified": True,
                "replay_receipt_ref": "sha256:replay",
                "regression_receipt_ref": "sha256:regression",
            }
        )
        resolved = ledger.reduce_events([report, proposed, verified])[0]
        self.assertEqual(resolved["lifecycle"], "RESOLVED")
        self.assertEqual(resolved["resolution"]["status"], "fix_verified")
        self.assertIn("next_action", resolved["diagnostics"])
        reopened = report_event(
            "ILE-resolution-regression", "run-resolution-regression", "2026-09-22T12:03:00Z"
        )
        reopened["event_type"] = "issue_reopened"
        reopened["actor"] = {"id": "system:issue-ledger-verifier", "kind": "system"}
        reopened["provenance"] = {"producer": "fixture-verifier", "policy_hash": "policy:test"}
        reopened["payload"].update(
            {"verified": True, "reopen_receipt_ref": "sha256:regression-reopen"}
        )
        regressed = ledger.reduce_events([report, proposed, verified, reopened])[0]
        self.assertEqual(regressed["lifecycle"], "REGRESSED")

    def test_resolution_without_replay_and_regression_receipts_stays_unresolved(self):
        report = report_event("ILE-incomplete-resolution-report", "run-incomplete-resolution")
        verified = report_event(
            "ILE-incomplete-resolution", "run-incomplete-resolution", "2026-09-22T12:01:00Z"
        )
        verified["event_type"] = "resolution_verified"
        verified["actor"] = {"id": "system:issue-ledger-verifier", "kind": "system"}
        verified["evidence_refs"] = [{"ref_id": "sha256:only-one", "role": "reproduction"}]
        verified["provenance"] = {"producer": "fixture-verifier", "policy_hash": "policy:test"}
        verified["payload"].update(
            {
                "fix_ref": "commit:fixture-fix",
                "verified": True,
                "regression_receipt_ref": "sha256:regression",
            }
        )
        issue = ledger.reduce_events([report, verified])[0]
        self.assertNotEqual(issue["lifecycle"], "RESOLVED")
        self.assertEqual(issue["resolution"]["status"], "unknown")

    def test_untrusted_counterexample_and_reopen_cannot_mutate_accepted_state(self):
        report = report_event("ILE-authority-report", "run-authority")
        receipt = report_event("ILE-authority-receipt", "run-authority", "2026-09-22T12:01:00Z")
        receipt["event_type"] = "evidence_attached"
        receipt["actor"] = {"id": "system:issue-ledger-verifier", "kind": "system"}
        receipt["provenance"] = {"producer": "fixture-verifier", "policy_hash": "policy:test"}
        receipt["payload"].update(
            {
                "receipt_ref": "sha256:authority",
                "deterministic_verifier": True,
                "verified": True,
                "accept_for_recommendation": True,
            }
        )
        fake_counterexample = report_event(
            "ILE-fake-counterexample", "run-authority-fake", "2026-09-22T12:02:00Z"
        )
        fake_counterexample["event_type"] = "issue_retracted"
        fake_counterexample["actor"] = {"id": "agent:hallucinating", "kind": "agent"}
        fake_counterexample["payload"].update({"counterexample": True, "verified": True})
        fake_reopen = report_event(
            "ILE-fake-reopen", "run-authority-fake-2", "2026-09-22T12:03:00Z"
        )
        fake_reopen["event_type"] = "issue_reopened"
        fake_reopen["actor"] = {"id": "agent:hallucinating", "kind": "agent"}
        issue = ledger.reduce_events([report, receipt, fake_counterexample, fake_reopen])[0]
        self.assertEqual(issue["lifecycle"], "ACCEPTED")
        self.assertEqual(issue["evidence_summary"]["counterexample_count"], 0)


class IssueLedgerStoreTests(unittest.TestCase):
    def test_append_is_durable_and_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            store = AppendOnlyEventStore(Path(directory) / "ledger.sqlite3")
            first = report_event("ILE-store-1", "run-store-1")
            duplicate = report_event("ILE-store-adapter-copy", "run-store-1")
            duplicate["idempotency_key"] = first["idempotency_key"]
            self.assertTrue(store.append(first)["stored"])
            self.assertFalse(store.append(duplicate)["stored"])
            reopened = AppendOnlyEventStore(Path(directory) / "ledger.sqlite3")
            self.assertEqual(len(reopened.events()), 1)
            self.assertEqual(reopened.project()[0]["evidence_summary"]["report_count"], 1)

    def test_store_is_sqlite_and_event_rows_reject_update_and_delete(self):
        with tempfile.TemporaryDirectory(dir=REPO_ROOT) as directory:
            database = Path(directory) / "ledger.sqlite3"
            store = AppendOnlyEventStore(database)
            event = report_event("ILE-store-immutable", "run-store-immutable")
            store.append(event)
            self.assertTrue(database.read_bytes().startswith(b"SQLite format 3\x00"))
            connection = sqlite3.connect(database)
            try:
                with self.assertRaisesRegex(sqlite3.IntegrityError, "append-only"):
                    connection.execute(
                        "UPDATE ledger_events SET canonical_json = '{}' WHERE event_id = ?",
                        (event["event_id"],),
                    )
                with self.assertRaisesRegex(sqlite3.IntegrityError, "append-only"):
                    connection.execute(
                        "DELETE FROM ledger_events WHERE event_id = ?", (event["event_id"],)
                    )
            finally:
                connection.close()

    def test_conflicting_replay_fails_closed_without_appending(self):
        with tempfile.TemporaryDirectory() as directory:
            store = AppendOnlyEventStore(Path(directory) / "ledger.sqlite3")
            first = report_event("ILE-store-conflict", "run-store-conflict")
            store.append(first)
            conflict = report_event("ILE-store-conflict-copy", "run-store-conflict")
            conflict["idempotency_key"] = first["idempotency_key"]
            conflict["payload"]["summary"] = "different claim"
            with self.assertRaises(StoreError):
                store.append(conflict)
            self.assertEqual(len(store.events()), 1)

    def test_store_ire_read_is_a_projection_not_a_write(self):
        with tempfile.TemporaryDirectory() as directory:
            store = AppendOnlyEventStore(Path(directory) / "ledger.sqlite3")
            store.append(report_event("ILE-store-ire", "run-store-ire"))
            before = store.events()
            self.assertEqual(store.ire()["issues"], [])
            self.assertEqual(store.events(), before)

    def test_concurrent_process_appenders_preserve_all_events(self):
        child_code = r"""
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[3])
from issue_ledger import make_report_event
from issue_ledger_store import AppendOnlyEventStore

log = Path(sys.argv[1])
agent = sys.argv[2]
store = AppendOnlyEventStore(log)
for index in range(5):
    execution_id = f"run:{agent}:{index}"
    stamp = f"2026-09-22T12:{int(agent):02d}:{index:02d}Z"
    packet = {
        "schema_version": "issue-ledger/report/v1",
        "actor": {"id": f"agent:concurrent:{agent}", "kind": "agent", "harness": "concurrency-fixture"},
        "subject": {"provider": "provider:test", "route": "route:test", "model": "model:test", "operation": "completion", "workload_class": "long_horizon", "stream_mode": "sse"},
        "execution_id": execution_id,
        "correlation_id": execution_id,
        "summary": "concurrent fixture report",
        "classification": "harness",
        "outcome": "failure",
        "observed_at": stamp,
        "recorded_at": stamp,
        "observation": {"failure_phase": "capture", "stream_mode": "sse", "error_code": "fixture"},
    }
    store.append(make_report_event(packet))
"""
        with tempfile.TemporaryDirectory() as directory:
            log_path = Path(directory) / "ledger.sqlite3"
            children = [
                subprocess.Popen(
                    [sys.executable, "-c", child_code, str(log_path), str(agent), str(SCRIPT_DIR)],
                    cwd=REPO_ROOT,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
                for agent in range(3)
            ]
            outputs = [child.communicate(timeout=20) for child in children]
            for output, error in outputs:
                self.assertEqual(output, "")
                self.assertEqual(error, "")
            self.assertEqual([child.returncode for child in children], [0, 0, 0])
            store = AppendOnlyEventStore(log_path)
            events = store.events()
            self.assertEqual(len(events), 15)
            issue = store.project()[0]
            self.assertEqual(issue["evidence_summary"]["distinct_execution_count"], 15)
            self.assertEqual(issue["evidence_summary"]["distinct_actor_count"], 3)

    def test_report_cli_normalizes_packet_into_durable_event(self):
        with tempfile.TemporaryDirectory() as directory:
            packet_path = Path(directory) / "report.json"
            log_path = Path(directory) / "ledger.sqlite3"
            packet_path.write_text(
                json.dumps(
                    {
                        "schema_version": "issue-ledger/report/v1",
                        "actor": {"id": "agent:cli", "kind": "agent"},
                        "subject": {
                            "provider": "provider:test",
                            "route": "route:test",
                            "operation": "completion",
                            "workload_class": "short",
                        },
                        "execution_id": "run-cli",
                        "observed_at": "2026-09-22T12:00:00Z",
                        "recorded_at": "2026-09-22T12:00:01Z",
                        "summary": "provider returned a timeout",
                        "outcome": "timeout",
                    }
                ),
                encoding="utf-8",
            )
            self.assertEqual(store_main(["--db", str(log_path), "report", str(packet_path)]), 0)
            self.assertEqual(len(AppendOnlyEventStore(log_path).events()), 1)


class TelemetryAdapterTests(unittest.TestCase):
    def test_failure_telemetry_becomes_report_only_issue_event(self):
        event = convert_record(
            {
                "event_time": "2026-09-22T12:00:00Z",
                "ingested_at": "2026-09-22T12:00:01Z",
                "event_id": "telemetry-001",
                "agent": "runner-a",
                "provider": "provider:test",
                "model": "model:test",
                "route": "route:test",
                "operation": "chat",
                "streaming": True,
                "outcome": "failure",
                "error_class": "content_empty_with_tokens",
                "request_id": "request-001",
                "workload_class": "agent_tool_call",
            }
        )
        self.assertEqual(event["event_type"], "report_submitted")
        self.assertEqual(event["payload"]["classification"], "telemetry")
        self.assertEqual(event["payload"]["outcome"], "failure")
        self.assertEqual(event["payload"]["subject"]["stream_mode"], "sse")
        issue = ledger.reduce_events([event])[0]
        self.assertEqual(issue["evidence_summary"]["level"], "reported_only")
        self.assertFalse(issue["evidence_summary"]["accepted_for_recommendation"])

    def test_success_telemetry_is_not_an_issue_by_default(self):
        record = {
            "event_time": "2026-09-22T12:00:00Z",
            "event_id": "telemetry-success",
            "agent": "runner-a",
            "provider": "provider:test",
            "model": "model:test",
            "route": "route:test",
            "outcome": "success",
        }
        self.assertIsNone(convert_record(record))
        event = convert_record(record, include_success=True)
        self.assertIsNotNone(event)
        self.assertFalse(event["payload"]["predicate_failure"])


class AgentAdapterTests(unittest.TestCase):
    def test_sidecar_cli_reports_bounded_metadata_and_keeps_candidate_status(self):
        with tempfile.TemporaryDirectory() as directory:
            log_path = Path(directory) / "ledger.sqlite3"
            result = agent_main(
                [
                    "--db",
                    str(log_path),
                    "report",
                    "--actor-id",
                    "agent:sidecar",
                    "--harness",
                    "fixture-tui",
                    "--provider",
                    "provider:test",
                    "--route",
                    "route:test",
                    "--model",
                    "model:test",
                    "--operation",
                    "completion",
                    "--workload-class",
                    "long_horizon",
                    "--stream-mode",
                    "sse",
                    "--execution-id",
                    "run:sidecar-001",
                    "--summary",
                    "capture ended without a terminal marker",
                    "--classification",
                    "harness",
                    "--outcome",
                    "partial",
                    "--observed-at",
                    "2026-09-22T12:00:00Z",
                    "--recorded-at",
                    "2026-09-22T12:00:01Z",
                    "--failure-phase",
                    "capture",
                    "--finish-reason-capture-status",
                    "partial",
                    "--timeout-policy",
                    "inactivity-20m",
                    "--recovery-status",
                    "failed",
                    "--recovery-action",
                    "reconnect the stream",
                    "--recovery-result",
                    "no terminal event arrived",
                    "--next-action",
                    "replay under the verifier",
                ]
            )
            self.assertEqual(result, 0)
            events = AppendOnlyEventStore(log_path).events()
            self.assertEqual(len(events), 1)
            payload = events[0]["payload"]
            self.assertEqual(payload["attempted_recovery"]["status"], "failed")
            self.assertEqual(payload["proposed_next_action"], "replay under the verifier")
            self.assertEqual(ledger.reduce_events(events)[0]["lifecycle"], "CANDIDATE")

    def test_process_wrapper_reports_failure_without_imposing_timeout(self):
        with tempfile.TemporaryDirectory() as directory:
            log_path = Path(directory) / "ledger.sqlite3"
            result = wrapper_main(
                [
                    "--db",
                    str(log_path),
                    "run",
                    "--provider",
                    "provider:test",
                    "--route",
                    "route:test",
                    "--execution-id",
                    "run:wrapper-001",
                    "--stream-mode",
                    "sse",
                    "--",
                    sys.executable,
                    "-c",
                    "import sys; sys.exit(3)",
                ]
            )
            self.assertEqual(result, 3)
            events = AppendOnlyEventStore(log_path).events()
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0]["payload"]["outcome"], "failure")
            self.assertEqual(events[0]["payload"]["observation"]["error_code"], "process_exit:3")

    def test_process_wrapper_does_not_create_success_issue_by_default(self):
        with tempfile.TemporaryDirectory() as directory:
            log_path = Path(directory) / "ledger.sqlite3"
            result = wrapper_main(
                [
                    "--db",
                    str(log_path),
                    "run",
                    "--execution-id",
                    "run:wrapper-success",
                    "--",
                    sys.executable,
                    "-c",
                    "pass",
                ]
            )
            self.assertEqual(result, 0)
            self.assertEqual(AppendOnlyEventStore(log_path).events(), [])

    def test_lifecycle_cli_records_proposed_and_verified_fix(self):
        with tempfile.TemporaryDirectory() as directory:
            log_path = Path(directory) / "ledger.sqlite3"
            report = report_event("ILE-action-report", "run-action", "2026-09-22T12:00:00Z")
            AppendOnlyEventStore(log_path).append(report)
            common = [
                "--db",
                str(log_path),
                "propose-fix",
                "--provider",
                "provider:test",
                "--route",
                "route:test",
                "--model",
                "model:test",
                "--operation",
                "completion",
                "--workload-class",
                "long_horizon",
                "--stream-mode",
                "sse",
                "--execution-id",
                "run-action",
                "--summary",
                "use the stream-aware capture path",
                "--classification",
                "harness",
                "--fix-ref",
                "commit:fixture-fix",
                "--observed-at",
                "2026-09-22T12:01:00Z",
                "--recorded-at",
                "2026-09-22T12:01:00Z",
            ]
            self.assertEqual(actions_main(common), 0)
            verify = [
                "--db",
                str(log_path),
                "verify-resolution",
                "--actor-id",
                "system:issue-ledger-verifier",
                "--actor-kind",
                "system",
                "--provider",
                "provider:test",
                "--route",
                "route:test",
                "--model",
                "model:test",
                "--operation",
                "completion",
                "--workload-class",
                "long_horizon",
                "--stream-mode",
                "sse",
                "--execution-id",
                "run-action",
                "--summary",
                "replay and regression passed",
                "--classification",
                "harness",
                "--fix-ref",
                "commit:fixture-fix",
                "--replay-receipt-ref",
                "sha256:replay-action",
                "--regression-receipt-ref",
                "sha256:regression-action",
                "--provenance-producer",
                "fixture-verifier",
                "--policy-hash",
                "policy:test",
                "--observed-at",
                "2026-09-22T12:02:00Z",
                "--recorded-at",
                "2026-09-22T12:02:00Z",
            ]
            self.assertEqual(actions_main(verify), 0)
            events = AppendOnlyEventStore(log_path).events()
            self.assertEqual(
                {event["event_type"] for event in events},
                {"report_submitted", "resolution_proposed", "resolution_verified"},
            )
            issue = AppendOnlyEventStore(log_path).project()[0]
            self.assertEqual(issue["lifecycle"], "RESOLVED")
            self.assertEqual(issue["resolution"]["status"], "fix_verified")

    def test_lifecycle_cli_exposes_evidence_and_two_step_reproduction(self):
        with tempfile.TemporaryDirectory() as directory:
            log_path = Path(directory) / "ledger.sqlite3"
            AppendOnlyEventStore(log_path).append(
                report_event("ILE-evidence-lifecycle-report", "run-evidence-lifecycle")
            )
            prefix = ["--db", str(log_path)]
            common = [
                "--provider",
                "provider:test",
                "--route",
                "route:test",
                "--model",
                "model:test",
                "--operation",
                "completion",
                "--workload-class",
                "long_horizon",
                "--stream-mode",
                "sse",
                "--failure-phase",
                "capture",
                "--finish-reason-capture-status",
                "partial",
                "--classification",
                "harness",
            ]
            self.assertEqual(
                actions_main(
                    prefix
                    + ["start-reproduction"]
                    + common
                    + [
                        "--execution-id",
                        "run-reproduction-start",
                        "--summary",
                        "reproduction started",
                        "--recipe-ref",
                        "recipe:fixture",
                        "--independent",
                    ]
                ),
                0,
            )
            self.assertEqual(
                actions_main(
                    prefix
                    + ["record-reproduction"]
                    + common
                    + [
                        "--actor-id",
                        "system:issue-ledger-verifier",
                        "--actor-kind",
                        "system",
                        "--execution-id",
                        "run-reproduction-complete",
                        "--summary",
                        "failure reproduced",
                        "--result",
                        "reproduced",
                        "--recipe-ref",
                        "recipe:fixture",
                        "--receipt-ref",
                        "sha256:reproduction",
                        "--independent",
                        "--verified",
                        "--provenance-producer",
                        "fixture-verifier",
                        "--policy-hash",
                        "policy:test",
                    ]
                ),
                0,
            )
            self.assertEqual(
                actions_main(
                    prefix
                    + ["attach-evidence"]
                    + common
                    + [
                        "--actor-id",
                        "system:issue-ledger-verifier",
                        "--actor-kind",
                        "system",
                        "--execution-id",
                        "run-evidence",
                        "--summary",
                        "deterministic receipt attached",
                        "--receipt-ref",
                        "sha256:deterministic",
                        "--evidence-role",
                        "receipt",
                        "--deterministic-verifier",
                        "--verified",
                        "--accept-for-recommendation",
                        "--provenance-producer",
                        "fixture-verifier",
                        "--policy-hash",
                        "policy:test",
                    ]
                ),
                0,
            )
            events = AppendOnlyEventStore(log_path).events()
            self.assertEqual(
                {event["event_type"] for event in events},
                {
                    "report_submitted",
                    "reproduction_attempted",
                    "reproduction_completed",
                    "evidence_attached",
                },
            )
            issue = AppendOnlyEventStore(log_path).project()[0]
            self.assertEqual(issue["lifecycle"], "ACCEPTED")
            self.assertEqual(issue["evidence_summary"]["independent_reproduction_count"], 1)
            self.assertEqual(issue["evidence_summary"]["deterministic_receipt_count"], 1)

    def test_lifecycle_cli_cannot_self_authorize_resolution(self):
        with tempfile.TemporaryDirectory() as directory:
            log_path = Path(directory) / "ledger.sqlite3"
            args = [
                "--db",
                str(log_path),
                "verify-resolution",
                "--actor-id",
                "agent:claiming-verifier",
                "--actor-kind",
                "agent",
                "--provider",
                "provider:test",
                "--route",
                "route:test",
                "--execution-id",
                "run-self",
                "--summary",
                "agent says it verified the fix",
                "--fix-ref",
                "commit:self",
                "--replay-receipt-ref",
                "sha256:self-replay",
                "--regression-receipt-ref",
                "sha256:self-regression",
                "--provenance-producer",
                "agent",
                "--policy-hash",
                "policy:test",
            ]
            self.assertEqual(actions_main(args), 0)
            issue = AppendOnlyEventStore(log_path).project()[0]
            self.assertNotEqual(issue["lifecycle"], "RESOLVED")

    def test_claude_stopfailure_template_emits_bounded_report_only(self):
        hook = REPO_ROOT / "integrations" / "claude-code-issue-ledger-hook.py"
        with tempfile.TemporaryDirectory() as directory:
            log_path = Path(directory) / "ledger.sqlite3"
            environment = os.environ.copy()
            environment.update(
                {
                    "IRE_ISSUE_LEDGER_DB": str(log_path),
                    "IRE_ISSUE_LEDGER_AGENT_SCRIPT": str(SCRIPT_DIR / "issue_ledger_agent.py"),
                    "IRE_ISSUE_LEDGER_PROVIDER": "provider:test",
                    "IRE_ISSUE_LEDGER_ROUTE": "route:test",
                }
            )
            result = subprocess.run(
                [sys.executable, str(hook)],
                input=json.dumps(
                    {
                        "hook_event_name": "StopFailure",
                        "session_id": "session-hook-test",
                        "error": "rate_limit",
                        "last_assistant_message": "secret response must not be stored",
                    }
                ),
                text=True,
                capture_output=True,
                env=environment,
                check=False,
            )
            self.assertEqual(result.returncode, 0)
            event = AppendOnlyEventStore(log_path).events()[0]
            self.assertEqual(event["event_type"], "report_submitted")
            self.assertEqual(event["payload"]["observation"]["error_code"], "rate_limit")
            self.assertNotIn("secret response", json.dumps(event))

    def test_opencode_plugin_emits_bounded_report_only(self):
        if shutil.which("node") is None:
            self.skipTest("node is required to exercise the OpenCode plugin template")
        plugin_path = REPO_ROOT / "integrations" / "opencode-issue-ledger.js"
        node_source = f"""
import {{ readFile }} from 'node:fs/promises';
const source = await readFile({json.dumps(str(plugin_path))}, 'utf8');
const mod = await import('data:text/javascript,' + encodeURIComponent(source));
process.env.IRE_ISSUE_LEDGER_DB = 'ledger.sqlite3';
process.env.IRE_ISSUE_LEDGER_AGENT_SCRIPT = 'operational/scripts/issue_ledger_agent.py';
process.env.IRE_ISSUE_LEDGER_PROVIDER = 'provider:test';
process.env.IRE_ISSUE_LEDGER_ROUTE = 'route:test';
let captured = null;
globalThis.Bun = {{ spawn(args, options) {{ captured = {{ args, options }}; return {{ exited: Promise.resolve(0) }}; }} }};
const plugin = await mod.InferenceRecommendationEngineIssueLedger();
await plugin.event({{ event: {{ type: 'session.error', properties: {{ sessionID: 'session-001', error: {{ name: 'rate_limit' }}, secretResponse: 'must-not-be-read' }} }} }});
if (!captured) throw new Error('plugin did not spawn sidecar');
if (!captured.args.includes('--error-code') || !captured.args.includes('rate_limit')) throw new Error('bounded error code missing');
if (captured.args.some((value) => String(value).includes('secretResponse') || String(value).includes('must-not-be-read'))) throw new Error('unbounded data captured');
console.log('ok');
"""
        result = subprocess.run(
            ["node", "--input-type=module", "-e", node_source],
            cwd=REPO_ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "ok")


if __name__ == "__main__":
    unittest.main()
