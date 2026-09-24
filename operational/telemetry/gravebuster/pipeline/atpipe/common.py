"""Shared paths, OTLP helpers, canonical JSON, hashing and the identity scrub."""

import datetime
import hashlib
import json
import os
import re

ROOT = os.environ.get("AT_ROOT", "/srv/agent-telemetry")
PIPE = os.environ.get("AT_PIPE", os.path.join(ROOT, "pipeline"))
OTEL_DIR = os.path.join(ROOT, "data", "otel")
SQLITE_DB = os.path.join(ROOT, "data", "sqlite", "traces.sqlite3")
RECEIPTS_DIR = os.path.join(ROOT, "data", "receipts")
LAKE = os.environ.get("AT_LAKE", os.path.join(ROOT, "data", "lake"))
RAW = os.path.join(LAKE, "raw")
SEGMENTS = os.path.join(RAW, "segments")
MANIFEST = os.path.join(RAW, "manifest.jsonl")
CLEAN = os.path.join(LAKE, "clean")
MODELED = os.path.join(LAKE, "modeled")
STATE = os.path.join(PIPE, "state")
TMP = os.path.join(PIPE, "tmp")
SIGNALS = ("traces", "logs", "metrics")
COLLECTOR_VERSION = "0.155.0"

DUCK_MEMORY = os.environ.get("AT_DUCK_MEMORY", "1500MB")
DUCK_THREADS = int(os.environ.get("AT_DUCK_THREADS", "2"))


def now_iso():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="milliseconds")


def ns_iso(ns):
    """Same conversion as loader/loader.py (keeps content hashes comparable with SQLite rows)."""
    try:
        ns = int(ns)
    except (TypeError, ValueError):
        return None
    if ns <= 0:
        return None
    return datetime.datetime.fromtimestamp(ns / 1e9, datetime.timezone.utc).isoformat(
        timespec="microseconds"
    )


def ns_day(ns):
    try:
        ns = int(ns)
    except (TypeError, ValueError):
        return "unknown"
    if ns <= 0:
        return "unknown"
    return datetime.datetime.fromtimestamp(ns / 1e9, datetime.timezone.utc).strftime("%Y-%m-%d")


def anyval(v):
    if not isinstance(v, dict):
        return v
    if "stringValue" in v:
        return v["stringValue"]
    if "boolValue" in v:
        return v["boolValue"]
    if "intValue" in v:
        try:
            return int(v["intValue"])
        except Exception:
            return v["intValue"]
    if "doubleValue" in v:
        return v["doubleValue"]
    if "arrayValue" in v:
        return [anyval(x) for x in v["arrayValue"].get("values", [])]
    if "kvlistValue" in v:
        return attrs(v["kvlistValue"].get("values", []))
    if "bytesValue" in v:
        return v["bytesValue"]
    return None


def attrs(lst):
    return {kv.get("key"): anyval(kv.get("value", {})) for kv in (lst or [])}


def J(o):
    """loader-compatible compact JSON (not sorted)."""
    return json.dumps(o, separators=(",", ":"), ensure_ascii=False, default=str)


def canon(o):
    """Canonical JSON: sorted keys, compact, UTF-8."""
    return json.dumps(o, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def sha256_hex(b):
    if isinstance(b, str):
        b = b.encode("utf-8")
    return hashlib.sha256(b).hexdigest()


def file_sha256(path, bufsize=1 << 20):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(bufsize)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


# ---------------------------------------------------------------------------
# Identity scrub. Mirrors collector processor transform/identity (2026-09-24):
#   user.email / user.account_id (string) -> "sha256:" + hex(sha256(plaintext)), unsalted, so values
#   join with rows the collector already hashed; non-string values are deleted; values that already
#   look like sha256:<64 hex> are kept; keys matching (?i).*tailscale[._-]?user.* are deleted.
# Applied to resource, scope, span, span-event, log and datapoint attributes (recursively into kvlists)
# BEFORE hashing/writing, so no plaintext identity reaches raw/clean/modeled Parquet.
# ---------------------------------------------------------------------------
HASHED_IDENTITY_KEYS = ("user.email", "user.account_id")
TAILSCALE_KEY_RE = re.compile(r"(?i).*tailscale[._-]?user.*")
SHA_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


class ScrubStats:
    def __init__(self):
        self.hashed = 0
        self.deleted = 0
        self.plaintexts = set()  # kept in memory only, for the post-run leak check

    def as_dict(self):
        return {
            "identity_values_hashed": self.hashed,
            "identity_keys_deleted": self.deleted,
            "emails_masked_in_text": getattr(self, "emails_masked", 0),
        }


def scrub(d, stats=None):
    if not isinstance(d, dict):
        return d
    for k in list(d.keys()):
        v = d[k]
        if k is not None and TAILSCALE_KEY_RE.match(k):
            del d[k]
            if stats:
                stats.deleted += 1
            continue
        if k in HASHED_IDENTITY_KEYS:
            if isinstance(v, str):
                if not SHA_RE.match(v):
                    d[k] = "sha256:" + sha256_hex(v)
                    if stats:
                        stats.hashed += 1
                        if v:
                            stats.plaintexts.add(v)
            else:
                del d[k]
                if stats:
                    stats.deleted += 1
            continue
        if isinstance(v, dict):
            scrub(v, stats)
        elif isinstance(v, list):
            for x in v:
                if isinstance(x, dict):
                    scrub(x, stats)
    return d


def pick(keys, *dicts):
    for d in dicts:
        for k in keys:
            if d.get(k) not in (None, ""):
                return str(d[k])
    return None


def duck_connect(db=":memory:"):
    import duckdb

    os.makedirs(TMP, exist_ok=True)
    con = duckdb.connect(db)
    con.execute(f"SET memory_limit='{DUCK_MEMORY}'")
    con.execute(f"SET threads={DUCK_THREADS}")
    con.execute(f"SET temp_directory='{TMP}'")
    con.execute("SET preserve_insertion_order=false")
    return con


def append_jsonl(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(canon(obj) + "\n")
        f.flush()
        os.fsync(f.fileno())


def read_jsonl(path):
    if not os.path.exists(path):
        return []
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def atomic_write_text(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


# Any e-mail address appearing in any other string value (tool output, stderr tail, bodies...) is
# replaced by the same unsalted "sha256:<hex>" form, so no plaintext address lands in Parquet and the
# value still joins with user.email hashes. Applied identically to file- and SQLite-sourced rows.
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")


def _email_sub(m, stats=None):
    if stats is not None:
        stats.emails_masked = getattr(stats, "emails_masked", 0) + 1
        stats.plaintexts.add(m.group(0))
    return "sha256:" + sha256_hex(m.group(0))


def mask_emails(o, stats=None):
    if isinstance(o, str):
        if "@" not in o:
            return o
        return EMAIL_RE.sub(lambda m: _email_sub(m, stats), o)
    if isinstance(o, dict):
        return {k: mask_emails(v, stats) for k, v in o.items()}
    if isinstance(o, list):
        return [mask_emails(v, stats) for v in o]
    return o
