"""Astra launcher receipts (data/receipts/<stamp>/) -> clean Parquet.

Files are read-only copies from the PC. Encodings: codex-events.jsonl / codex-stderr.txt /
launcher-error.txt are UTF-16LE with BOM; summary.txt / attempt.txt / BUG.md are UTF-8 (BOM optional).
Outputs (rebuilt when the receipt file set changes; small):
  clean/receipts/receipt_files.parquet   stamp, file, bytes, sha256, encoding
  clean/receipts/receipt_runs.parquet    one row per receipt dir (exit code, killed, duration, errors...)
  clean/receipts/receipt_events.parquet  one row per codex exec JSON event (no message text, only metadata)
Only metadata is extracted; free text (agent messages, command output, stderr) is NOT copied, except a
short first line of launcher-error/stderr with e-mails masked.
"""

import datetime
import glob
import json
import os
import re

import pyarrow as pa
import pyarrow.parquet as pq

from . import common as C

EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
STAMP = re.compile(r"^\d{8}T\d{6}Z$")
OUT = os.path.join(C.CLEAN, "receipts")


def decode(b):
    if b[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return b.decode("utf-16", errors="replace"), "utf-16" + (
            "le" if b[:2] == b"\xff\xfe" else "be"
        ) + "-bom"
    if b[:3] == b"\xef\xbb\xbf":
        return b[3:].decode("utf-8", errors="replace"), "utf-8-bom"
    if len(b) > 4 and b[1:2] == b"\x00" and b[3:4] == b"\x00":
        return b.decode("utf-16-le", errors="replace"), "utf-16le"
    return b.decode("utf-8", errors="replace"), "utf-8"


def kv(text):
    d = {}
    for line in text.splitlines():
        if "=" in line and not line.startswith(" "):
            k, _, v = line.partition("=")
            k = k.strip()
            if re.match(r"^[A-Za-z_][A-Za-z0-9_.]*$", k) and k not in d:
                d[k] = v.strip()
        elif line.strip() == "" and d:
            break  # free-text notes follow the key=value header
    return d


def _int(s):
    try:
        return int(s)
    except Exception:
        return None


SHAPE = {
    k: re.compile(p)
    for k, p in {
        "msgs": r"msgs=(\d+)",
        "tools": r"tools=(\d+)",
        "empty_content": r"emptyContent=(\d+)",
        "max_msg_bytes": r"maxMsg=(\d+)B",
        "additional_properties": r"additionalProperties=(\d+)",
        "max_tool_desc_bytes": r"maxDesc=(\d+)",
    }.items()
}


def parse_error_blob(s):
    """codex exec 'error' / 'turn.failed' message: JSON-in-a-string from InferHub."""
    out = {}
    try:
        e = json.loads(s)
    except Exception:
        e = None
    if isinstance(e, dict):
        out["provider_error_code"] = e.get("code")
        ext = e.get("extError") or {}
        out["error_type"] = ext.get("code")
        out["http_status"] = ext.get("StatusCode")
        out["error_message"] = ext.get("message")
        out["request_id"] = e.get("requestId")
        out["retry_class"] = e.get("inferhubRetry")
        shape = e.get("inferhubShape") or ""
    else:
        m = re.search(r'"code"\s*:\s*(\d+)', s or "")
        out["provider_error_code"] = int(m.group(1)) if m else None
        m = re.search(r'"requestId"\s*:\s*"([0-9a-f]+)"', s or "")
        out["request_id"] = m.group(1) if m else None
        out["error_message"] = EMAIL.sub("<email-redacted>", (s or "")[:300])
        shape = s or ""
    for k, rx in SHAPE.items():
        m = rx.search(shape)
        out[k] = int(m.group(1)) if m else None
    out["request_shape_hash"] = C.sha256_hex(re.sub(r"\d+B", "", shape))[:16] if shape else None
    return out


def parse_dir(d):
    stamp = os.path.basename(d.rstrip("/"))
    files, texts = [], {}
    for p in sorted(glob.glob(os.path.join(d, "*"))):
        if not os.path.isfile(p):
            continue
        b = open(p, "rb").read()
        t, enc = decode(b)
        st = os.stat(p)
        files.append(
            {
                "stamp": stamp,
                "file": os.path.basename(p),
                "bytes": len(b),
                "sha256": C.sha256_hex(b),
                "encoding": enc,
                "mtime_utc": datetime.datetime.fromtimestamp(
                    st.st_mtime, datetime.timezone.utc
                ).replace(tzinfo=None),
            }
        )
        texts[os.path.basename(p)] = t
    summ = kv(texts.get("summary.txt", ""))
    att = kv(texts.get("attempt.txt", ""))
    events, seq = [], 0
    counts = {}
    thread_id = None
    usage = {}
    errors = []
    for line in texts.get("codex-events.jsonl", "").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except Exception:
            counts["bad_json"] = counts.get("bad_json", 0) + 1
            continue
        typ = ev.get("type")
        item = ev.get("item") if isinstance(ev.get("item"), dict) else {}
        itype = item.get("type")
        k = f"{typ}/{itype}" if itype else typ
        counts[k] = counts.get(k, 0) + 1
        row = {
            "stamp": stamp,
            "seq": seq,
            "type": typ,
            "item_id": item.get("id"),
            "item_type": itype,
            "status": item.get("status"),
            "exit_code": item.get("exit_code"),
            "command_sha256": C.sha256_hex(item["command"])[:16] if item.get("command") else None,
            "text_bytes": len(
                item.get("text") or item.get("aggregated_output") or item.get("message") or ""
            )
            or None,
            "provider_error_code": None,
            "error_type": None,
            "http_status": None,
            "request_id": None,
            "msgs": None,
            "tools": None,
            "empty_content": None,
            "max_msg_bytes": None,
            "additional_properties": None,
            "max_tool_desc_bytes": None,
            "request_shape_hash": None,
        }
        if typ == "thread.started":
            thread_id = ev.get("thread_id")
        if typ == "turn.completed":
            usage = ev.get("usage") or {}
        blob = None
        if typ == "error":
            blob = ev.get("message")
        elif typ == "turn.failed":
            blob = (ev.get("error") or {}).get("message")
        if blob:
            pe = parse_error_blob(blob)
            for kk in (
                "provider_error_code",
                "error_type",
                "http_status",
                "request_id",
                "msgs",
                "tools",
                "empty_content",
                "max_msg_bytes",
                "additional_properties",
                "max_tool_desc_bytes",
                "request_shape_hash",
            ):
                row[kk] = pe.get(kk)
            if typ == "turn.failed":
                errors.append(pe)
        events.append(row)
        seq += 1
    try:
        started = datetime.datetime.strptime(stamp, "%Y%m%dT%H%M%SZ")
    except ValueError:
        started = None
    ends = [
        f["mtime_utc"]
        for f in files
        if f["file"]
        in (
            "codex-events.jsonl",
            "codex-stderr.txt",
            "summary.txt",
            "final-message.md",
            "launcher-error.txt",
        )
    ]
    ended = max(ends) if ends else None
    exit_raw = summ.get("exit_code")
    exit_code = _int(exit_raw)
    result = summ.get("result")
    terminal = (
        "turn.completed"
        if counts.get("turn.completed")
        else ("turn.failed" if counts.get("turn.failed") else None)
    )
    if exit_raw is not None and exit_code is None:
        killed = True  # e.g. exit_code=interrupted
    elif result and "interrupt" in result:
        killed = True
    elif exit_code == -1:
        killed = True  # launcher reports terminated processes as -1
    elif exit_code is not None:
        killed = False
    else:
        killed = None  # no summary: unknown
    le = texts.get("launcher-error.txt")
    if le is not None:
        outcome = "launcher_error"
    elif killed:
        outcome = "killed"
    elif exit_code == 0 and terminal == "turn.completed":
        outcome = "completed"
    elif terminal == "turn.failed" or (exit_code not in (None, 0)):
        outcome = "failed"
    elif terminal == "turn.completed":
        outcome = "completed"
    elif terminal is None and not summ:
        outcome = "no_terminal_event"
    else:
        outcome = "unknown"
    first_err = errors[0] if errors else {}
    stderr_first = None
    for f in ("launcher-error.txt", "codex-stderr.txt"):
        if texts.get(f):
            stderr_first = (
                EMAIL.sub("<email-redacted>", texts[f].strip().splitlines()[0][:240])
                if texts[f].strip()
                else None
            )
            break
    receipt_sha = C.sha256_hex(C.canon(sorted((f["file"], f["sha256"]) for f in files)))
    run = {
        "stamp": stamp,
        "run_id": f"astra-{stamp}",
        "started_at_utc": started,
        "ended_at_utc": ended,
        "duration_s": (ended - started).total_seconds() if (started and ended) else None,
        "duration_source": "stamp_to_last_file_mtime",
        "exit_code_raw": exit_raw,
        "exit_code": exit_code,
        "killed": killed,
        "outcome": outcome,
        "result": result,
        "action": summ.get("action") or att.get("action"),
        "model": summ.get("model"),
        "provider": summ.get("provider"),
        "sandbox": summ.get("sandbox"),
        "approval_policy": summ.get("approval_policy"),
        "launcher": summ.get("launcher") or att.get("launcher"),
        "bugfix": summ.get("bugfix"),
        "trace_id": summ.get("trace_id"),
        "session_id": summ.get("session_id") or thread_id,
        "thread_id": thread_id,
        "prior_receipt": att.get("prior_receipt"),
        "prior_session_id": att.get("prior_session_id"),
        "terminal_event": terminal,
        "n_events": seq,
        "n_commands": counts.get("item.completed/command_execution", 0),
        "n_commands_started": counts.get("item.started/command_execution", 0),
        "n_agent_messages": counts.get("item.completed/agent_message", 0),
        "n_file_changes": counts.get("item.completed/file_change", 0),
        "n_errors": counts.get("error", 0) + counts.get("item.completed/error", 0),
        "provider_error_code": first_err.get("provider_error_code"),
        "error_type": first_err.get("error_type"),
        "http_status": first_err.get("http_status"),
        "request_id": first_err.get("request_id"),
        "tokens_in": usage.get("input_tokens"),
        "tokens_cached": usage.get("cached_input_tokens"),
        "tokens_out": usage.get("output_tokens"),
        "tokens_reasoning": usage.get("reasoning_output_tokens"),
        "has_summary": "summary.txt" in texts,
        "has_final_message": "final-message.md" in texts,
        "has_launcher_error": le is not None,
        "has_bug_note": "BUG.md" in texts,
        "stderr_first_line": stderr_first,
        "receipt_sha256": receipt_sha,
        "files": ",".join(f["file"] for f in files),
    }
    return run, events, files


def run(log=print):
    dirs = sorted(
        d
        for d in glob.glob(os.path.join(C.RECEIPTS_DIR, "*"))
        if os.path.isdir(d) and STAMP.match(os.path.basename(d))
    )
    runs, events, files = [], [], []
    for d in dirs:
        r, e, f = parse_dir(d)
        runs.append(r)
        events += e
        files += f
    os.makedirs(OUT, exist_ok=True)
    for name, rows in (
        ("receipt_runs", runs),
        ("receipt_events", events),
        ("receipt_files", files),
    ):
        if not rows:
            continue
        tbl = pa.Table.from_pylist(rows)
        tmp = os.path.join(OUT, name + ".parquet.tmp")
        pq.write_table(tbl, tmp, compression="zstd")
        os.replace(tmp, os.path.join(OUT, name + ".parquet"))
    set_hash = C.sha256_hex(C.canon(sorted((f["stamp"], f["file"], f["sha256"]) for f in files)))
    log(
        f"[receipts] {len(runs)} receipt dirs, {len(events)} events, {len(files)} files, set={set_hash[:12]}"
    )
    return {
        "receipt_dirs": len(runs),
        "receipt_events": len(events),
        "receipt_files": len(files),
        "receipts_set_sha256": set_hash,
        "file_hashes": sorted(f["sha256"] for f in files),
    }
