"""Sealer: turn collector file-exporter output into immutable zstd segments + sha256 manifest.

The collector's active file (e.g. data/otel/traces.jsonl) is only ever READ: every run seals the
complete-line prefix that is not yet sealed into a new segment (byte range [start, end) of that inode).
Rotated files (lumberjack renames traces.jsonl -> traces-<ts>.jsonl, same inode) get their tail sealed
and a final=true manifest entry; after that they may be pruned (only with --prune-rotated, only when the
old SQLite loader is no longer running, and only after the segment was verified by re-decompression).

manifest.jsonl is append-only; one line per segment:
  segment_id, signal, source_file, inode, head_sha256, start_offset, end_offset, lines, bytes, sha256
  (of the uncompressed bytes), zst_path, zst_bytes, zst_sha256, first_ts/last_ts (unix nanos seen in
  the data), sealed_at, collector_version, final
"""

import glob
import os
import re
import subprocess

import zstandard

from . import common as C

MAX_SEGMENT = int(os.environ.get("AT_MAX_SEGMENT_BYTES", str(64 << 20)))
ZSTD_LEVEL = int(os.environ.get("AT_ZSTD_LEVEL", "12"))
HEAD = 4096
TS_RE = re.compile(rb'"(?:startTimeUnixNano|timeUnixNano|endTimeUnixNano)":"(\d{16,20})"')


def head_sha(path):
    with open(path, "rb") as f:
        return C.sha256_hex(f.read(HEAD))


def load_manifest():
    return C.read_jsonl(C.MANIFEST)


def sealed_state(manifest):
    """(signal, inode, head_sha) -> (max end_offset, final?)"""
    st = {}
    for m in manifest:
        if m.get("kind", "segment") != "segment":
            continue
        k = (m["signal"], m["inode"], m["head_sha256"])
        end, fin = st.get(k, (0, False))
        st[k] = (max(end, m["end_offset"]), fin or bool(m.get("final")))
    return st


def _ts_range(buf):
    lo = hi = None
    for mt in TS_RE.finditer(buf):
        v = int(mt.group(1))
        if v <= 0:
            continue
        lo = v if lo is None or v < lo else lo
        hi = v if hi is None or v > hi else hi
    return lo, hi


def _write_segment(signal, path, inode, hsha, start, buf, final):
    end = start + len(buf)
    day = C.now_iso()[:10]
    seg_id = f"{signal}-{inode}-{hsha[:8]}-{start:012d}-{end:012d}"
    rel = os.path.join("segments", f"signal={signal}", f"date={day}", f"seg-{seg_id}.jsonl.zst")
    out = os.path.join(C.RAW, rel)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    raw_sha = C.sha256_hex(buf)
    comp = zstandard.ZstdCompressor(level=ZSTD_LEVEL, threads=0, write_checksum=True).compress(buf)
    tmp = out + ".tmp"
    with open(tmp, "wb") as f:
        f.write(comp)
        f.flush()
        os.fsync(f.fileno())
    # verify before publishing
    with open(tmp, "rb") as f:
        back = zstandard.ZstdDecompressor().decompress(f.read(), max_output_size=len(buf) + 1)
    if C.sha256_hex(back) != raw_sha:
        os.unlink(tmp)
        raise RuntimeError(f"segment verify failed for {seg_id}")
    os.replace(tmp, out)
    lo, hi = _ts_range(buf)
    m = {
        "kind": "segment",
        "segment_id": seg_id,
        "signal": signal,
        "source_file": os.path.basename(path),
        "inode": inode,
        "head_sha256": hsha,
        "start_offset": start,
        "end_offset": end,
        "lines": buf.count(b"\n"),
        "bytes": len(buf),
        "sha256": raw_sha,
        "zst_path": rel,
        "zst_bytes": len(comp),
        "zst_sha256": C.sha256_hex(comp),
        "first_ts": lo,
        "last_ts": hi,
        "sealed_at": C.now_iso(),
        "collector_version": C.COLLECTOR_VERSION,
        "final": bool(final),
        "verified": True,
    }
    C.append_jsonl(C.MANIFEST, m)
    return m


def _seal_file(signal, path, is_active, state):
    try:
        st = os.stat(path)
    except FileNotFoundError:
        return []
    if st.st_size == 0:
        return []
    hsha = head_sha(path)
    key = (signal, st.st_ino, hsha)
    done, fin = state.get(key, (0, False))
    if fin:
        return []
    out = []
    with open(path, "rb") as f:
        f.seek(done)
        pos = done
        while True:
            buf = f.read(MAX_SEGMENT)
            if not buf:
                break
            cut = buf.rfind(b"\n")
            more = len(buf) == MAX_SEGMENT
            if cut < 0:
                if is_active or more:
                    break  # partial line: wait for the writer / line too long for a chunk
                cut = len(buf) - 1  # rotated file ending without newline: take it all
            chunk = buf[: cut + 1]
            f.seek(pos + len(chunk))
            last = (not is_active) and (f.tell() >= st.st_size)
            out.append(_write_segment(signal, path, st.st_ino, hsha, pos, chunk, last))
            pos += len(chunk)
            if not more and is_active:
                break
        if not is_active and (not out or not out[-1]["final"]) and pos >= st.st_size:
            # rotated file that was already fully sealed while it was active: record finality
            m = {
                "kind": "segment_final",
                "signal": signal,
                "inode": st.st_ino,
                "head_sha256": hsha,
                "source_file": os.path.basename(path),
                "end_offset": pos,
                "sealed_at": C.now_iso(),
            }
            C.append_jsonl(C.MANIFEST, m)
    return out


def loader_active():
    try:
        r = subprocess.run(
            ["systemctl", "is-active", "agent-telemetry-loader"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return r.stdout.strip() == "active"
    except Exception:
        return True  # unknown -> assume active (never prune)


def run(prune_rotated=False, log=print):
    manifest = load_manifest()
    state = sealed_state(manifest)
    finals = {
        (m["signal"], m["inode"], m["head_sha256"])
        for m in manifest
        if m.get("kind") == "segment_final" or m.get("final")
    }
    new = []
    pruned = []
    for sig in C.SIGNALS:
        active = os.path.join(C.OTEL_DIR, f"{sig}.jsonl")
        rotated = sorted(glob.glob(os.path.join(C.OTEL_DIR, f"{sig}-*.jsonl")))
        for p in rotated:  # finish rotated files first (older data)
            new += _seal_file(sig, p, False, state)
        new += _seal_file(sig, active, True, state)
        if prune_rotated and rotated and not loader_active():
            manifest = load_manifest()
            finals = {
                (m["signal"], m["inode"], m["head_sha256"])
                for m in manifest
                if m.get("kind") == "segment_final" or m.get("final")
            }
            for p in rotated:
                st = os.stat(p)
                if (sig, st.st_ino, head_sha(p)) in finals:
                    os.unlink(p)
                    pruned.append(os.path.basename(p))
                    C.append_jsonl(
                        C.MANIFEST,
                        {
                            "kind": "source_pruned",
                            "signal": sig,
                            "source_file": os.path.basename(p),
                            "inode": st.st_ino,
                            "at": C.now_iso(),
                        },
                    )
    for m in new:
        log(f"[seal] {m['segment_id']} lines={m['lines']} bytes={m['bytes']} zst={m['zst_bytes']}")
    return new, pruned
