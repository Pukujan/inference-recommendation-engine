"""Provider-error capture on the Astra launcher root span (issue #41, #40 M0.6 context).

The launcher helper must turn receipt-only provider rejections (HTTP 400 code 11133 and other coded
4xx errors) into root-span attributes and events, deduplicated per request id, without treating
tool output that merely mentions a code as an error.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PC_DIR = REPO_ROOT / "operational" / "telemetry" / "pc"
HELPER = PC_DIR / "astra_otel.py"
FIXTURE = PC_DIR / "fixtures" / "receipt-provider-400"

_spec = importlib.util.spec_from_file_location("astra_otel", HELPER)
assert _spec and _spec.loader
astra_otel = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(astra_otel)


class ProviderErrorScanTests(unittest.TestCase):
    def scan(self) -> tuple[list[dict], int]:
        return astra_otel.scan_provider_errors(
            [str(FIXTURE / "codex-events.jsonl"), str(FIXTURE / "codex-stderr.txt")]
        )

    def test_utf16_events_yield_one_deduplicated_11133(self) -> None:
        errs, total = self.scan()
        coded = [e for e in errs if e["code"] == 11133]
        self.assertEqual(len(coded), 1)
        e = coded[0]
        self.assertEqual(e["http_status"], 400)
        self.assertEqual(e["type"], "model_param_invalid")
        self.assertEqual(e["request_id"], "00000000000000000000000000fixture")
        self.assertEqual(e["occurrences"], 2)  # error + turn.failed carry the same request
        self.assertEqual(e["source"], "codex-events.jsonl:4")
        self.assertEqual(e["request_shape"], "msgs=37 tools=13")
        self.assertGreaterEqual(total, 3)

    def test_tool_output_mentioning_a_code_is_ignored(self) -> None:
        errs, _ = self.scan()
        self.assertFalse(any(e["source"] == "codex-events.jsonl:3" for e in errs))

    def test_stderr_status_line_is_captured_and_noise_is_not(self) -> None:
        errs, _ = self.scan()
        text = [e for e in errs if e["source"].startswith("codex-stderr.txt")]
        self.assertEqual(len(text), 1)
        self.assertEqual(text[0]["http_status"], 429)
        self.assertEqual(text[0]["type"], "rate_limit_exceeded")
        self.assertEqual(text[0]["request_id"], "req_fixture_429_abcdef")

    def test_root_span_attributes_and_events(self) -> None:
        errs, total = self.scan()
        attrs, events = astra_otel.provider_error_attrs(errs, total)
        self.assertEqual(attrs["provider.error.count"], 2)
        self.assertEqual(attrs["provider.error.codes"], "11133")
        self.assertIn("00000000000000000000000000fixture", attrs["provider.request_ids"])
        self.assertEqual(len(events), 2)
        self.assertEqual(astra_otel.provider_error_attrs([], 0), ({"provider.error.count": 0}, []))

    def test_end_dry_run_puts_errors_on_root_span_without_network(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            receipt = Path(tmp)
            for name in ("codex-events.jsonl", "codex-stderr.txt"):
                (receipt / name).write_bytes((FIXTURE / name).read_bytes())
            out = subprocess.run(
                [
                    sys.executable,
                    str(HELPER),
                    "end",
                    "--dry-run",
                    "--receipt-dir",
                    str(receipt),
                    "--exit-code",
                    "1",
                    "--start-ns",
                    "1",
                    "--trace-id",
                    "a" * 32,
                    "--span-id",
                    "b" * 16,
                ],
                capture_output=True,
                text=True,
                check=True,
                env={"OTEL_EXPORTER_OTLP_ENDPOINT": "http://127.0.0.1:9"},
            )
        root = json.loads(out.stdout)
        attrs = {a["key"]: next(iter(a["value"].values())) for a in root["attributes"]}
        self.assertEqual(attrs["provider.error.code"], "11133")
        self.assertEqual(root["status"]["code"], 2)
        self.assertIn("11133", root["status"]["message"])
        self.assertEqual([e["name"] for e in root["events"]], ["provider.error"] * 2)
        self.assertIn("run.stderr_tail", attrs)


if __name__ == "__main__":
    unittest.main()
