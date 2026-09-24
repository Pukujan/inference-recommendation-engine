"""Experiment manifests (#40 M0.5 matrix) -> dim_experiment + fact_experiment_trials.

Manifests (schema: experiments/trial-manifest.schema.json) are JSON files in $AT_EXPERIMENTS
(default $AT_ROOT/data/experiments/). Parsing and linking are pure functions (unit-tested); ``run()``
writes the two tables into a snapshot directory. No manifest -> both tables stay empty with their
schema; rows are never invented. Invalid manifests are skipped and reported, not fatal.
"""

import glob
import hashlib
import json
import os

MANIFEST_VERSION = "ire-trial-manifest/v1"

DIM_COLUMNS = (
    "experiment_key",
    "experiment_id",
    "hypothesis_id",
    "cell",
    "varied_factor",
    "fixed_factors_hash",
    "preregistered_at",
    "decision_rule_hash",
    "snapshot_id",
    "cell_value",
    "manifest_file",
    "manifest_sha256",
)
TRIAL_COLUMNS = (
    "trial_id",
    "experiment_key",
    "run_id",
    "cell",
    "block",
    "randomization_seed",
    "success",
    "early_stop",
    "kills_at_cap",
    "snapshot_id",
    "experiment_id",
    "receipt_stamp",
    "run_outcome",
    "run_linked",
    "planned",
    "incident_count",
    "manifest_sha256",
)


def _sha(s):
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def manifest_dir():
    root = os.environ.get("AT_ROOT", "/srv/agent-telemetry")
    return os.environ.get("AT_EXPERIMENTS", os.path.join(root, "data", "experiments"))


def manifest_files(d=None):
    d = d or manifest_dir()
    return sorted(glob.glob(os.path.join(d, "*.json")))


def manifests_set_sha256(files):
    parts = []
    for f in files:
        with open(f, "rb") as fh:
            parts.append([os.path.basename(f), hashlib.sha256(fh.read()).hexdigest()])
    return _sha(json.dumps(parts, separators=(",", ":")))


def validate_manifest(m):
    errs = []
    if not isinstance(m, dict):
        return ["manifest is not an object"]
    if m.get("manifest_version") != MANIFEST_VERSION:
        errs.append(f"manifest_version must be {MANIFEST_VERSION}")
    for k in ("experiment_id", "hypothesis_id", "varied_factor"):
        if not isinstance(m.get(k), str) or not m.get(k):
            errs.append(f"{k} is required")
    cells = cell_names(m)
    if not cells:
        errs.append("cells must list at least one cell")
    ids = set()
    for i, t in enumerate(m.get("trials") if isinstance(m.get("trials"), list) else []):
        if not isinstance(t, dict) or not t.get("trial_id"):
            errs.append(f"trials[{i}]: trial_id is required")
            continue
        if t["trial_id"] in ids:
            errs.append(f"trials[{i}]: duplicate trial_id {t['trial_id']}")
        ids.add(t["trial_id"])
        if t.get("cell") not in cells:
            errs.append(f"trials[{i}]: cell {t.get('cell')!r} is not declared")
        if not (t.get("run_id") or t.get("receipt_stamp") or t.get("planned")):
            errs.append(f"trials[{i}]: needs run_id, receipt_stamp or planned=true")
    if not isinstance(m.get("trials"), list):
        errs.append("trials must be a list")
    return errs


def cell_names(m):
    out = []
    for c in m.get("cells") or [] if isinstance(m, dict) else []:
        name = c.get("cell") if isinstance(c, dict) else c
        if isinstance(name, str) and name:
            out.append(name)
    return out


def experiment_key(experiment_id, cell):
    return _sha(f"{experiment_id}|{cell}")[:32]


