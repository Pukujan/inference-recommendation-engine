#!/usr/bin/env python3
"""Import bounded Codex launch receipts into the local Issue Ledger.

Capture sits on the receipt written by a Codex launch, not on a Kilo transcript.
The importer keeps error codes, outcomes, and counts. It drops command text,
tool output, assistant text, and prompt text. A directory scan can run once or
until stopped. It does not modify receipts or install a Kilo config.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path
from typing import Any

from issue_ledger import make_report_event
from issue_ledger_agent import build_packet
from issue_ledger_store import AppendOnlyEventStore

DEFAULT_ROUTE = "cb/gpt-6-astra"
UNKNOWN_STAMP = "2026-09-24T00:00:00Z"
_CODE_RE = re.compile(r'"code"\s*:\s*(\d+)')


def decode_receipt(raw: bytes) -> str:
    """Read a Codex JSONL receipt as UTF-16 or UTF-8."""
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")) or b"\x00" in raw[:4]:
        return raw.decode("utf-16")
    return raw.decode("utf-8")


def parse_events(path: Path) -> list[dict[str, Any]]:
    """Parse receipt events without retaining a reason to copy their text."""
    rows: list[dict[str, Any]] = []
    for line in decode_receipt(path.read_bytes()).splitlines():
        stripped = line.strip().lstrip("\ufeff")
        if not stripped:
            continue
        value = json.loads(stripped)
        if isinstance(value, dict):
            rows.append(value)
    return rows


def read_summary(events_path: Path) -> dict[str, str]:
    """Read the sibling launch summary. Missing keys stay absent."""
    summary_path = events_path.parent / "summary.txt"
    if not summary_path.is_file():
        return {}
    values: dict[str, str] = {}
    for line in summary_path.read_text(encoding="utf-8").splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def thread_id(rows: list[dict[str, Any]]) -> str:
    for row in rows:
        if row.get("type") == "thread.started" and isinstance(row.get("thread_id"), str):
            return str(row["thread_id"])
    return "unknown-thread"


def outcome(rows: list[dict[str, Any]]) -> str:
    kinds = {row.get("type") for row in rows}
    if "turn.failed" in kinds or "error" in kinds:
        return "failure"
    if "turn.completed" in kinds:
        return "success"
    return "partial"


def error_code(rows: list[dict[str, Any]]) -> str | None:
    """Keep a numeric provider code. Do not keep the provider message body."""
    for row in rows:
        if row.get("type") not in {"error", "turn.failed"}:
            continue
        message = row.get("message")
        if not isinstance(message, str):
            continue
        match = _CODE_RE.search(message)
        if match:
            return match.group(1)
    return None


def import_receipt(
    events_path: Path,
    db_path: Path,
    *,
    route_filter: str | None = DEFAULT_ROUTE,
) -> dict[str, Any]:
    """Import one receipt. A non-matching model is skipped, not guessed."""
    summary = read_summary(events_path)
    model = summary.get("model", "")
    if route_filter and model != route_filter:
        return {"stored": False, "skipped": True, "reason": "route-filter"}
    rows = parse_events(events_path)
    identity = thread_id(rows)
    result = outcome(rows)
    code = error_code(rows)
    text = f"Codex receipt {result}"
    if code:
        text = f"Codex receipt failed with code {code}"
    namespace = argparse.Namespace(
        provider=summary.get("provider") or "unknown-provider",
        route=model or "unknown-route",
        model=model or None,
        operation="codex-exec",
        workload_class="long_horizon",
        stream_mode="unknown",
        harness="codex",
        configuration_hash=None,
        environment_hash=None,
        failure_phase="capture" if result == "failure" else None,
        error_code=code,
        finish_reason_capture_status=result,
        timeout_policy=None,
        timeout_seconds=None,
        actor_id=f"agent:codex:{identity}",
        actor_kind="agent",
        execution_id=f"codex-thread:{identity}",
        correlation_id=f"codex-thread:{identity}",
        summary=text,
        classification="provider" if code else "harness",
        outcome=result,
        observed_at=UNKNOWN_STAMP,
        recorded_at=UNKNOWN_STAMP,
        event_id=None,
        idempotency_key=f"codex-thread:{identity}",
        receipt_ref=[f"codex-thread:{identity}"],
        recovery_status=None,
        recovery_action=None,
        recovery_result=None,
        next_action=(
            "Do not replay the rejected thread and do not change routes."
            if result == "failure"
            else None
        ),
    )
    event = make_report_event(build_packet(namespace))
    receipt = AppendOnlyEventStore(db_path).append(event)
    return {"stored": bool(receipt["stored"]), "skipped": False, "event_id": event["event_id"]}


def import_tree(
    root: Path,
    db_path: Path,
    *,
    route_filter: str | None = DEFAULT_ROUTE,
) -> dict[str, int]:
    """Import every receipt under a directory. One bad file does not stop the rest."""
    stored = 0
    skipped = 0
    for events_path in sorted(root.rglob("codex-events.jsonl")):
        try:
            result = import_receipt(events_path, db_path, route_filter=route_filter)
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
            skipped += 1
            continue
        if result.get("skipped"):
            skipped += 1
        elif result.get("stored"):
            stored += 1
    return {"stored": stored, "skipped": skipped}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Import Codex launch receipts into the ledger.")
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--route", default=DEFAULT_ROUTE)
    sub = parser.add_subparsers(dest="command", required=True)
    one = sub.add_parser("import", help="Import one receipt file")
    one.add_argument("--receipt", type=Path, required=True)
    watch = sub.add_parser("watch", help="Scan a receipt directory")
    watch.add_argument("--root", type=Path, required=True)
    watch.add_argument("--once", action="store_true")
    watch.add_argument("--interval", type=float, default=30.0)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    route = args.route or None
    try:
        if args.command == "import":
            result = import_receipt(args.receipt, args.db, route_filter=route)
        else:
            while True:
                result = import_tree(args.root, args.db, route_filter=route)
                if args.once:
                    break
                time.sleep(max(args.interval, 1.0))
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        print(f"codex-receipt-import: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
