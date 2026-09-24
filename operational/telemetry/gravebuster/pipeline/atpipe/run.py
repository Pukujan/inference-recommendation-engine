"""Pipeline entry point: seal -> raw Parquet (dedup) -> clean -> receipts -> dbt modeled snapshot.

Usage: venv/bin/python -m atpipe.run [--reingest-all] [--rebuild-clean] [--force-model] [--prune-rotated]
Runs then exits (no daemon). A flock prevents overlapping runs. Every run appends one line to
data/lake/meta/etl_runs.jsonl.
"""

import argparse
import fcntl
import glob
import json
import os
import platform
import resource
import shutil
import subprocess
import sys
import time
import uuid

from . import clean, ingest, receipts, seal
from . import common as C

META = os.path.join(C.LAKE, "meta")
ETL_RUNS = os.path.join(META, "etl_runs.jsonl")
SNAPSHOTS = os.path.join(C.MODELED, "snapshots.jsonl")
KEEP_SNAPSHOTS = int(os.environ.get("AT_KEEP_SNAPSHOTS", "10"))
DBT_DIR = os.path.join(C.PIPE, "dbt")
STUB_TABLES = ("fact_incidents", "fact_experiment_trials", "dim_experiment")


def log(msg):
    print(msg, flush=True)


def code_sha():
    files = []
    for pat in (
        "atpipe/*.py",
        "dbt/dbt_project.yml",
        "dbt/models/**/*.sql",
        "dbt/models/**/*.yml",
        "dbt/seeds/*.csv",
        "dbt/macros/*.sql",
    ):
        files += glob.glob(os.path.join(C.PIPE, pat), recursive=True)
    parts = sorted((os.path.relpath(p, C.PIPE), C.file_sha256(p)) for p in set(files))
    return C.sha256_hex(C.canon(parts)), len(parts)


def versions():
    import duckdb

    v = {"duckdb": duckdb.__version__, "python": platform.python_version()}
    try:
        from importlib.metadata import version

        v["dbt_core"] = version("dbt-core")
        v["dbt_duckdb"] = version("dbt-duckdb")
    except Exception:
        pass
    return v


def git_head():
    """IRE repository commit recorded by deploy.sh ($AT_ROOT/DEPLOYED); falls back to a local git repo."""
    deployed = os.path.join(C.ROOT, "DEPLOYED")
    try:
        with open(deployed, encoding="utf-8") as f:
            for line in f:
                if line.startswith("sha="):
                    return "ire@" + line.strip()[4:]
    except OSError:
        pass
    try:
        r = subprocess.run(
            ["git", "-C", C.PIPE, "rev-parse", "HEAD"], capture_output=True, text=True, timeout=10
        )
        return r.stdout.strip() or None
    except Exception:
        return None


def leak_check(con, plaintexts):
    """No plaintext identity (values seen by the scrubber) and no e-mail address in raw/clean Parquet."""
    res = {}
    rx = C.EMAIL_RE.pattern.replace("'", "''")
    for layer, pat in (
        ("raw_spans", f"{C.RAW}/spans/day=*/*.parquet"),
        ("raw_logs", f"{C.RAW}/logs/day=*/*.parquet"),
        ("clean_spans", f"{C.CLEAN}/spans/day=*/*.parquet"),
        ("clean_logs", f"{C.CLEAN}/logs/day=*/*.parquet"),
    ):
        if not glob.glob(pat):
            continue
        col = (
            "canonical_json"
            if layer.startswith("raw")
            else "CAST(attributes AS VARCHAR) || coalesce(CAST(resource AS VARCHAR),'')"
        )
        n_email = con.execute(
            f"SELECT count(*) FROM read_parquet('{pat}', hive_partitioning=false) "
            f"WHERE regexp_matches({col}, '{rx}')"
        ).fetchone()[0]
        n_plain = 0
        for p in list(plaintexts)[:50]:
            n_plain += con.execute(
                f"SELECT count(*) FROM read_parquet('{pat}', hive_partitioning=false) "
                f"WHERE contains({col}, ?)",
                [p],
            ).fetchone()[0]
        res[layer] = {"rows_with_email": n_email, "rows_with_known_plaintext": n_plain}
    return res


