#!/usr/bin/env python3
"""Run a runner/TUI command while reporting process-level failures.

The child inherits the current console, so stdout/stderr remain live. This
wrapper has no default inactivity or wall-clock timeout and never kills a
long-running child merely because it is quiet. Provider-specific stream and
capture outcomes should be reported by the host adapter with
issue_ledger_agent.py; this wrapper covers process-level failures only.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import os
from pathlib import Path
import subprocess
import sys
from typing import Any
import uuid

from issue_ledger import LedgerError, make_report_event
from issue_ledger_store import AppendOnlyEventStore, DEFAULT_DB_PATH


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _packet(args: argparse.Namespace, outcome: str, summary: str, error_code: str | None = None) -> dict[str, Any]:
    observed_at = _now()
    stream_mode = args.stream_mode
    observation: dict[str, Any] = {
        "failure_phase": "runner" if outcome != "success" else None,
        "stream_mode": stream_mode,
        "error_code": error_code,
    }
    observation = {key: value for key, value in observation.items() if value is not None}
    return {
        "schema_version": "issue-ledger/report/v1",
        "actor": {"id": args.actor_id, "kind": args.actor_kind, "harness": args.harness},
        "subject": {
            "provider": args.provider,
            "route": args.route,
            "model": args.model,
            "operation": args.operation,
            "workload_class": args.workload_class,
            "stream_mode": stream_mode,
        },
        "execution_id": args.execution_id or f"run:wrapper-{os.getpid()}-{uuid.uuid4().hex[:12]}",
        "summary": summary,
        "classification": "harness",
        "outcome": outcome,
        "observed_at": observed_at,
        "recorded_at": observed_at,
        "observation": observation,
    }


def _report(args: argparse.Namespace, outcome: str, summary: str, error_code: str | None = None) -> dict[str, Any]:
    event = make_report_event(_packet(args, outcome, summary, error_code))
    return AppendOnlyEventStore(args.db).append(event)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a live child process and report process failures to the Issue Ledger.")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH, help="local SQLite database (default: .ire/issue-ledger/ledger.sqlite3)")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="run a child command; place the command after --")
    run.add_argument("--actor-id", default="service:issue-ledger-wrapper")
    run.add_argument("--actor-kind", default="service", choices=["human", "agent", "service", "importer", "system"])
    run.add_argument("--harness", default="process-wrapper")
    run.add_argument("--provider", default="unknown-provider")
    run.add_argument("--route", default="unknown-route")
    run.add_argument("--model")
    run.add_argument("--operation", default="agent-run")
    run.add_argument("--workload-class", default="long_horizon")
    run.add_argument("--stream-mode", choices=["sse", "buffered", "unknown"], default="unknown")
    run.add_argument("--execution-id")
    run.add_argument("--record-success", action="store_true", help="also record successful process completion as report_only")
    run.add_argument("child", nargs=argparse.REMAINDER)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.command != "run":
        parser.error("a command is required")
    child = list(args.child)
    if child and child[0] == "--":
        child = child[1:]
    if not child:
        parser.error("run requires a child command after --")
    try:
        try:
            completed = subprocess.run(child, check=False)
            return_code = int(completed.returncode)
        except FileNotFoundError:
            _report(args, "failure", f"child command was not found: {child[0]}", "command_not_found")
            return 127
        if return_code != 0:
            receipt = _report(args, "failure", f"child process exited with status {return_code}", f"process_exit:{return_code}")
            print(f"issue-ledger-wrapper: failure reported stored={receipt['stored']}", file=sys.stderr)
        elif args.record_success:
            receipt = _report(args, "success", "child process completed successfully")
            print(f"issue-ledger-wrapper: success observed stored={receipt['stored']}", file=sys.stderr)
        return return_code
    except (LedgerError, OSError) as exc:
        print(f"issue-ledger-wrapper: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
