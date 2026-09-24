"""CLI: python -m ihub {fast|logs|backfill|model|catalogue|compact|export|publish} [options].

  fast                    catalog order book + market + status + balance (+hourly config) -> model
  logs                    request logs since watermark - overlap (default 30 min) -> model ->
                          route catalogue (catalogue/route-catalogue.{json,csv}) -> compact
                          finished UTC days of the Parquet parts
  backfill --since ISO    request logs back to a time (e.g. 2026-09-24T04:00:00Z = midnight ET)
  model                   rebuild the modeled tables only
  catalogue               regenerate the reliability-adjusted route catalogue only (IRE #46 M4)
  compact                 merge the Parquet parts of finished UTC days (one file per table/day)
  export [--day D]        write the curated daily snapshot CSV + manifest (ET day, default today)
  publish [--day D]       export, then commit + push to the data branch (see export.py)
Runs are serialised with a non-blocking flock (state/ihub.lock); a busy lock skips the run.
"""

from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import json
import os
import resource
import sys
import time

from . import collect, transform


def _catalogue(rec: dict) -> int:
    from . import catalogue, export

    try:
        rec["catalogue"] = catalogue.run(collect.IH, export._git_sha())
    except Exception as e:  # the previous catalogue stays in place
        rec["catalogue_error"] = f"{type(e).__name__}: {e}"
        return 1
    return 0


def _compact(rec: dict) -> int:
    from . import lake

    today = dt.datetime.now(dt.timezone.utc).date().isoformat()
    try:
        rec["compact"] = lake.compact_finished(lake.connect(), collect.PQ, before_day=today)
    except Exception as e:  # parts stay as they are; retried next run
        rec["compact_error"] = f"{type(e).__name__}: {e}"
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="ihub")
    ap.add_argument(
        "cmd",
        choices=["fast", "logs", "backfill", "model", "catalogue", "compact", "export", "publish"],
    )
    ap.add_argument("--since")
    ap.add_argument("--day")
    ap.add_argument("--no-model", action="store_true")
    ap.add_argument("--force-model", action="store_true")
    a = ap.parse_args(argv)
    os.makedirs(collect.STATE, exist_ok=True)
    lock = open(os.path.join(collect.STATE, "ihub.lock"), "w")  # noqa: SIM115
    try:
        fcntl.flock(
            lock, fcntl.LOCK_EX | (0 if a.cmd in ("backfill", "publish") else fcntl.LOCK_NB)
        )
    except BlockingIOError:
        print("ihub: another run holds the lock; skipping", file=sys.stderr)
        return 0
    t0 = time.time()
    rec: dict = {"cmd": a.cmd, "started_at": collect.now_iso()}
    rc = 0
    if a.cmd in ("export", "publish"):
        from . import export

        rec.update(export.run(day=a.day, publish=(a.cmd == "publish")))
    elif a.cmd == "catalogue":
        rc = _catalogue(rec)
    elif a.cmd == "compact":
        rc = _compact(rec)
    else:
        ctx = collect.Ctx()
        rec["run_id"] = ctx.run_id
        if a.cmd == "fast":
            collect.fast(ctx)
        elif a.cmd == "logs":
            collect.logs(ctx)
        elif a.cmd == "backfill":
            if not a.since:
                ap.error("backfill needs --since")
            since = transform.parse_ts(a.since)
            assert since is not None
            collect.logs(ctx, since=since, until_pages=200)
        rec["tables"] = ctx.stats
        rec["api_calls"] = ctx.client.calls
        rec["errors"] = ctx.errors
        if a.cmd == "model" or a.force_model or (not a.no_model and ctx.new_rows() > 0):
            from . import model

            try:
                rec["model"] = model.build(ctx.run_id)
            except Exception as e:  # keep the previous snapshot current
                rec["model_error"] = f"{type(e).__name__}: {e}"
                rc = 1
        if ctx.errors and not any(v.get("new") for v in ctx.stats.values() if isinstance(v, dict)):
            rc = rc or 2
        if a.cmd == "logs":
            # every 15 min: agents read the catalogue; compaction is a no-op until a UTC day ends
            rc = _catalogue(rec) or rc
            rc = _compact(rec) or rc
    rec["seconds"] = round(time.time() - t0, 2)
    rec["peak_rss_mb"] = round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1)
    rec["finished_at"] = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    os.makedirs(collect.META, exist_ok=True)
    with open(os.path.join(collect.META, "runs.jsonl"), "a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, sort_keys=True, default=str) + "\n")
    print(json.dumps(rec, sort_keys=True, default=str))
    return rc


if __name__ == "__main__":
    sys.exit(main())
