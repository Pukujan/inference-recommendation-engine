"""Property tests for Codex receipt capture.

Capture belongs at the Codex receipt, not in a Kilo transcript. These tests
state the relations first. They fail until the importer exists.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_DIR = REPO_ROOT / "operational" / "scripts"
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from codex_receipt_import import import_receipt, import_tree  # noqa: E402
from issue_ledger_store import AppendOnlyEventStore  # noqa: E402

OWNER_ROUTE = "cb/gpt-6-astra"
SECRET = "SECRET_PROMPT_MUST_NOT_BE_STORED"


def events(thread_id: str, *, failed: bool, wording: str = "draft answer") -> list[dict]:
    rows = [
        {"type": "thread.started", "thread_id": thread_id},
        {"type": "turn.started"},
        {
            "type": "item.completed",
            "item": {
                "type": "agent_message",
                "text": f"{wording} {SECRET}",
            },
        },
        {
            "type": "item.completed",
            "item": {
                "type": "command_execution",
                "command": f"Get-Content {SECRET}",
                "aggregated_output": SECRET,
                "exit_code": 0,
                "status": "completed",
            },
        },
    ]
    if failed:
        rows.append(
            {
                "type": "error",
                "message": json.dumps(
                    {
                        "code": 11133,
                        "extError": {"code": "model_param_invalid"},
                        "prompt": SECRET,
                    }
                ),
            }
        )
        rows.append({"type": "turn.failed", "message": SECRET})
    else:
        rows.append({"type": "turn.completed"})
    return rows


def write_receipt(folder: Path, rows: list[dict], *, model: str, encoding: str) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    events_path = folder / "codex-events.jsonl"
    text = "\n".join(json.dumps(row) for row in rows) + "\n"
    events_path.write_bytes(text.encode(encoding))
    (folder / "summary.txt").write_text(
        f"exit_code={'1' if any(row.get('type') == 'turn.failed' for row in rows) else '0'}\n"
        f"model={model}\n"
        "provider=configured-byok\n"
        "sandbox=workspace-write\n",
        encoding="utf-8",
    )
    return events_path


class CodexReceiptImportTests(unittest.TestCase):
    def test_same_thread_imports_once(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            receipt = write_receipt(
                root / "run-a",
                events("thread-1", failed=True),
                model=OWNER_ROUTE,
                encoding="utf-8",
            )
            db = root / "ledger.sqlite3"
            first = import_receipt(receipt, db, route_filter=OWNER_ROUTE)
            second = import_receipt(receipt, db, route_filter=OWNER_ROUTE)
            self.assertTrue(first["stored"])
            self.assertFalse(second["stored"])
            self.assertEqual(len(AppendOnlyEventStore(db).events()), 1)

    def test_utf16_keeps_error_code_and_drops_prompt_text(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            receipt = write_receipt(
                root / "run-b",
                events("thread-2", failed=True, wording="other wording"),
                model=OWNER_ROUTE,
                encoding="utf-16",
            )
            db = root / "ledger.sqlite3"
            import_receipt(receipt, db, route_filter=OWNER_ROUTE)
            stored = json.dumps(AppendOnlyEventStore(db).events())
            self.assertNotIn(SECRET, stored)
            self.assertIn("11133", stored)
            self.assertEqual(AppendOnlyEventStore(db).events()[0]["payload"]["outcome"], "failure")

    def test_wording_change_does_not_change_outcome(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            left = write_receipt(
                root / "left",
                events("thread-3", failed=False, wording="alpha"),
                model=OWNER_ROUTE,
                encoding="utf-8",
            )
            right = write_receipt(
                root / "right",
                events("thread-3", failed=False, wording="beta"),
                model=OWNER_ROUTE,
                encoding="utf-16",
            )
            db = root / "ledger.sqlite3"
            import_receipt(left, db, route_filter=OWNER_ROUTE)
            import_receipt(right, db, route_filter=OWNER_ROUTE)
            rows = AppendOnlyEventStore(db).events()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["payload"]["outcome"], "success")
            self.assertNotIn(SECRET, json.dumps(rows))

    def test_non_matching_model_is_skipped(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_receipt(
                root / "other",
                events("thread-4", failed=True),
                model="other-model",
                encoding="utf-8",
            )
            write_receipt(
                root / "owner",
                events("thread-5", failed=False),
                model=OWNER_ROUTE,
                encoding="utf-8",
            )
            db = root / "ledger.sqlite3"
            imported = import_tree(root, db, route_filter=OWNER_ROUTE)
            self.assertEqual(imported["stored"], 1)
            self.assertEqual(imported["skipped"], 1)
            self.assertEqual(len(AppendOnlyEventStore(db).events()), 1)
            self.assertNotIn(SECRET, json.dumps(AppendOnlyEventStore(db).events()))

    def test_open_receipt_is_partial_and_source_is_unchanged(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            folder = root / "open"
            receipt = write_receipt(
                folder,
                [{"type": "thread.started", "thread_id": "thread-6"}],
                model=OWNER_ROUTE,
                encoding="utf-8",
            )
            before = receipt.read_bytes()
            db = root / "ledger.sqlite3"
            import_receipt(receipt, db, route_filter=OWNER_ROUTE)
            self.assertEqual(receipt.read_bytes(), before)
            self.assertEqual(AppendOnlyEventStore(db).events()[0]["payload"]["outcome"], "partial")


if __name__ == "__main__":
    unittest.main()
