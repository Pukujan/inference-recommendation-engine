#!/usr/bin/env python3
"""Tiny, daemon-less OTLP/HTTP-JSON root-span emitter for agent launchers (stdlib only).

  begin  -> prints JSON {trace_id, span_id, traceparent, start_ns}; also emits a short
            "<name>.start" marker span (child of the root) so a run that is hard-killed
            before `end` still leaves evidence.
  end    -> emits the root span (start..now) with exit code, killed/timeout flag and a
            redacted stderr tail.
  test   -> emits one self-contained test span; prints its trace_id.
  scan   -> prints the provider errors found in receipt files as JSON (no network).

`end` also scans the run's receipt files (--events-file, --stderr-file, --launcher-error-file, or all
three from --receipt-dir) for provider errors (HTTP status, provider code such as 11133, error type,
message, request id) and attaches them to the root span as provider.error.* attributes plus one
`provider.error` span event per distinct error. Codex's own spans carry no response body, and the span
of the failing request is often lost when the process dies before its exporter flushes, so the
receipt is the only durable source of the code.

Never raises into the caller: network errors are reported on stderr and exit code stays 0.
Endpoint: $OTEL_EXPORTER_OTLP_TRACES_ENDPOINT or $OTEL_EXPORTER_OTLP_ENDPOINT + /v1/traces.
Resource attributes: $OTEL_RESOURCE_ATTRIBUTES (k=v,k=v) + service.name.
"""

import argparse
import json
import os
import re
import secrets
import sys
import time
import urllib.request

REDACT = [
    (
        re.compile(r"(sk-lf-|pk-lf-|sk-|ghp_|gho_|ghu_|ghs_|ghr_|github_pat_)[A-Za-z0-9_\-]{6,}"),
        "***REDACTED***",
    ),
    (re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=\-]{8,}"), r"\1 ***REDACTED***"),
    (re.compile(r"(?i)((?:api[_-]?key|token|secret|password)\s*[=:]\s*)\S+"), r"\1***REDACTED***"),
]
# Windows NTSTATUS / conventional codes that mean "killed or interrupted", not "failed normally".
KILLED = {
    -1073741510: "CTRL_C/CTRL_CLOSE (0xC000013A)",
    3221225786: "CTRL_C/CTRL_CLOSE (0xC000013A)",
    -1: "terminated (-1)",
    130: "SIGINT (130)",
    137: "SIGKILL (137)",
    143: "SIGTERM (143)",
    258: "WAIT_TIMEOUT (258)",
    124: "timeout (124)",
}


def redact(s):
    for rx, rep in REDACT:
        s = rx.sub(rep, s)
    return s


def endpoint():
    e = os.environ.get("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT")
    if e:
        return e
    base = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "http://100.93.66.34:4318").rstrip("/")
    return base + "/v1/traces"


def resource_attrs(service):
    attrs = {}
    for part in os.environ.get("OTEL_RESOURCE_ATTRIBUTES", "").split(","):
        if "=" in part:
            k, v = part.split("=", 1)
            attrs[k.strip()] = urllib.request.unquote(v.strip())
    attrs["service.name"] = service
    attrs.setdefault("host.name", os.environ.get("COMPUTERNAME", ""))
    return attrs


def kv(d):
    out = []
    for k, v in d.items():
        if v is None:
            continue
        if isinstance(v, bool):
            val = {"boolValue": v}
        elif isinstance(v, int):
            val = {"intValue": str(v)}
        elif isinstance(v, float):
            val = {"doubleValue": v}
        else:
            val = {"stringValue": str(v)}
        out.append({"key": k, "value": val})
    return out


