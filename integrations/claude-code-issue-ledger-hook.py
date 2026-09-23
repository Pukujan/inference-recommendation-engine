#!/usr/bin/env python3
"""Claude Code StopFailure hook adapter for the local Issue Ledger.

The hook reads only bounded hook metadata from stdin. Configure the absolute
path to this script (or place it under the project) and set:
  IRE_ISSUE_LEDGER_DB
  IRE_ISSUE_LEDGER_AGENT_SCRIPT

It intentionally ignores transcript_path, last_assistant_message, prompts,
and error_details. A hook failure is non-blocking and never changes Claude's
session decision.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys


def main() -> int:
    try:
        event = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, OSError, UnicodeError):
        return 0
    if not isinstance(event, dict) or event.get("hook_event_name") != "StopFailure":
        return 0

    db = os.environ.get("IRE_ISSUE_LEDGER_DB", ".ire/issue-ledger/ledger.sqlite3")
    agent_script = os.environ.get("IRE_ISSUE_LEDGER_AGENT_SCRIPT")
    if not db or not agent_script:
        return 0
    session_id = event.get("session_id") if isinstance(event.get("session_id"), str) else "unknown-session"
    error = event.get("error") if isinstance(event.get("error"), str) else "unknown"
    python = os.environ.get("IRE_ISSUE_LEDGER_PYTHON", sys.executable)
    args = [
        python,
        str(Path(agent_script)),
        "--db", db,
        "report",
        "--actor-id", f"agent:claude-code:{session_id}",
        "--actor-kind", "agent",
        "--harness", "claude-code",
        "--provider", os.environ.get("IRE_ISSUE_LEDGER_PROVIDER", "anthropic"),
        "--route", os.environ.get("IRE_ISSUE_LEDGER_ROUTE", "claude-code"),
        "--operation", "agent-session",
        "--workload-class", os.environ.get("IRE_ISSUE_LEDGER_WORKLOAD_CLASS", "long_horizon"),
        "--stream-mode", os.environ.get("IRE_ISSUE_LEDGER_STREAM_MODE", "unknown"),
        "--execution-id", f"claude-session:{session_id}",
        "--summary", "Claude Code reported an API failure",
        "--classification", "provider",
        "--outcome", "failure",
        "--failure-phase", "request",
        "--error-code", error,
    ]
    try:
        subprocess.run(args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
    except (OSError, ValueError):
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
