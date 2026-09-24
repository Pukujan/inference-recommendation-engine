#!/usr/bin/env python3
"""Append evidence-bound lifecycle actions to the local Issue Ledger.

These commands record what an adapter actually did; they do not execute a
reproduction, authenticate a verifier, or promote an issue by themselves.
Projection authority remains the configured trusted-verifier allowlist.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from issue_ledger import LedgerError, _hash, validate_event
from issue_ledger_store import DEFAULT_DB_PATH, AppendOnlyEventStore


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _optional(target: dict[str, Any], key: str, value: Any) -> None:
    if value is not None:
        target[key] = value


def _subject(args: argparse.Namespace) -> dict[str, Any]:
    subject = {
        "provider": args.provider,
        "route": args.route,
        "model": args.model,
        "operation": args.operation,
        "workload_class": args.workload_class,
        "stream_mode": args.stream_mode,
    }
    _optional(subject, "agent_harness", args.harness)
    _optional(subject, "configuration_hash", args.configuration_hash)
    _optional(subject, "environment_hash", args.environment_hash)
    return subject


def _observation(args: argparse.Namespace) -> dict[str, Any]:
    observation = {
        "failure_phase": args.failure_phase,
        "stream_mode": args.stream_mode,
        "error_code": args.error_code,
        "finish_reason_capture_status": args.finish_reason_capture_status,
        "configuration_hash": args.configuration_hash,
        "environment_hash": args.environment_hash,
    }
    return {key: value for key, value in observation.items() if value is not None}


def _provenance(args: argparse.Namespace, required: bool = False) -> dict[str, str] | None:
    producer = getattr(args, "provenance_producer", None)
    policy_hash = getattr(args, "policy_hash", None)
    if producer is None and policy_hash is None:
        if required:
            raise LedgerError("trusted evidence requires --provenance-producer and --policy-hash")
        return None
    if not producer or not policy_hash:
        raise LedgerError("--provenance-producer and --policy-hash must be provided together")
    return {"producer": producer, "policy_hash": policy_hash}


def build_action_event(args: argparse.Namespace) -> dict[str, Any]:
    now = _now()
    observed_at = args.observed_at or now
    recorded_at = args.recorded_at or now
    subject = _subject(args)
    action = args.command
    event_type = {
        "attach-evidence": "evidence_attached",
        "start-reproduction": "reproduction_attempted",
        "propose-fix": "resolution_proposed",
        "record-reproduction": "reproduction_completed",
        "verify-resolution": "resolution_verified",
        "counterexample": "issue_retracted",
        "reopen": "issue_reopened",
    }[action]
    payload: dict[str, Any] = {
        "summary": args.summary,
        "classification": args.classification,
        "execution_id": args.execution_id,
        "observed_at": observed_at,
        "outcome": getattr(args, "outcome", "unknown"),
        "subject": subject,
        "observation": _observation(args),
    }
    top_level: dict[str, Any] = {}
    if action == "attach-evidence":
        payload.update(
            {
                "receipt_ref": args.receipt_ref,
                "evidence_role": args.evidence_role,
                "deterministic_verifier": args.deterministic_verifier,
                "verified": args.verified,
                "accept_for_recommendation": args.accept_for_recommendation,
            }
        )
        top_level["evidence_refs"] = [
            {
                "ref_id": args.receipt_ref,
                "role": args.evidence_role,
                "content_hash": args.content_hash,
            }
        ]
        provenance = _provenance(
            args, args.deterministic_verifier or args.accept_for_recommendation
        )
        if provenance is not None:
            top_level["provenance"] = provenance
    elif action == "start-reproduction":
        payload.update(
            {
                "recipe_ref": args.recipe_ref,
                "independent": args.independent,
                "result": "pending",
            }
        )
    elif action == "propose-fix":
        payload["fix_ref"] = args.fix_ref
    elif action == "record-reproduction":
        payload.update(
            {
                "result": args.result,
                "independent": args.independent,
                "verified": args.verified,
                "recipe_ref": args.recipe_ref,
                "receipt_ref": args.receipt_ref,
            }
        )
        provenance = _provenance(args, args.verified)
        if provenance is not None:
            top_level["provenance"] = provenance
    elif action == "verify-resolution":
        if args.replay_receipt_ref == args.regression_receipt_ref:
            raise LedgerError("replay and regression receipts must be distinct")
        payload.update(
            {
                "fix_ref": args.fix_ref,
                "verified": True,
                "accept_for_recommendation": args.accept_for_recommendation,
                "replay_receipt_ref": args.replay_receipt_ref,
                "regression_receipt_ref": args.regression_receipt_ref,
            }
        )
        top_level["evidence_refs"] = [
            {"ref_id": args.replay_receipt_ref, "role": "reproduction"},
            {"ref_id": args.regression_receipt_ref, "role": "reproduction"},
        ]
        top_level["provenance"] = {
            "producer": args.provenance_producer,
            "policy_hash": args.policy_hash,
        }
    elif action == "counterexample":
        payload.update(
            {
                "counterexample": True,
                "verified": True,
                "counterexample_ref": args.counterexample_ref,
            }
        )
        top_level["provenance"] = _provenance(args, True)
        event_type = "issue_retracted"
    elif action == "reopen":
        payload.update({"verified": True, "reopen_receipt_ref": args.reopen_receipt_ref})
        top_level["provenance"] = _provenance(args, True)
        event_type = "issue_reopened"
    else:
        raise LedgerError(f"unsupported action: {action}")

    identity = {
        "event_type": event_type,
        "actor": {"id": args.actor_id, "kind": args.actor_kind, "harness": args.harness},
        "subject": subject,
        "execution_id": args.execution_id,
        "observed_at": observed_at,
        "payload": payload,
    }
    event_id = args.event_id or "ILE-action-" + _hash(identity).removeprefix("sha256:")[:24]
    idempotency_key = args.idempotency_key or "action:" + _hash(identity).removeprefix("sha256:")
    event = {
        "schema_version": "issue-ledger/v1",
        "event_id": event_id,
        "event_type": event_type,
        "recorded_at": recorded_at,
        "valid_at": {"from": observed_at, "to": None},
        "known_at": recorded_at,
        "actor": {"id": args.actor_id, "kind": args.actor_kind, "harness": args.harness},
        "correlation_id": args.correlation_id or args.execution_id,
        "idempotency_key": idempotency_key,
        "subject_refs": [f"{args.provider}:{args.route}"],
        "payload": payload,
        **top_level,
    }
    return validate_event(event)


def _add_common(
    parser: argparse.ArgumentParser, actor_id: str = "agent:unknown", actor_kind: str = "agent"
) -> None:
    parser.add_argument("--actor-id", default=actor_id)
    parser.add_argument(
        "--actor-kind",
        default=actor_kind,
        choices=["human", "agent", "service", "importer", "system"],
    )
    parser.add_argument("--harness")
    parser.add_argument("--provider", required=True)
    parser.add_argument("--route", required=True)
    parser.add_argument("--model")
    parser.add_argument("--operation", default="completion")
    parser.add_argument("--workload-class", default="unspecified")
    parser.add_argument("--stream-mode", choices=["sse", "buffered", "unknown"], default="unknown")
    parser.add_argument("--execution-id", required=True)
    parser.add_argument("--correlation-id")
    parser.add_argument("--summary", required=True)
    parser.add_argument(
        "--classification",
        default="unknown",
        choices=[
            "protocol",
            "client",
            "provider",
            "model_behavior",
            "harness",
            "telemetry",
            "evaluation",
            "unknown",
        ],
    )
    parser.add_argument(
        "--outcome",
        default="unknown",
        choices=["success", "failure", "timeout", "cancelled", "partial", "unknown"],
    )
    parser.add_argument("--observed-at")
    parser.add_argument("--recorded-at")
    parser.add_argument("--event-id")
    parser.add_argument("--idempotency-key")
    parser.add_argument("--failure-phase")
    parser.add_argument("--error-code")
    parser.add_argument("--finish-reason-capture-status")
    parser.add_argument("--configuration-hash")
    parser.add_argument("--environment-hash")
    parser.add_argument("--provenance-producer")
    parser.add_argument("--policy-hash")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Append an evidence-bound Issue Ledger lifecycle action."
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=DEFAULT_DB_PATH,
        help="local SQLite database (default: .ire/issue-ledger/ledger.sqlite3)",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    proposed = sub.add_parser("propose-fix", help="record a proposed workaround or fix")
    _add_common(proposed)
    proposed.add_argument("--fix-ref", required=True)

    evidence = sub.add_parser(
        "attach-evidence", help="attach a bounded receipt or artifact reference"
    )
    _add_common(evidence)
    evidence.add_argument("--receipt-ref", required=True)
    evidence.add_argument(
        "--evidence-role",
        choices=["receipt", "trace", "artifact", "source", "reproduction", "counterexample"],
        default="receipt",
    )
    evidence.add_argument("--content-hash")
    evidence.add_argument("--deterministic-verifier", action="store_true")
    evidence.add_argument("--verified", action="store_true")
    evidence.add_argument("--accept-for-recommendation", action="store_true")

    started = sub.add_parser(
        "start-reproduction", help="record that a declared reproduction recipe was started"
    )
    _add_common(started)
    started.add_argument("--recipe-ref", required=True)
    started.add_argument("--independent", action="store_true")

    reproduced = sub.add_parser(
        "record-reproduction", help="record the result of an already-run reproduction recipe"
    )
    _add_common(reproduced)
    reproduced.add_argument(
        "--result", choices=["reproduced", "not_reproduced", "inconclusive"], required=True
    )
    reproduced.add_argument("--recipe-ref", required=True)
    reproduced.add_argument("--receipt-ref", required=True)
    reproduced.add_argument("--independent", action="store_true")
    reproduced.add_argument("--verified", action="store_true")

    verified = sub.add_parser(
        "verify-resolution", help="record replay plus regression verification"
    )
    _add_common(verified, "system:issue-ledger-verifier", "system")
    verified.add_argument("--fix-ref", required=True)
    verified.add_argument("--replay-receipt-ref", required=True)
    verified.add_argument("--regression-receipt-ref", required=True)
    verified.add_argument("--accept-for-recommendation", action="store_true")

    counter = sub.add_parser(
        "counterexample", help="record a verifier-marked contradictory receipt"
    )
    _add_common(counter)
    counter.add_argument("--counterexample-ref", required=True)

    reopen = sub.add_parser("reopen", help="record a verifier-marked regression after resolution")
    _add_common(reopen)
    reopen.add_argument("--reopen-receipt-ref", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        event = build_action_event(args)
        receipt = AppendOnlyEventStore(args.db).append(event)
    except (LedgerError, OSError) as exc:
        print(f"issue-ledger-actions: {exc}", file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "event": event["event_id"],
                "event_type": event["event_type"],
                "stored": receipt["stored"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