def run_dbt(snapshot_id, out_dir, inputs_hash):
    env = dict(os.environ)
    env["DBT_PROFILES_DIR"] = DBT_DIR
    env["AT_TMP"] = C.TMP
    env["DBT_SEND_ANONYMOUS_USAGE_STATS"] = "False"
    vars_ = {
        "clean_root": C.CLEAN,
        "out_dir": out_dir,
        "snapshot_id": snapshot_id,
        "inputs_hash": inputs_hash,
        "duck_db": os.path.join(C.STATE, "dbt.duckdb"),
        "memory_limit": C.DUCK_MEMORY,
    }
    dbt = os.path.join(os.path.dirname(sys.executable), "dbt")
    cmd = [
        dbt,
        "build",
        "--project-dir",
        DBT_DIR,
        "--profiles-dir",
        DBT_DIR,
        "--target-path",
        os.path.join(C.STATE, "dbt-target"),
        "--log-path",
        os.path.join(C.STATE, "dbt-logs"),
        "--vars",
        json.dumps(vars_),
        "--threads",
        "1",
        "--no-use-colors",
        "--quiet",
    ]
    env["DBT_DUCK_PATH"] = vars_["duck_db"]
    env["AT_OUT_DIR"] = out_dir
    env["AT_DUCK_MEMORY"] = C.DUCK_MEMORY
    env["AT_DUCK_THREADS"] = str(C.DUCK_THREADS)
    t = time.time()
    r = subprocess.run(cmd, env=env, capture_output=True, text=True, cwd=DBT_DIR)
    if r.returncode != 0:
        log(r.stdout[-4000:])
        log(r.stderr[-4000:])
        raise RuntimeError("dbt build failed")
    # dbt-duckdb's external materialization writes one all-NULL row for an empty result; the stub
    # tables (no detectors / experiments yet) must be truly empty with their schema, so rewrite them.
    import duckdb

    con = duckdb.connect()
    for stub in STUB_TABLES:
        f = os.path.join(out_dir, stub + ".parquet")
        if os.path.exists(f):
            con.execute(
                f"COPY (SELECT * FROM read_parquet('{f}') WHERE false) TO '{f}.tmp' (FORMAT parquet)"
            )
            os.replace(f + ".tmp", f)
    con.close()
    return round(time.time() - t, 2)