def build_rows(manifests, runs, incidents, snapshot_id=None):
    """manifests: [(file_name, sha256, dict)] -> (dim_rows, trial_rows, errors)."""
    by_run = {r["run_id"]: r for r in runs if r.get("run_id")}
    by_stamp = {}
    for r in runs:
        if r.get("receipt_stamp") and not r.get("parent_run_id"):
            by_stamp.setdefault(r["receipt_stamp"], r["run_id"])
    inc_by_root, kills_by_root = {}, {}
    for i in incidents:
        root = i.get("root_run_id") or i.get("run_id")
        inc_by_root[root] = inc_by_root.get(root, 0) + 1
        if i.get("incident_type") == "unjustified_kill":
            kills_by_root[root] = kills_by_root.get(root, 0) + 1
    dims, trials, errors = [], [], {}
    for fname, fsha, m in manifests:
        errs = validate_manifest(m)
        if errs:
            errors[fname] = errs
            continue
        eid = m["experiment_id"]
        ff = (
            _sha(json.dumps(m.get("fixed_factors") or {}, sort_keys=True))
            if m.get("fixed_factors")
            else None
        )
        dr = _sha(m["decision_rule"]) if m.get("decision_rule") else None
        for c in m["cells"]:
            name = c.get("cell") if isinstance(c, dict) else c
            dims.append(
                {
                    "experiment_key": experiment_key(eid, name),
                    "experiment_id": eid,
                    "hypothesis_id": m["hypothesis_id"],
                    "cell": name,
                    "varied_factor": m["varied_factor"],
                    "fixed_factors_hash": ff,
                    "preregistered_at": m.get("preregistered_at"),
                    "decision_rule_hash": dr,
                    "snapshot_id": snapshot_id,
                    "cell_value": json.dumps(c.get("value"), sort_keys=True)
                    if isinstance(c, dict) and "value" in c
                    else None,
                    "manifest_file": fname,
                    "manifest_sha256": fsha,
                }
            )
        for t in m["trials"]:
            rid = t.get("run_id") or by_stamp.get(t.get("receipt_stamp"))
            if rid is None and t.get("receipt_stamp"):
                rid = f"astra-{t['receipt_stamp']}"
            run = by_run.get(rid)
            success = t.get("success")
            if success is None and run is not None and run.get("outcome") not in (None, "unknown"):
                success = run.get("outcome") == "completed"
            trials.append(
                {
                    "trial_id": t["trial_id"],
                    "experiment_key": experiment_key(eid, t["cell"]),
                    "run_id": rid,
                    "cell": t["cell"],
                    "block": t.get("block"),
                    "randomization_seed": t.get("randomization_seed"),
                    "success": success,
                    "early_stop": t.get("early_stop"),
                    "kills_at_cap": kills_by_root.get(rid, 0) if run is not None else None,
                    "snapshot_id": snapshot_id,
                    "experiment_id": eid,
                    "receipt_stamp": t.get("receipt_stamp") or (run or {}).get("receipt_stamp"),
                    "run_outcome": (run or {}).get("outcome"),
                    "run_linked": run is not None,
                    "planned": bool(t.get("planned")),
                    "incident_count": inc_by_root.get(rid, 0) if run is not None else None,
                    "manifest_sha256": fsha,
                }
            )
    return dims, trials, errors


def load_manifests(files):
    out, errors = [], {}
    for f in files:
        with open(f, "rb") as fh:
            raw = fh.read()
        try:
            m = json.loads(raw.decode("utf-8-sig"))
        except ValueError as e:
            errors[os.path.basename(f)] = [f"invalid JSON: {e}"]
            continue
        out.append((os.path.basename(f), hashlib.sha256(raw).hexdigest(), m))
    return out, errors


def _schema():
    import pyarrow as pa

    ts = pa.timestamp("us")
    dim = pa.schema([(c, ts if c == "preregistered_at" else pa.string()) for c in DIM_COLUMNS])
    typ = {
        "randomization_seed": pa.int64(),
        "success": pa.bool_(),
        "early_stop": pa.bool_(),
        "kills_at_cap": pa.int32(),
        "run_linked": pa.bool_(),
        "planned": pa.bool_(),
        "incident_count": pa.int32(),
    }
    trial = pa.schema([(c, typ.get(c, pa.string())) for c in TRIAL_COLUMNS])
    return dim, trial


def _write(rows, cols, schema, path):
    import pyarrow as pa
    import pyarrow.parquet as pq

    tbl = pa.Table.from_pylist([{c: r.get(c) for c in cols} for r in rows], schema=schema)
    pq.write_table(tbl, path + ".tmp", compression="zstd")
    os.replace(path + ".tmp", path)


def run(out_dir, snapshot_id, files=None, log=print):
    import datetime

    import duckdb

    files = manifest_files() if files is None else files
    manifests, errors = load_manifests(files)
    con = duckdb.connect()
    try:

        def t(name):
            f = os.path.join(out_dir, name + ".parquet")
            if not os.path.exists(f):
                return []
            cur = con.execute(f"SELECT * FROM read_parquet('{f}')")
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]

        runs, incidents = t("fact_runs"), t("fact_incidents")
    finally:
        con.close()
    dims, trials, errs = build_rows(manifests, runs, incidents, snapshot_id)
    errors.update(errs)
    for d in dims:
        if d["preregistered_at"]:
            d["preregistered_at"] = datetime.datetime.fromisoformat(
                d["preregistered_at"].replace("Z", "+00:00")
            ).replace(tzinfo=None)
    dim_s, trial_s = _schema()
    _write(dims, DIM_COLUMNS, dim_s, os.path.join(out_dir, "dim_experiment.parquet"))
    _write(trials, TRIAL_COLUMNS, trial_s, os.path.join(out_dir, "fact_experiment_trials.parquet"))
    for f, e in errors.items():
        log(f"[experiments] WARNING skipped {f}: {'; '.join(e)}")
    log(
        f"[experiments] {len(files)} manifests, {len(dims)} cells, {len(trials)} trials, {len(errors)} invalid"
    )
    return {
        "manifests": len(files),
        "cells": len(dims),
        "trials": len(trials),
        "invalid": sorted(errors),
    }
