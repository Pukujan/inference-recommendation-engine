#!/usr/bin/env python3
"""Convert secret-free agent telemetry records into Issue Ledger reports.

The converter intentionally imports only bounded request-envelope metadata. It
does not read prompts, responses, credentials, headers, or provider bodies.
Successes are ignored by default: an operational issue report requires a
failure-like outcome or an explicit predicate/counterexample marker.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from typing import Any, Iterable, Iterator

from issue_ledger import LedgerError, make_report_event
from issue_ledger_store import AppendOnlyEventStore, DEFAULT_DB_PATH


OUTCOME_MAP = {
    "success": "success",
    "ok": "success",
    "succeeded": "success",
    "failure": "failure",
    "failed": "failure",
    "timeout": "timeout",
    "cancelled": "cancelled",
    "canceled": "cancelled",
    "interrupted": "cancelled",
    "partial": "partial",
    "rejected": "failure",
}
FAILURE_OUTCOMES = {"failure", "timeout", "cancelled", "partial"}


def _stamp(value: Any) -> str:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _text(value: Any, fallback: str) -> str:
    return str(value).strip() if isinstance(value, str) and value.strip() else fallback


def convert_record(record: dict[str, Any], include_success: bool = False) -> dict[str, Any] | None:
    """Convert one telemetry record, returning None for non-issue successes."""
    if not isinstance(record, dict):
        raise LedgerError("telemetry record must be an object")
    raw_outcome = _text(record.get("outcome"), "unknown").lower()
    outcome = OUTCOME_MAP.get(raw_outcome, "unknown")
    explicit_issue = bool(record.get("predicate_failure") or record.get("counterexample"))
    if outcome not in FAILURE_OUTCOMES and not include_success and not explicit_issue:
        return None
    provider = _text(record.get("provider"), "unknown-provider")
    route = _text(record.get("route") or record.get("model"), "unknown-route")
    model = record.get("model") if isinstance(record.get("model"), str) else None
    operation = _text(record.get("operation"), "unknown-operation")
    workload_class = _text(record.get("workload_class"), "unspecified")
    event_id = _text(record.get("event_id"), _text(record.get("request_id"), "unknown-event"))
    execution_id = _text(record.get("request_id"), event_id)
    observed_at = _stamp(record.get("event_time"))
    error_class = _text(record.get("error_class"), raw_outcome)
    streaming = record.get("streaming")
    stream_mode = "sse" if streaming is True else ("buffered" if streaming is False else "unknown")
    packet = {
        "schema_version": "issue-ledger/report/v1",
        "event_id": "ILE-telemetry-" + event_id,
        "idempotency_key": "telemetry:" + event_id,
        "actor": {
            "id": "agent:" + _text(record.get("agent"), "unknown-agent"),
            "kind": "agent",
            "harness": record.get("source") if isinstance(record.get("source"), str) else None,
        },
        "subject": {
            "provider": provider,
            "route": route,
            "model": model,
            "operation": operation,
            "workload_class": workload_class,
            "stream_mode": stream_mode,
        },
        "execution_id": execution_id,
        "correlation_id": execution_id,
        "observed_at": observed_at,
        "recorded_at": _stamp(record.get("ingested_at") or record.get("event_time")),
        "summary": f"{error_class} observed for {route}",
        "classification": "telemetry",
        "outcome": outcome,
        "observation": {
            "failure_phase": record.get("timeout_phase") or ("capture" if "capture" in error_class else "request"),
            "stream_mode": stream_mode,
            "error_code": record.get("error_code") or error_class,
            "finish_reason_capture_status": record.get("capture_status") or error_class,
            "configuration_hash": record.get("policy_version"),
        },
    }
    event = make_report_event(packet)
    event["payload"].update({
        "telemetry_event_id": event_id,
        "telemetry_source": record.get("source"),
        "request_id": record.get("request_id"),
        "http_status": record.get("http_status"),
        "predicate_failure": explicit_issue,
        "counterexample": bool(record.get("counterexample")),
    })
    return event


def convert_records(records: Iterable[dict[str, Any]], include_success: bool = False) -> Iterator[dict[str, Any]]:
    for record in records:
        converted = convert_record(record, include_success)
        if converted is not None:
            yield converted


def read_jsonl(path: Path) -> Iterator[dict[str, Any]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise LedgerError("telemetry input must be UTF-8 JSONL") from exc
    for line_number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise LedgerError(f"telemetry line {line_number} is invalid JSON") from exc
        if not isinstance(record, dict):
            raise LedgerError(f"telemetry line {line_number} is not an object")
        yield record


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Convert secret-free telemetry into a local Issue Ledger.")
    parser.add_argument("--telemetry", required=True, type=Path)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH, help="local SQLite database (default: .ire/issue-ledger/ledger.sqlite3)")
    parser.add_argument("--include-success", action="store_true", help="retain successes as observations; not recommendation acceptance")
    args = parser.parse_args(argv)
    try:
        events = list(convert_records(read_jsonl(args.telemetry), args.include_success))
        receipts = AppendOnlyEventStore(args.db).append_many(events)
    except (LedgerError, OSError) as exc:
        print(f"telemetry-to-issue-ledger: {exc}", file=sys.stderr)
        return 2
    stored = sum(receipt["stored"] for receipt in receipts)
    print(json.dumps({"records_converted": len(events), "events_stored": stored}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