def prune_snapshots():
    pins = set()
    pf = os.path.join(C.MODELED, "pins.txt")
    if os.path.exists(pf):
        pins = {ln.strip() for ln in open(pf) if ln.strip()}
    dirs = sorted(
        (d for d in glob.glob(os.path.join(C.MODELED, "snap-*")) if os.path.isdir(d)),
        key=os.path.getmtime,
    )
    removed = []
    for d in dirs[:-KEEP_SNAPSHOTS] if len(dirs) > KEEP_SNAPSHOTS else []:
        if os.path.basename(d) in pins or os.path.basename(d)[5:] in pins:
            continue
        shutil.rmtree(d)
        removed.append(os.path.basename(d))
    return removed


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--reingest-all",
        action="store_true",
        help="re-read every sealed segment and all SQLite rows (dedup proves idempotence)",
    )
    ap.add_argument(
        "--rebuild-clean", action="store_true", help="rebuild every clean day partition"
    )
    ap.add_argument(
        "--force-model",
        action="store_true",
        help="rebuild the modeled snapshot even if inputs are unchanged",
    )
    ap.add_argument(
        "--prune-rotated",
        action="store_true",
        help="delete fully sealed rotated collector files (only if the SQLite loader is stopped)",
    )
    ap.add_argument("--no-seal", action="store_true")
    a = ap.parse_args(argv)

    for d in (C.STATE, C.TMP, META, C.MODELED, C.RAW, C.CLEAN):
        os.makedirs(d, exist_ok=True)
    lockf = open(os.path.join(C.STATE, "pipeline.lock"), "w")
    try:
        fcntl.flock(lockf, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        log("[run] another pipeline run holds the lock; exiting")
        return 0

    t0 = time.time()
    etl_run_id = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()) + "-" + uuid.uuid4().hex[:6]
    started = C.now_iso()
    rec = {"etl_run_id": etl_run_id, "started_at": started, "args": vars(a), "status": "running"}
    stats = C.ScrubStats()
    try:
        new_segments, pruned = (
            ([], []) if a.no_seal else seal.run(prune_rotated=a.prune_rotated, log=log)
        )
        manifest = seal.load_manifest()
        pending = ingest.pending_segments(manifest, a.reingest_all)
        con = C.duck_connect()
        ing, touched, maxes = ingest.run(
            con, pending, etl_run_id, started, stats, a.reingest_all, log
        )
        cl = clean.run(
            con,
            touched,
            rebuild_all=a.rebuild_clean or not glob.glob(os.path.join(C.CLEAN, "spans", "day=*")),
            log=log,
        )
        rc = receipts.run(log=log)
        leaks = leak_check(con, stats.plaintexts)
        con.close()

        segs = sorted(m["sha256"] for m in manifest if m.get("kind", "segment") == "segment")
        sqlite_state = (
            json.load(open(ingest.SQLITE_STATE)) if os.path.exists(ingest.SQLITE_STATE) else {}
        )
        csha, nfiles = code_sha()
        vers = versions()
        inputs = {
            "segment_sha256s": segs,
            "sqlite_backfill": sqlite_state,
            "receipts_set_sha256": rc["receipts_set_sha256"],
            "pipeline_code_sha256": csha,
            "pipeline_git_head": git_head(),
            "versions": vers,
            "model_catalog_version": "ire-telemetry-model/v1",
        }
        snapshot_id = C.sha256_hex(C.canon(inputs))
        inputs_hash = C.sha256_hex(
            C.canon({k: v for k, v in inputs.items() if k != "pipeline_git_head"})
        )
        out_dir = os.path.join(C.MODELED, "snap-" + snapshot_id[:16])
        built, dbt_s = False, None
        if a.force_model or not os.path.exists(os.path.join(out_dir, "_SUCCESS")):
            tmp_out = out_dir + ".building"
            shutil.rmtree(tmp_out, ignore_errors=True)
            os.makedirs(tmp_out)
            dbt_s = run_dbt(snapshot_id, tmp_out, inputs_hash)
            C.atomic_write_text(os.path.join(tmp_out, "_inputs.json"), json.dumps(inputs, indent=1))
            C.atomic_write_text(os.path.join(tmp_out, "_SUCCESS"), C.now_iso() + "\n")
            shutil.rmtree(out_dir, ignore_errors=True)
            os.replace(tmp_out, out_dir)
            C.append_jsonl(
                SNAPSHOTS,
                {
                    "snapshot_id": snapshot_id,
                    "dir": os.path.basename(out_dir),
                    "etl_run_id": etl_run_id,
                    "built_at": C.now_iso(),
                    "segments": len(segs),
                    "pipeline_code_sha256": csha,
                    "versions": vers,
                },
            )
            built = True
        cur = os.path.join(C.MODELED, "current")
        if os.path.islink(cur) or os.path.exists(cur):
            if os.readlink(cur) != os.path.basename(out_dir):
                os.unlink(cur)
                os.symlink(os.path.basename(out_dir), cur)
        else:
            os.symlink(os.path.basename(out_dir), cur)
        removed = prune_snapshots()

        ru_self = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        ru_kids = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
        rec.update(
            {
                "status": "ok",
                "ended_at": C.now_iso(),
                "duration_s": round(time.time() - t0, 2),
                "segments_sealed": len(new_segments),
                "segments_in": len(pending),
                "rotated_pruned": pruned,
                "ingest": ing,
                "clean_rows_rebuilt": cl,
                "receipts": {k: v for k, v in rc.items() if k != "file_hashes"},
                "scrub": stats.as_dict(),
                "leak_check": leaks,
                "snapshot_id": snapshot_id,
                "snapshot_dir": os.path.basename(out_dir),
                "snapshot_built": built,
                "dbt_seconds": dbt_s,
                "snapshots_pruned": removed,
                "peak_rss_mb_self": round(ru_self / 1024, 1),
                "peak_rss_mb_children": round(ru_kids / 1024, 1),
                "pipeline_code_sha256": csha,
                "code_files": nfiles,
                "versions": vers,
            }
        )
        C.append_jsonl(ETL_RUNS, rec)
        log(
            f"[run] ok {etl_run_id} snapshot={snapshot_id[:16]} built={built} {rec['duration_s']}s "
            f"rss_self={rec['peak_rss_mb_self']}MB rss_children={rec['peak_rss_mb_children']}MB"
        )
        bad = {
            k: v for k, v in leaks.items() if v["rows_with_email"] or v["rows_with_known_plaintext"]
        }
        if bad:
            log(f"[run] WARNING identity leak check failed: {bad}")
            return 3
        return 0
    except Exception as e:
        rec.update(
            {
                "status": "error",
                "error": repr(e),
                "ended_at": C.now_iso(),
                "duration_s": round(time.time() - t0, 2),
            }
        )
        C.append_jsonl(ETL_RUNS, rec)
        raise


if __name__ == "__main__":
    sys.exit(main())
