#!/usr/bin/env python3
"""Tiny, daemon-less OTLP/HTTP-JSON root-span emitter for agent launchers (stdlib only).

  begin  -> prints JSON {trace_id, span_id, traceparent, start_ns}; also emits a short
            "<name>.start" marker span (child of the root) so a run that is hard-killed
            before `end` still leaves evidence.
  end    -> emits the root span (start..now) with exit code, killed/timeout flag and a
            redacted stderr tail.
  test   -> emits one self-contained test span; prints its trace_id.

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


def span(trace_id, span_id, parent, name, start_ns, end_ns, attrs, error=False, msg=None, kind=1):
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["begin", "end", "test"])
    ap.add_argument("--service", default="astra-launcher")
    ap.add_argument("--name", default="astra.run")
    ap.add_argument("--trace-id")
    ap.add_argument("--span-id")
    ap.add_argument("--start-ns", type=int)
    ap.add_argument("--exit-code")
    ap.add_argument("--stderr-file")
    ap.add_argument("--stderr-bytes", type=int, default=2048)
    ap.add_argument("--timed-out", action="store_true")
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
    if a.stderr_file:
        attrs["run.stderr_tail"] = tail(a.stderr_file, a.stderr_bytes)
    err = (code != 0) or a.timed_out
    msg = None if not err else (attrs["run.signal"] or f"exit {code}")
    send(
        a.service,
        [span(a.trace_id, a.span_id, None, a.name, a.start_ns or now, now, attrs, err, msg)],
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"[astra_otel] error: {e}", file=sys.stderr)
