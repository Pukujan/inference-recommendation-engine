#!/usr/bin/env python3
"""Durable local append-only SQLite storage for the Issue Ledger.

The database is deliberately local and standard-library-only. It stores private
operational events in the ignored ``.ire/`` directory, does not contact
providers, run reproductions, or grant recommendation authority. GitHub remains
the source of truth for project plans, issues, and code changes.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from issue_ledger import (
    LedgerError,
    _canonical,
    deduplicate_events,
    export_ire,
    make_report_event,
    reduce_events,
    validate_event,
)

DEFAULT_DB_PATH = Path(".ire/issue-ledger/ledger.sqlite3")
SCHEMA_VERSION = "1"


class StoreError(LedgerError):
    """Raised when the local database cannot safely store or read events."""


class AppendOnlyEventStore:
    """A transactionally locked SQLite event store suitable for local adapters."""

    def __init__(self, path: str | Path = DEFAULT_DB_PATH):
        self.path = Path(path)
        self._initialize()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection: sqlite3.Connection | None = None
        try:
            connection = sqlite3.connect(self.path, timeout=30, isolation_level=None)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA busy_timeout = 30000")
            connection.execute("PRAGMA synchronous = FULL")
            yield connection
        except sqlite3.Error as exc:
            raise StoreError("local SQLite issue ledger operation failed") from exc
        finally:
            if connection is not None:
                connection.close()

    def _initialize(self) -> None:
        try:
            with self._connect() as connection:
                connection.execute("PRAGMA journal_mode = WAL")
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    """CREATE TABLE IF NOT EXISTS ledger_metadata (
                        key TEXT PRIMARY KEY,
                        value TEXT NOT NULL
                    ) WITHOUT ROWID"""
                )
                connection.execute(
                    """CREATE TABLE IF NOT EXISTS ledger_events (
                        event_id TEXT PRIMARY KEY,
                        idempotency_key TEXT UNIQUE,
                        recorded_at TEXT NOT NULL,
                        canonical_json TEXT NOT NULL
                    ) WITHOUT ROWID"""
                )
                connection.execute(
                    """CREATE TRIGGER IF NOT EXISTS ledger_events_no_update
                       BEFORE UPDATE ON ledger_events
                       BEGIN SELECT RAISE(ABORT, 'issue ledger events are append-only'); END"""
                )
                connection.execute(
                    """CREATE TRIGGER IF NOT EXISTS ledger_events_no_delete
                       BEFORE DELETE ON ledger_events
                       BEGIN SELECT RAISE(ABORT, 'issue ledger events are append-only'); END"""
                )
                version = connection.execute(
                    "SELECT value FROM ledger_metadata WHERE key = 'schema_version'"
                ).fetchone()
                if version is None:
                    connection.execute(
                        "INSERT INTO ledger_metadata(key, value) VALUES ('schema_version', ?)",
                        (SCHEMA_VERSION,),
                    )
                elif version["value"] != SCHEMA_VERSION:
                    raise StoreError("unsupported local issue-ledger database schema version")
                connection.commit()
        except sqlite3.Error as exc:
            raise StoreError("cannot initialize local SQLite issue ledger") from exc

    @staticmethod
    def _read_unlocked(connection: sqlite3.Connection) -> list[dict[str, Any]]:
        rows = connection.execute(
            "SELECT canonical_json FROM ledger_events ORDER BY recorded_at, event_id"
        ).fetchall()
        events: list[dict[str, Any]] = []
        for row in rows:
            try:
                value = json.loads(row["canonical_json"])
            except (TypeError, json.JSONDecodeError) as exc:
                raise StoreError("database contains an invalid event record") from exc
            if not isinstance(value, dict):
                raise StoreError("database event record is not an object")
            events.append(value)
        try:
            return deduplicate_events(events)
        except LedgerError as exc:
            raise StoreError(f"database contains conflicting event history: {exc}") from exc

    @staticmethod
    def _identity_keys(event: dict[str, Any]) -> set[str]:
        keys = {event["event_id"]}
        if event.get("idempotency_key"):
            keys.add(str(event["idempotency_key"]))
        return keys

    def events(self) -> list[dict[str, Any]]:
        with self._connect() as connection:
            return self._read_unlocked(connection)

    def append(self, event: dict[str, Any]) -> dict[str, Any]:
        result = self.append_many([event])
        return result[0]

    def append_many(self, events: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
        try:
            incoming = [validate_event(event) for event in events]
        except LedgerError as exc:
            raise StoreError(f"event rejected: {exc}") from exc
        if not incoming:
            return []

        try:
            with self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                existing = self._read_unlocked(connection)
                try:
                    merged = deduplicate_events(existing + incoming)
                except LedgerError as exc:
                    raise StoreError(f"event conflicts with append-only history: {exc}") from exc

                existing_ids = {key for event in existing for key in self._identity_keys(event)}
                new_events: list[dict[str, Any]] = []
                receipts: list[dict[str, Any]] = []
                seen_new: set[str] = set()
                for event in incoming:
                    event_keys = self._identity_keys(event)
                    identity = str(event.get("idempotency_key") or event["event_id"])
                    if event_keys & existing_ids or identity in seen_new:
                        receipts.append(
                            {"event_id": event["event_id"], "stored": False, "reason": "duplicate"}
                        )
                        continue
                    seen_new.add(identity)
                    existing_ids.update(event_keys)
                    new_events.append(event)
                    receipts.append({"event_id": event["event_id"], "stored": True})

                connection.executemany(
                    """INSERT INTO ledger_events(event_id, idempotency_key, recorded_at, canonical_json)
                       VALUES (?, ?, ?, ?)""",
                    [
                        (
                            event["event_id"],
                            event.get("idempotency_key"),
                            event["recorded_at"],
                            _canonical(event),
                        )
                        for event in new_events
                    ],
                )
                if len(merged) != len(existing) + len(new_events):
                    raise StoreError("append integrity check failed")
                connection.commit()
                return receipts
        except sqlite3.Error as exc:
            raise StoreError("cannot commit events to the local SQLite issue ledger") from exc

    def project(
        self,
        policy_hash: str = "policy:issue-ledger-v1",
        trusted_verifier_ids: Iterable[str] | None = None,
    ) -> list[dict[str, Any]]:
        return reduce_events(self.events(), policy_hash, trusted_verifier_ids)

    def ire(
        self,
        policy_hash: str = "policy:issue-ledger-v1",
        window_days: int | None = None,
        as_of: str | None = None,
        trusted_verifier_ids: Iterable[str] | None = None,
    ) -> dict[str, Any]:
        return export_ire(
            self.project(policy_hash, trusted_verifier_ids), policy_hash, window_days, as_of
        )

    def diagnose(
        self,
        issue_id: str | None = None,
        trusted_verifier_ids: Iterable[str] | None = None,
    ) -> list[dict[str, Any]]:
        projections = self.project(trusted_verifier_ids=trusted_verifier_ids)
        if issue_id is None:
            return projections
        return [issue for issue in projections if issue["issue_id"] == issue_id]


def _read_event(path: str) -> dict[str, Any]:
    try:
        raw = sys.stdin.read() if path == "-" else Path(path).read_text(encoding="utf-8")
        value = json.loads(raw)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise StoreError("event input must be one UTF-8 JSON object or stdin (-)") from exc
    if not isinstance(value, dict):
        raise StoreError("event input must be a JSON object")
    return value


def _read_report(path: str) -> dict[str, Any]:
    return _read_event(path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Append and inspect the local SQLite Issue Ledger."
    )
    parser.add_argument(
        "--db",
        default=str(DEFAULT_DB_PATH),
        type=Path,
        help="local SQLite database path (default: .ire/issue-ledger/ledger.sqlite3)",
    )
    parser.add_argument("--policy-hash", default="policy:issue-ledger-v1")
    parser.add_argument("--window-days", type=int, help="optional valid-time window for IRE export")
    parser.add_argument("--as-of", help="window end timestamp; defaults to latest observed event")
    parser.add_argument(
        "--trusted-verifier-id",
        action="append",
        default=[],
        help="authorized verifier actor ID; repeatable",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    append = sub.add_parser("append", help="append one event JSON object")
    append.add_argument("event", help="JSON file path or - for stdin")
    report = sub.add_parser("report", help="normalize and append one agent report packet")
    report.add_argument("packet", help="report packet JSON file or - for stdin")
    sub.add_parser("project", help="print canonical issue projections")
    sub.add_parser("ire", help="print accepted read-only operational inputs")
    diagnose = sub.add_parser("diagnose", help="print all issues or one issue projection")
    diagnose.add_argument("issue_id", nargs="?")
    args = parser.parse_args(argv)
    try:
        store = AppendOnlyEventStore(args.db)
        if args.command == "append":
            output: Any = store.append(_read_event(args.event))
        elif args.command == "report":
            output = store.append(make_report_event(_read_report(args.packet)))
        elif args.command == "project":
            output = store.project(args.policy_hash, args.trusted_verifier_id or None)
        elif args.command == "ire":
            output = store.ire(
                args.policy_hash, args.window_days, args.as_of, args.trusted_verifier_id or None
            )
        else:
            output = store.diagnose(args.issue_id, args.trusted_verifier_id or None)
    except (LedgerError, OSError) as exc:
        print(f"issue-ledger-store: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
