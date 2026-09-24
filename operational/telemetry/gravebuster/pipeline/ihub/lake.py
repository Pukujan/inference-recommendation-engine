"""Deduped, append-only Parquet parts: data/inferhub/parquet/<table>/day=<D>/part-<run>.parquet.

Each row carries ``_key`` (natural key). A row whose key already exists in that day's parts is
dropped (duplicate). For tables with ``content_hash`` (request logs) a known key with a different
hash is written once to ``<table>_revisions`` instead (latest revision wins in the modeled layer).
"""

from __future__ import annotations

import glob
import os
from collections import defaultdict
from typing import Any

MEMORY = os.environ.get("IHUB_DUCK_MEMORY", "256MB")


def connect() -> Any:
    import duckdb

    con = duckdb.connect()
    con.execute(f"SET memory_limit='{MEMORY}'")
    con.execute("SET threads=1")
    con.execute("SET preserve_insertion_order=false")
    tmp = os.environ.get("IHUB_DUCK_TMP") or os.path.join(
        os.environ.get("IHUB_DATA")
        or os.path.join(os.environ.get("AT_ROOT", "/srv/agent-telemetry"), "data", "inferhub"),
        "tmp",
    )
    os.makedirs(tmp, exist_ok=True)
    con.execute(f"SET temp_directory='{tmp}'")  # spill instead of OOM under MemoryMax
    con.execute("SET max_temp_directory_size='2GB'")
    return con


def sql_list(files: list[str]) -> str:
    return "[" + ", ".join("'" + f.replace("'", "''") + "'" for f in files) + "]"


def _parts(root: str, table: str, day: str | None = None) -> list[str]:
    pat = os.path.join(root, table, f"day={day}" if day else "day=*", "*.parquet")
    return sorted(glob.glob(pat))


def existing(con: Any, root: str, table: str, day: str, with_hash: bool) -> dict[str, str | None]:
    files = _parts(root, table, day)
    if not files:
        return {}
    cols = "_key, content_hash" if with_hash else "_key, NULL"
    rows = con.execute(
        f"SELECT {cols} FROM read_parquet({sql_list(files)}, union_by_name=true)"
    ).fetchall()
    return {k: h for k, h in rows}


def append(
    con: Any,
    root: str,
    table: str,
    rows: list[dict[str, Any]],
    key_fn: Any,
    day_fn: Any,
    run_id: str,
    with_hash: bool = False,
) -> dict[str, int]:
    """Append rows not yet present; returns counts {staged,new,dup,revised}."""
    import pyarrow as pa

    stats = {"staged": len(rows), "new": 0, "dup": 0, "revised": 0}
    if not rows:
        return stats
    by_day: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        r = dict(r)
        r["_key"] = key_fn(r)
        by_day[day_fn(r)].append(r)
    for day, drs in by_day.items():
        known = existing(con, root, table, day, with_hash)
        new, rev, seen = [], [], set()
        for r in drs:
            k = r["_key"]
            if k in seen:
                stats["dup"] += 1
                continue
            seen.add(k)
            if k not in known:
                new.append(r)
            elif with_hash and known[k] != r.get("content_hash"):
                rev.append(r)
            else:
                stats["dup"] += 1
        for tbl, batch in ((table, new), (table + "_revisions", rev)):
            if not batch:
                continue
            if tbl.endswith("_revisions"):
                # write each revision only once
                prev = existing(con, root, tbl, day, True)
                batch = [r for r in batch if prev.get(r["_key"]) != r.get("content_hash")]
                if not batch:
                    stats["dup"] += len(rev)
                    continue
                stats["revised"] += len(batch)
            else:
                stats["new"] += len(batch)
            out = os.path.join(root, tbl, f"day={day}", f"part-{run_id}.parquet")
            os.makedirs(os.path.dirname(out), exist_ok=True)
            arrow_batch = pa.Table.from_pylist(batch)  # noqa: F841  (referenced by DuckDB below)
            tmp = out + ".tmp"
            con.execute(
                f"COPY (SELECT * FROM arrow_batch) TO '{tmp}' (FORMAT parquet, COMPRESSION zstd, "
                "COMPRESSION_LEVEL 9)"
            )
            os.replace(tmp, out)
    return stats


def compact(con: Any, root: str, table: str, day: str) -> int:
    """Merge a day's parts into one ``compact.parquet`` (housekeeping; content unchanged).

    Streams in file order (no sort/hash of whole rows, so memory stays flat and row locality, i.e.
    compression, is kept). Rows are unique per identity within a day because ``append`` dedups
    (``*_revisions`` may hold several revisions of one key, so there identity is
    ``(_key, content_hash)``). If a ``compact.parquet`` already exists (late parts, or a previous
    merge died after writing it but before removing the parts) its rows are kept and other parts
    contribute only identities it does not contain, so a re-run never duplicates rows.
    Returns the number of files merged (0 = nothing to do)."""
    files = _parts(root, table, day)
    if len(files) <= 1:
        return 0
    ident = "_key || '|' || coalesce(content_hash, '')" if table.endswith("_revisions") else "_key"
    out = os.path.join(root, table, f"day={day}", "compact.parquet")
    tmp = out + ".tmp"
    others = [f for f in files if f != out]
    src = f"read_parquet({sql_list(others)}, union_by_name=true)"
    if out in files:
        base = f"read_parquet({sql_list([out])}, union_by_name=true)"
        q = (
            f"SELECT * FROM {base} UNION ALL BY NAME SELECT * FROM {src} "
            f"WHERE {ident} NOT IN (SELECT {ident} FROM {base})"
        )
    else:
        q = f"SELECT * FROM {src}"
    con.execute(
        f"COPY ({q}) TO '{tmp}' (FORMAT parquet, COMPRESSION zstd, COMPRESSION_LEVEL 9, "
        "ROW_GROUP_SIZE 100000)"
    )
    os.replace(tmp, out)
    for f in others:
        os.remove(f)
    return len(files)


def compact_finished(con: Any, root: str, before_day: str) -> dict[str, Any]:
    """Compact every ``<table>/day=D`` with more than one part and D < ``before_day`` (UTC day of
    the run, so only finished days). Late rows for an old day (request-log overlap) land as a new
    part and are merged on the next call. Caller must hold the ihub lock (all writers take it)."""
    merged: dict[str, int] = {}
    bytes_before = bytes_after = 0
    for d in sorted(glob.glob(os.path.join(root, "*", "day=*"))):
        table, day = os.path.basename(os.path.dirname(d)), os.path.basename(d)[4:]
        if day >= before_day:
            continue
        files = _parts(root, table, day)
        if len(files) <= 1:
            continue
        bytes_before += sum(os.path.getsize(f) for f in files)
        n = compact(con, root, table, day)
        bytes_after += os.path.getsize(os.path.join(d, "compact.parquet"))
        merged[f"{table}/{day}"] = n
    return {"merged": merged, "bytes_before": bytes_before, "bytes_after": bytes_after}