def send(service, spans, timeout=5.0):
    body = {
        "resourceSpans": [
            {
                "resource": {"attributes": kv(resource_attrs(service))},
                "scopeSpans": [{"scope": {"name": "astra_otel", "version": "1"}, "spans": spans}],
            }
        ]
    }
    req = urllib.request.Request(
        endpoint(),
        data=json.dumps(body).encode(),
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status
    except Exception as e:  # never break the launcher
        print(f"[astra_otel] export failed: {e}", file=sys.stderr)
        return None


def span(
    trace_id,
    span_id,
    parent,
    name,
    start_ns,
    end_ns,
    attrs,
    error=False,
    msg=None,
    kind=1,
    events=None,
):
    s = {
        "traceId": trace_id,
        "spanId": span_id,
        "name": name,
        "kind": kind,
        "startTimeUnixNano": str(start_ns),
        "endTimeUnixNano": str(end_ns),
        "attributes": kv(attrs),
        "status": {"code": 2 if error else 1},
    }
    if parent:
        s["parentSpanId"] = parent
    if msg:
        s["status"]["message"] = msg
    if events:
        s["events"] = [
            {"timeUnixNano": str(ts), "name": ev_name, "attributes": kv(ev_attrs)}
            for ts, ev_name, ev_attrs in events
        ]
    return s


def _sniff_encoding(head, sample):
    """Pick a codec from the file's leading bytes (BOM) or, failing that, NUL heuristics on a sample."""
    if head.startswith(b"\xef\xbb\xbf"):
        return "utf-8", 3
    if head.startswith(b"\xff\xfe"):
        return "utf-16-le", 2
    if head.startswith(b"\xfe\xff"):
        return "utf-16-be", 2
    if sample and sample.count(b"\x00") > len(sample) // 4:
        # Many NULs: UTF-16 without BOM. NULs at odd offsets (ASCII lo-byte first) => LE, else BE.
        odd = sample[1::2].count(b"\x00")
        even = sample[0::2].count(b"\x00")
        return ("utf-16-le" if odd >= even else "utf-16-be"), 0
    return "utf-8", 0


def tail(path, n):
    try:
        with open(path, "rb") as f:
            head = f.read(4)
            f.seek(0, 2)
            size = f.tell()
            start = max(0, size - n)
            f.seek(start)
            data = f.read()
        # NUL-parity sniffing needs a 2-byte-aligned (absolute offset) sample.
        enc, bom = _sniff_encoding(head, data[start % 2 :])
        if start < bom:
            data = data[bom - start :]
            start = bom
        if enc.startswith("utf-16") and (start - bom) % 2:
            data = data[1:]  # keep 2-byte code units aligned when the tail window starts mid-unit
        return redact(data.decode(enc, "replace"))
    except Exception:
        return None


def read_text(path, max_bytes=16 << 20):
    """Whole-file decode with the same BOM/NUL sniffing as tail(); None if unreadable."""
    try:
        with open(path, "rb") as f:
            data = f.read(max_bytes)
    except OSError:
        return None
    enc, bom = _sniff_encoding(data[:4], data[:4096])
    return data[bom:].decode(enc, "replace")


# Provider error extraction ---------------------------------------------------------------------
ERROR_EVENT_TYPES = ("error", "turn.failed", "stream_error")
RX_STATUS = re.compile(
    r"(?i)(?:http[ _-]?status|status[ _-]?code|statuscode|status)\W{0,4}([45]\d\d)\b"
)
RX_CODE = re.compile(r"(?i)[\"']?code[\"']?\s*[:=]\s*[\"']?(\d{3,6})\b")
RX_TYPE = re.compile(
    r"\b(model_param_invalid|invalid_request_error|rate_limit_exceeded|insufficient_quota|"
    r"context_length_exceeded|server_error|permission_denied|authentication_error)\b"
)
RX_REQID = re.compile(r"(?i)request[ _-]?id[\"']?\s*[:=]\s*[\"']?([A-Za-z0-9._-]{8,64})")
RX_STDERR_ERR = re.compile(r"(?i)\b(error|failed|rejected|unexpected status)\b")


def _as_int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _from_json_message(message):
    """Parse the provider body Codex embeds as a JSON string in error / turn.failed messages."""
    try:
        inner = json.loads(message)
    except (TypeError, ValueError):
        return None
    if not isinstance(inner, dict):
        return None
    ext = inner.get("extError") if isinstance(inner.get("extError"), dict) else {}
    err = inner.get("error") if isinstance(inner.get("error"), dict) else {}
    shape = inner.get("inferhubShape")
    rec = {
        "code": _as_int(inner.get("code")) or _as_int(err.get("code")),
        "type": ext.get("code") or err.get("type") or err.get("code") or ext.get("type"),
        "http_status": _as_int(ext.get("StatusCode")) or _as_int(inner.get("status")),
        "message": inner.get("msg") or ext.get("message") or err.get("message"),
        "request_id": inner.get("requestId") or inner.get("request_id") or err.get("request_id"),
        "retry_hint": inner.get("inferhubRetry"),
        # only the counts prefix ("msgs=37 tools=13"); per-message sizes stay in the receipt
        "request_shape": shape.split("|", 1)[0].strip() if isinstance(shape, str) else None,
    }
    if rec["type"] and not isinstance(rec["type"], str):
        rec["type"] = str(rec["type"])
    return rec


def _from_text(line):
    """Best-effort extraction from a non-JSON line (stderr, launcher-error.txt)."""
    st = RX_STATUS.search(line)
    code = RX_CODE.search(line)
    typ = RX_TYPE.search(line)
    if not (st or typ or (code and RX_STDERR_ERR.search(line))):
        return None
    if not RX_STDERR_ERR.search(line) and not typ:
        return None
    rid = RX_REQID.search(line)
    return {
        "code": _as_int(code.group(1)) if code else None,
        "type": typ.group(1) if typ else None,
        "http_status": _as_int(st.group(1)) if st else None,
        "message": line.strip()[:300],
        "request_id": rid.group(1) if rid else None,
        "retry_hint": None,
        "request_shape": None,
    }


def scan_provider_errors(paths):
    """Returns (distinct_errors, occurrences). Each error: code, type, http_status, message,
    request_id, retry_hint, request_shape, source (file name:line), event_type, occurrences."""
    found = {}
    order = []
    total = 0
    for path in paths:
        if not path or not os.path.exists(path):
            continue
        text = read_text(path)
        if not text:
            continue
        fname = os.path.basename(path)
        for lineno, line in enumerate(text.splitlines(), 1):
            if not line.strip():
                continue
            rec, etype = None, None
            stripped = line.lstrip()
            if stripped.startswith("{"):
                try:
                    o = json.loads(stripped)
                except ValueError:
                    o = None
                if isinstance(o, dict):
                    etype = o.get("type")
                    if etype not in ERROR_EVENT_TYPES:
                        continue  # ordinary events (tool output may mention codes; ignore)
                    msg = o.get("message")
                    if msg is None and isinstance(o.get("error"), dict):
                        msg = o["error"].get("message")
                    rec = _from_json_message(msg)
                    if rec is None:
                        rec = _from_text(str(msg or ""))
                        if rec is None:
                            rec = dict.fromkeys(
                                ("code", "type", "http_status", "request_id", "retry_hint"), None
                            )
                            rec.update(message=str(msg or "")[:300], request_shape=None)
            if rec is None and etype is None:
                rec = _from_text(line)
                etype = "text"
            if rec is None:
                continue
            total += 1
            if rec.get("message"):
                rec["message"] = redact(str(rec["message"]))[:300]
            key = (rec.get("request_id") or "", rec.get("code"), rec.get("http_status"))
            if not key[0]:
                key = ("", rec.get("code"), rec.get("http_status"), rec.get("type"), rec["message"])
            if key in found:
                found[key]["occurrences"] += 1
                continue
            rec.update(source=f"{fname}:{lineno}", event_type=etype, occurrences=1)
            found[key] = rec
            order.append(key)
    errs = [found[k] for k in order]
    # a JSON-derived record is authoritative; drop text-only duplicates of the same code/status
    json_sigs = {(e["code"], e["http_status"]) for e in errs if e["event_type"] != "text"}
    errs = [
        e
        for e in errs
        if e["event_type"] != "text"
        or (e["code"], e["http_status"]) not in json_sigs
        or (e["code"] is None and e["http_status"] is None)
    ]
    return errs, total


def provider_error_attrs(errs, total):
    """Root-span attributes (scalar fields describe the last structured error) and span events."""
    if not errs:
        return {"provider.error.count": 0}, []
    # the terminal JSON error event (what ended the turn) wins over best-effort stderr matches
    structured = [e for e in errs if e.get("event_type") != "text"]
    last = structured[-1] if structured else errs[-1]
    attrs = {
        "provider.error.count": len(errs),
        "provider.error.occurrences": total,
        "provider.error.code": last.get("code"),
        "provider.error.type": last.get("type"),
        "provider.error.http_status": last.get("http_status"),
        "provider.error.message": last.get("message"),
        "provider.request_id": last.get("request_id"),
        "provider.error.retry_hint": last.get("retry_hint"),
        "provider.error.request_shape": last.get("request_shape"),
        "provider.error.source": last.get("source"),
        "provider.error.codes": ",".join(
            sorted({str(e["code"]) for e in errs if e.get("code") is not None})
        )
        or None,
        "provider.request_ids": ",".join(
            [e["request_id"] for e in errs if e.get("request_id")][:10]
        )
        or None,
    }
    events = []
    for e in errs:
        events.append(
            {
                "provider.error.code": e.get("code"),
                "provider.error.type": e.get("type"),
                "provider.error.http_status": e.get("http_status"),
                "provider.error.message": e.get("message"),
                "provider.request_id": e.get("request_id"),
                "provider.error.retry_hint": e.get("retry_hint"),
                "provider.error.request_shape": e.get("request_shape"),
                "provider.error.occurrences": e.get("occurrences"),
                "receipt.source": e.get("source"),
                "receipt.event_type": e.get("event_type"),
            }
        )
    return attrs, events


def receipt_files(a):
    files = {"events": a.events_file, "stderr": a.stderr_file, "launcher": a.launcher_error_file}
    if a.receipt_dir:
        defaults = {
            "events": "codex-events.jsonl",
            "stderr": "codex-stderr.txt",
            "launcher": "launcher-error.txt",
        }
        for k, name in defaults.items():
            if not files[k]:
                p = os.path.join(a.receipt_dir, name)
                if os.path.exists(p):
                    files[k] = p
    return files


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["begin", "end", "test", "scan"])
    ap.add_argument("--service", default="astra-launcher")
    ap.add_argument("--name", default="astra.run")
    ap.add_argument("--trace-id")
    ap.add_argument("--span-id")
    ap.add_argument("--start-ns", type=int)
    ap.add_argument("--exit-code")
    ap.add_argument("--stderr-file")
    ap.add_argument("--stderr-bytes", type=int, default=2048)
    ap.add_argument("--timed-out", action="store_true")
    ap.add_argument("--events-file", help="codex --json events (provider error scan)")
    ap.add_argument("--launcher-error-file", help="launcher-error.txt (provider error scan)")
    ap.add_argument("--receipt-dir", help="default location of the three receipt files")
    ap.add_argument("--dry-run", action="store_true", help="end: print the span JSON, do not send")
    ap.add_argument("--attr", action="append", default=[], help="k=v extra span attribute")
    a = ap.parse_args()
    extra = dict(x.split("=", 1) for x in a.attr if "=" in x)

    if a.cmd == "begin":
        t, s, now = secrets.token_hex(16), secrets.token_hex(8), time.time_ns()
        send(
            a.service,
            [
                span(
                    t,
                    secrets.token_hex(8),
                    s,
                    a.name + ".start",
                    now,
                    now + 1_000_000,
                    dict(extra, **{"run.phase": "start"}),
                )
            ],
            timeout=3.0,
        )
        print(
            json.dumps(
                {"trace_id": t, "span_id": s, "start_ns": now, "traceparent": f"00-{t}-{s}-01"}
            )
        )
        return
    if a.cmd == "test":
        t, s, now = secrets.token_hex(16), secrets.token_hex(8), time.time_ns()
        st = send(
            a.service,
            [
                span(
                    t,
                    s,
                    None,
                    a.name,
                    now - 250_000_000,
                    now,
                    dict(
                        extra,
                        **{
                            "test": True,
                            "secret_probe": "should-be-deleted",
                            "note": "masked? sk-lf-abcdef123456 ghp_" + "A" * 24,
                        },
                    ),
                )
            ],
        )
        print(json.dumps({"trace_id": t, "span_id": s, "http_status": st}))
        return
    if a.cmd == "scan":
        files = receipt_files(a)
        errs, total = scan_provider_errors([files["events"], files["stderr"], files["launcher"]])
        attrs, _ = provider_error_attrs(errs, total)
        print(json.dumps({"files": files, "errors": errs, "root_span_attributes": attrs}, indent=1))
        return
    # end
    now = time.time_ns()
    code = None
    try:
        code = int(a.exit_code) if a.exit_code not in (None, "") else None
    except ValueError:
        pass
    killed = KILLED.get(code) if code is not None else "no exit code (launcher interrupted?)"
    attrs = dict(extra)
    attrs.update(
        {
            "process.exit_code": code,
            "run.killed": bool(killed) or a.timed_out,
            "run.signal": ("timeout" if a.timed_out else killed),
            "run.timed_out": a.timed_out,
            "run.duration_ms": (now - a.start_ns) / 1e6 if a.start_ns else None,
        }
    )
    stderr_file = a.stderr_file or receipt_files(a)["stderr"]
    if stderr_file:
        attrs["run.stderr_tail"] = tail(stderr_file, a.stderr_bytes)
    events = []
    try:
        files = receipt_files(a)
        errs, total = scan_provider_errors([files["events"], files["stderr"], files["launcher"]])
        perr_attrs, perr_events = provider_error_attrs(errs, total)
        attrs.update(perr_attrs)
        events = [(now, "provider.error", ev) for ev in perr_events]
    except Exception as e:  # scanning must never block the root span
        attrs["provider.error.scan_failed"] = repr(e)[:200]
    err = (code != 0) or a.timed_out
    msg = None if not err else (attrs["run.signal"] or f"exit {code}")
    if err and attrs.get("provider.error.count"):
        msg = "provider error {} {} (HTTP {})".format(
            attrs.get("provider.error.code") or "",
            attrs.get("provider.error.type") or "",
            attrs.get("provider.error.http_status") or "?",
        )
    root = span(
        a.trace_id or "0" * 32,
        a.span_id or "0" * 16,
        None,
        a.name,
        a.start_ns or now,
        now,
        attrs,
        err,
        msg,
        events=events,
    )
    if a.dry_run:
        print(json.dumps(root, indent=1, ensure_ascii=False))
        return
    send(a.service, [root])


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"[astra_otel] error: {e}", file=sys.stderr)
