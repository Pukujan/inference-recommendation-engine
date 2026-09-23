#!/usr/bin/env python3
"""Small adapter-facing CLI for agent and runner issue reporting.

This is intentionally a sidecar, not a host-specific plugin. Hermes,
OpenCode, a TUI, or another runner can invoke it at request completion or
capture failure. It accepts bounded execution metadata only; it never reads
prompts, responses, credentials, headers, or provider bodies.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from typing import Any

from issue_ledger import LedgerError, make_report_event
from issue_ledger_store import AppendOnlyEventStore, DEFAULT_DB_PATH


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _add_if_present(target: dict[str, Any], key: str, value: Any) -> None:
    if value is not None:
        target[key] = value


def build_packet(args: argparse.Namespace) -> dict[str, Any]:
    """Build one closed report packet from CLI metadata."""
    observed_at = args.observed_at or _now()
    recorded_at = args.recorded_at or _now()
    subject = {
        "provider": args.provider,
        "route": args.route,
        "model": args.model,
        "operation": args.operation,
        "workload_class": args.workload_class,
        "stream_mode": args.stream_mode,
    }
    _add_if_present(subject, "agent_harness", args.harness)
    _add_if_present(subject, "configuration_hash", args.configuration_hash)
    _add_if_present(subject, "environment_hash", args.environment_hash)
    observation = {
        "failure_phase": args.failure_phase,
        "stream_mode": args.stream_mode,
        "error_code": args.error_code,
        "finish_reason_capture_status": args.finish_reason_capture_status,
        "timeout_policy": args.timeout_policy,
        "timeout_seconds": args.timeout_seconds,
    }
    for field in ("failure_phase", "stream_mode", "error_code", "finish_reason_capture_status", "timeout_policy", "timeout_seconds"):
        if observation[field] is None:
            observation.pop(field)
    _add_if_present(observation, "configuration_hash", args.configuration_hash)
    _add_if_present(observation, "environment_hash", args.environment_hash)
    packet: dict[str, Any] = {
        "schema_version": "issue-ledger/report/v1",
        "actor": {
            "id": args.actor_id,
            "kind": args.actor_kind,
            "harness": args.harness,
        },
        "subject": subject,
        "execution_id": args.execution_id,
        "correlation_id": args.correlation_id or args.execution_id,
        "summary": args.summary,
        "classification": args.classification,
        "outcome": args.outcome,
        "observed_at": observed_at,
        "recorded_at": recorded_at,
        "observation": observation,
    }
    _add_if_present(packet, "event_id", args.event_id)
    _add_if_present(packet, "idempotency_key", args.idempotency_key)
    if args.receipt_ref:
        packet["receipt_refs"] = args.receipt_ref
    if args.recovery_status is not None:
        packet["attempted_recovery"] = {
            "status": args.recovery_status,
            "action": args.recovery_action,
            "result": args.recovery_result,
        }
    _add_if_present(packet, "proposed_next_action", args.next_action)
    return packet


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Append a bounded agent observation to the local Issue Ledger.")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH, help="local SQLite database (default: .ire/issue-ledger/ledger.sqlite3)")
    sub = parser.add_subparsers(dest="command", required=True)
    report = sub.add_parser("report", help="report one agent/runner observation")
    report.add_argument("--packet", type=Path, help="validated issue-ledger/report/v1 JSON packet")
    report.add_argument("--actor-id", default="agent:unknown")
    report.add_argument("--actor-kind", default="agent", choices=["human", "agent", "service", "importer", "system"])
    report.add_argument("--harness")
    report.add_argument("--provider", default="unknown-provider")
    report.add_argument("--route", default="unknown-route")
    report.add_argument("--model")
    report.add_argument("--operation", default="unknown-operation")
    report.add_argument("--workload-class", default="unspecified")
    report.add_argument("--stream-mode", choices=["sse", "buffered", "unknown"], default="unknown")
    report.add_argument("--execution-id")
    report.add_argument("--correlation-id")
    report.add_argument("--summary")
    report.add_argument("--classification", default="unknown", choices=["protocol", "client", "provider", "model_behavior", "harness", "telemetry", "evaluation", "unknown"])
    report.add_argument("--outcome", default="unknown", choices=["success", "failure", "timeout", "cancelled", "partial", "unknown"])
    report.add_argument("--observed-at")
    report.add_argument("--recorded-at")
    report.add_argument("--event-id")
    report.add_argument("--idempotency-key")
    report.add_argument("--failure-phase")
    report.add_argument("--error-code")
    report.add_argument("--finish-reason-capture-status")
    report.add_argument("--timeout-policy")
    report.add_argument("--timeout-seconds", type=float)
    report.add_argument("--configuration-hash")
    report.add_argument("--environment-hash")
    report.add_argument("--receipt-ref", action="append", default=[])
    report.add_argument("--recovery-status", choices=["not_attempted", "attempted", "succeeded", "failed", "unknown"])
    report.add_argument("--recovery-action")
    report.add_argument("--recovery-result")
    report.add_argument("--next-action")
    return parser


def _read_packet(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise LedgerError("packet must be one UTF-8 JSON object") from exc
    if not isinstance(value, dict):
        raise LedgerError("packet must be a JSON object")
    return value


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.command != "report":
        parser.error("a command is required")
    try:
        if args.packet is not None:
            packet = _read_packet(args.packet)
        else:
            if not args.execution_id or not args.summary:
                parser.error("report requires --execution-id and --summary when --packet is omitted")
            packet = build_packet(args)
        event = make_report_event(packet)
        receipt = AppendOnlyEventStore(args.db).append(event)
    except (LedgerError, OSError) as exc:
        print(f"issue-ledger-agent: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({"event": event["event_id"], "stored": receipt["stored"], "lifecycle": "CANDIDATE"}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
