"""raw Parquet ingest: sealed segments (+ SQLite ledger backfill) -> deduplicated raw Parquet.

Tables (Parquet, zstd, Hive-partitioned by UTC day of the span start / log time):
  raw/spans/day=YYYY-MM-DD/part-<etl_run_id>.parquet      one row per unique (trace_id, span_id)
  raw/logs/...                                            one row per unique log key
  raw/metrics/...                                         one row per unique datapoint key
  raw/conflicts/<table>/part-<etl_run_id>.parquet         same key, different content_hash (never overwrites)
Dedup rules (design doc 5.1): same key + same content_hash -> dropped and counted; same key + different
hash -> first-seen row stays, the other goes to conflicts. Parts are append-only; reruns only add rows
whose key is not yet present, so re-ingesting everything writes nothing (idempotent).
"""

import os
import sqlite3

import pyarrow as pa
import zstandard

from . import common as C
from . import parse as P

TABLES = {"traces": "spans", "logs": "logs", "metrics": "metrics"}
BATCH = 5000

SCHEMAS = {
    "spans": pa.schema(
        [
            ("key", pa.string()),
            ("trace_id", pa.string()),
            ("span_id", pa.string()),
            ("parent_span_id", pa.string()),
            ("name", pa.string()),
            ("service_name", pa.string()),
            ("start_unix_nano", pa.int64()),
            ("end_unix_nano", pa.int64()),
            ("day", pa.string()),
            ("content_hash", pa.string()),
            ("canonical_json", pa.string()),
            ("extras_json", pa.string()),
            ("source", pa.string()),
            ("source_ref", pa.string()),
            ("priority", pa.int32()),
            ("seq", pa.int64()),
        ]
    ),
    "logs": pa.schema(
        [
            ("key", pa.string()),
            ("trace_id", pa.string()),
            ("span_id", pa.string()),
            ("time_unix_nano", pa.int64()),
            ("event_name", pa.string()),
            ("service_name", pa.string()),
            ("day", pa.string()),
            ("content_hash", pa.string()),
            ("canonical_json", pa.string()),
            ("extras_json", pa.string()),
            ("source", pa.string()),
            ("source_ref", pa.string()),
            ("priority", pa.int32()),
            ("seq", pa.int64()),
        ]
    ),
    "metrics": pa.schema(
        [
            ("key", pa.string()),
            ("name", pa.string()),
            ("time_unix_nano", pa.int64()),
            ("service_name", pa.string()),
            ("day", pa.string()),
            ("content_hash", pa.string()),
            ("canonical_json", pa.string()),
            ("extras_json", pa.string()),
            ("source", pa.string()),
            ("source_ref", pa.string()),
            ("priority", pa.int32()),
            ("seq", pa.int64()),
        ]
    ),
}

INGESTED = os.path.join(C.STATE, "ingested_segments.jsonl")
SQLITE_STATE = os.path.join(C.STATE, "sqlite_backfill.json")


class Stager:
    def __init__(self, con):
        self.con = con
        self.buf = {t: [] for t in SCHEMAS}
        self.n = {t: 0 for t in SCHEMAS}
        for t, sch in SCHEMAS.items():
            cols = ", ".join(f'"{f.name}" {self._duck(f.type)}' for f in sch)
            con.execute(f"CREATE TEMP TABLE stage_{t} ({cols})")

    @staticmethod
    def _duck(t):
        return {pa.string(): "VARCHAR", pa.int64(): "BIGINT", pa.int32(): "INTEGER"}[t]

    def add(self, table, rec):
        self.buf[table].append(rec)
        self.n[table] += 1
        if len(self.buf[table]) >= BATCH:
            self.flush(table)

    def flush(self, table=None):
        for t in [table] if table else list(self.buf):
            rows = self.buf[t]
            if not rows:
                continue
            tbl = pa.Table.from_pylist(rows, schema=SCHEMAS[t])
            self.con.register("_batch", tbl)
            self.con.execute(f"INSERT INTO stage_{t} SELECT * FROM _batch")
            self.con.unregister("_batch")
            self.buf[t] = []


def stage_segments(stager, segments, stats, log):
    for m in segments:
        path = os.path.join(C.RAW, m["zst_path"])
        table = TABLES[m["signal"]]
        fn = P.OTLP[m["signal"]]
        with open(path, "rb") as f:
            data = zstandard.ZstdDecompressor().decompress(f.read(), max_output_size=m["bytes"] + 1)
        if C.sha256_hex(data) != m["sha256"]:
            raise RuntimeError(f"sha256 mismatch for sealed segment {m['segment_id']}")
        off = 0
        seq_base = m["start_offset"] * 1000
        for line in data.split(b"\n"):
            pos = off
            off += len(line) + 1
            s = line.strip()
            if not s:
                continue
            try:
                import json

                doc = json.loads(s)
            except Exception as e:
                log(f"[ingest] bad json in {m['segment_id']}@{pos}: {e}")
                continue
            ref = f"{m['segment_id']}@{pos}"
            for rec in fn(doc, ref, seq_base + pos * 1000 // 1, stats):
                stager.add(table, rec)
        del data


def stage_sqlite(stager, state, stats, full=False):
    if not os.path.exists(C.SQLITE_DB):
        return {}
    con = sqlite3.connect(f"file:{C.SQLITE_DB}?mode=ro", uri=True, timeout=30)
    con.row_factory = sqlite3.Row
    maxes = {}
    for table, fn in P.SQLITE.items():
        start = 0 if full else int(state.get(f"{table}_max_id", 0))
        top = con.execute(f"SELECT COALESCE(MAX(id),0) FROM {table}").fetchone()[0]
        cur = con.execute(
            f"SELECT * FROM {table} WHERE id > ? AND id <= ? ORDER BY id", (start, top)
        )
        for r in cur:
            stager.add(table, fn(r, stats, 10**15 + r["id"]))
        maxes[f"{table}_max_id"] = top
    con.close()
    return maxes


def _existing(con, table):
    import glob

    files = glob.glob(os.path.join(C.RAW, table, "day=*", "*.parquet"))
    if files:
        con.execute(
            f"CREATE OR REPLACE TEMP VIEW existing_{table} AS SELECT key, content_hash FROM "
            f"read_parquet('{C.RAW}/{table}/day=*/*.parquet', hive_partitioning=false)"
        )
    else:
        con.execute(
            f"CREATE OR REPLACE TEMP VIEW existing_{table} AS SELECT NULL::VARCHAR AS key, "
            f"NULL::VARCHAR AS content_hash WHERE false"
        )
    cfiles = glob.glob(os.path.join(C.RAW, "conflicts", table, "*.parquet"))
    if cfiles:
        con.execute(
            f"CREATE OR REPLACE TEMP VIEW existing_conf_{table} AS SELECT key, content_hash FROM "
            f"read_parquet('{C.RAW}/conflicts/{table}/*.parquet')"
        )
    else:
        con.execute(
            f"CREATE OR REPLACE TEMP VIEW existing_conf_{table} AS SELECT NULL::VARCHAR AS key, "
            f"NULL::VARCHAR AS content_hash WHERE false"
        )


def dedup_and_write(con, table, etl_run_id, ingested_at):
    """Classify staged rows and append new/conflict rows. Returns counts and touched days."""
    _existing(con, table)
    # classify on a narrow projection (rowid, key, hash, order) so wide JSON columns are not copied/sorted
    con.execute(f"""
      CREATE OR REPLACE TEMP TABLE cls_{table} AS
      WITH s AS (
        SELECT rowid AS rid, key, content_hash, priority, seq, day, source,
               row_number() OVER (PARTITION BY key ORDER BY priority, seq, source_ref) AS rn FROM stage_{table}),
      f AS (SELECT key, content_hash AS first_hash FROM s WHERE rn = 1)
      SELECT s.rid, s.key, s.content_hash, s.day, s.source, s.priority, s.seq,
             e.content_hash AS existing_hash, f.first_hash,
             CASE WHEN e.key IS NULL AND s.rn = 1 THEN 'new'
                  WHEN s.content_hash = coalesce(e.content_hash, f.first_hash) THEN 'dup'
                  ELSE 'conflict' END AS cls
      FROM s JOIN f USING (key) LEFT JOIN existing_{table} e USING (key)""")
    counts = dict(con.execute(f"SELECT cls, count(*) FROM cls_{table} GROUP BY 1").fetchall())
    by_src = con.execute(
        f"SELECT source, cls, count(*) FROM cls_{table} GROUP BY 1,2 ORDER BY 1,2"
    ).fetchall()
    cols = ", ".join(f'st."{f.name}"' for f in SCHEMAS[table] if f.name not in ("priority", "seq"))
    days = [
        r[0]
        for r in con.execute(
            f"SELECT DISTINCT day FROM cls_{table} WHERE cls='new' ORDER BY 1"
        ).fetchall()
    ]
    for d in days:
        out_dir = os.path.join(C.RAW, table, f"day={d}")
        os.makedirs(out_dir, exist_ok=True)
        out = os.path.join(out_dir, f"part-{etl_run_id}.parquet")
        con.execute(
            f"""COPY (SELECT {cols}, '{etl_run_id}' AS etl_run_id, '{ingested_at}' AS ingested_at
                              FROM cls_{table} c JOIN stage_{table} st ON st.rowid = c.rid
                              WHERE c.cls='new' AND c.day = ? ORDER BY st.key)
                        TO '{out}.tmp' (FORMAT parquet, COMPRESSION zstd, COMPRESSION_LEVEL 9, ROW_GROUP_SIZE 100000)""",
            [d],
        )
        os.replace(out + ".tmp", out)
    nconf = con.execute(f"""SELECT count(*) FROM cls_{table} c WHERE cls='conflict'
                            AND NOT EXISTS (SELECT 1 FROM existing_conf_{table} x
                                            WHERE x.key=c.key AND x.content_hash=c.content_hash)""").fetchone()[
        0
    ]
    if nconf:
        out_dir = os.path.join(C.RAW, "conflicts", table)
        os.makedirs(out_dir, exist_ok=True)
        out = os.path.join(out_dir, f"part-{etl_run_id}.parquet")
        con.execute(f"""COPY (SELECT {cols}, coalesce(c.existing_hash, c.first_hash) AS kept_content_hash,
                                     '{etl_run_id}' AS etl_run_id, '{ingested_at}' AS detected_at
                              FROM cls_{table} c JOIN stage_{table} st ON st.rowid = c.rid WHERE c.cls='conflict'
                              AND NOT EXISTS (SELECT 1 FROM existing_conf_{table} x
                                              WHERE x.key=c.key AND x.content_hash=c.content_hash)
                              QUALIFY row_number() OVER (PARTITION BY c.key, c.content_hash ORDER BY c.priority, c.seq)=1)
                        TO '{out}.tmp' (FORMAT parquet, COMPRESSION zstd)""")
        os.replace(out + ".tmp", out)
    return {
        "staged": sum(counts.values()),
        "new": counts.get("new", 0),
        "dups_dropped": counts.get("dup", 0),
        "conflicts": counts.get("conflict", 0),
        "conflicts_written": nconf,
        "by_source": [list(x) for x in by_src],
    }, days


def run(con, segments, etl_run_id, ingested_at, stats, reingest_all=False, log=print):
    import json

    st = {}
    if os.path.exists(SQLITE_STATE) and not reingest_all:
        st = json.load(open(SQLITE_STATE))
    stager = Stager(con)
    stage_segments(stager, segments, stats, log)
    maxes = stage_sqlite(stager, st, stats, full=reingest_all)
    stager.flush()
    result, touched = {}, {}
    for table in SCHEMAS:
        result[table], touched[table] = dedup_and_write(con, table, etl_run_id, ingested_at)
        log(f"[ingest] {table}: {result[table]}")
    for m in segments:
        C.append_jsonl(
            INGESTED,
            {
                "segment_id": m["segment_id"],
                "sha256": m["sha256"],
                "etl_run_id": etl_run_id,
                "at": ingested_at,
            },
        )
    if maxes:
        C.atomic_write_text(SQLITE_STATE, json.dumps(maxes))
    return result, touched, maxes


def pending_segments(manifest, reingest_all=False):
    done = set() if reingest_all else {x["segment_id"] for x in C.read_jsonl(INGESTED)}
    return [
        m for m in manifest if m.get("kind", "segment") == "segment" and m["segment_id"] not in done
    ]
