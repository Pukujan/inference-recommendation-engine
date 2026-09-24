# agent-telemetry batch pipeline (IRE #41, P2 + P3)

A short batch job that exits when done; no server stays running. Every 15 minutes it:
**seals** collector output into immutable compressed raw segments, **dedups** them into raw Parquet,
rebuilds the typed **clean** Parquet, and builds a **modeled** star schema with dbt-duckdb into
an input-addressed snapshot. DuckDB is capped at 1.5 GB and 2 threads, and the unit is capped at `MemoryMax=2G`.

```
data/otel/{traces,logs,metrics}.jsonl[.rotated]   collector file exporters (source of truth; rotate at 100 MB)
data/sqlite/traces.sqlite3                          old loader ledger; FROZEN archive since the loader was retired (see below)
data/receipts/<stamp>/                              Astra launcher receipts (UTF-16LE/UTF-8)
data/lake/
  raw/segments/signal=S/date=D/seg-*.jsonl.zst      verbatim sealed byte ranges of collector files (zstd 12, verified before publish)
  raw/manifest.jsonl                                append-only: sha256, bytes, zst_bytes, lines, first/last ts, final flag
  raw/{spans,logs,metrics}/day=D/part-<etl>.parquet deduped canonical records (zstd 9), append-only parts
  raw/conflicts/<table>/part-*.parquet              same key, different content (kept_content_hash recorded)
  clean/{spans,logs}/day=D/data.parquet             typed columns, run_key, noise flags (rebuilt for touched days)
  clean/receipts/receipt_{runs,events,files}.parquet receipt metadata only (no prompts / outputs)
  modeled/snap-<id16>/*.parquet + _SUCCESS + _inputs.json, modeled/current -> newest, snapshots.jsonl
  meta/etl_runs.jsonl                               one record per run: counts, scrub stats, leak check, RSS, versions
pipeline/
  atpipe/ (seal, parse, ingest, clean, receipts, run)   dbt/ (models, seeds/noise_rules.csv, macros)
  systemd/agent-telemetry-pipeline.{service,timer}      state/ (ingested segments, sqlite backfill ids)   venv/
bin/at-duck.py                                       read-only query helper over the lake
```

## Stages
1. **seal**: reads only files that are fully written. For the active file it takes the prefix up to the last complete line. For a rotated file it takes the tail, then marks the file `final`. Output goes to a new `.jsonl.zst`, which is decompressed and checked against its sha256 before it is published. Each source file is identified by (signal, inode, sha256 of its first 4 KB), so a rotated file cannot be mistaken for the new active file. `--prune-rotated` deletes a rotated file only after it is fully sealed and final, and only while the old loader is inactive.
2. **ingest**: parses the segments not ingested yet, plus any SQLite rows above the last backfilled id. Both are converted to canonical records compatible with the loader. Rows are staged in DuckDB and classified against the existing raw keys:
   - key unknown → `new`
   - key known, same `content_hash` → `dup` (dropped)
   - key known, different hash → `conflict`, written once to raw/conflicts. The kept row is the one from the higher-priority source: collector file 1 beats SQLite 2, then earliest.
   Keys:
   - span: `trace_id:span_id`
   - log: sha256(trace, span, time, observed_time, severity_number, event_name, body, attributes)
   - metric: sha256(name, type, time, start, attributes, resource)
   `content_hash` covers the core fields the loader also keeps. Extra OTLP fields (flags, links, scope version...) go into `extras_json`, which is not hashed, so file rows and SQLite rows compare equal.
3. **clean**: rebuilds only the days that changed. It adds typed columns (timestamps, duration, status, service/harness/topology/task/IRE issue, `run_key` = correlation_id | `opencode:`run | service:instance | `trace:`id, and run.* launcher fields). It also recovers the UTF-16LE mojibake in `run.stderr_tail`. For logs, prompt, output and arguments are dropped and only `*_bytes` / `*_sha256` are kept. Noise flags come from the seed.
4. **receipts**: one row per receipt dir, event and file. Fields: outcome (launcher_error / killed / completed / failed / no_terminal_event), tokens, the parsed InferHub 11133 error (requestId, message count, tool count, empty-content count, additionalProperties count, max description size), and receipt_sha256.
5. **leak check**: scans every raw and clean Parquet table for email addresses and for the known plaintext identity values. If anything is found, the run exits with code 3.
6. **dbt build** (only when the input hash changes): writes staging views, then external Parquet marts. The build goes to a temp dir; `_SUCCESS` is written and the result renamed to `snap-<id16>`, then `current` is swapped atomically. The last 10 snapshots are kept, and any listed in `modeled/pins.txt` are never pruned.

**snapshot_id** = sha256 of the canonical JSON of:
- the ingested segment sha256s;
- the SQLite backfill state;
- the receipts set hash;
- the sha256 of the pipeline code (atpipe + dbt);
- the git HEAD of pipeline/;
- the duckdb, dbt and python versions.
The same inputs always give the same id, and a rerun with unchanged inputs skips the build.

## Identity scrub (matches collector `transform/identity`)
Since 2026-09-24 ~21:36 UTC (17:36 ET), the collector hashes `user.email` and `user.account_id` to `sha256:<hex of the plaintext>` (unsalted) and deletes every key matching `(?i).*tailscale[._-]?user.*`. Older data still holds plaintext: about 2044 SQLite log rows, the older part of `logs.jsonl`, and older OpenCode spans with `tailscale-user-*` headers. During parsing, ingest applies **the same transform** to resource, scope, span, span-event, log and datapoint attributes:
- values that are already hashed are left alone;
- values that are not strings are deleted, as the collector does;
- the same unsalted sha256 is used, so pre- and post-redaction rows join on the same `user.email` value.
Any email address found anywhere else in a string (bodies, messages) is replaced with `sha256:<hex>` as well. Scrub counts are recorded in `etl_runs.jsonl`. The first backfill hashed 8176 values and deleted 174 keys. The leak check found 0 email rows and 0 known-plaintext rows in raw and clean.
**Caveat:** the sealed `raw/segments/*.jsonl.zst` files are *verbatim* byte copies of the collector files, so they are an audit trail and can be re-parsed. The segments sealed from files written before redaction therefore still contain plaintext identity, as the source files do. The frozen SQLite file does too. Parquet (raw/clean/modeled) contains none. To purge the old plaintext, delete those segments and the SQLite file once you no longer want the audit copy. Parquet can be rebuilt with `--reingest-all` from the post-redaction files only.

## Modeled layer (dbt-duckdb)
dbt was chosen because it fits on this host. Measured on gravebuster: dbt-core 1.12.5 + dbt-duckdb 1.11.0 add ~380 MB to the venv, and `dbt --version` peaks at 139 MB RSS. A full dbt build stays inside the 1.5 GB DuckDB cap. It is invoked in-process from `atpipe.run`.

| table | grain | notes |
|---|---|---|
| fact_runs | one process run (launcher, codex child, opencode, verify) + receipt-only runs | outcome, exit/killed, wall_s, tokens, n_model_requests/errors, provider_error_code, n_tool_calls, max_idle_gap_s, receipt link, trace_ids, evidence span ids |
| fact_hops | launcher → child edge | only from telemetry |
| fact_model_requests | one model call | telemetry spans + orphan `codex.api_request` logs + receipt `turn.failed`; `is_primary` prevents double counting; 11133 request-shape features |
| fact_tool_calls | one tool call | telemetry `codex.tool_result` + receipt `command_execution`; `is_primary` |
| fact_idle_gaps | gaps between liveness events | the rank-1 gap per run plus every gap ≥30 s; noise that fires while idle (N001–N003, N009, N010) does not count as liveness |
| dim_route / dim_model / dim_harness / dim_task / dim_github_ref | md5 surrogate keys | |
| fact_incidents | one detected incident | written by `atpipe/detectors.py` after dbt; see "Incident detectors" below |
| dim_experiment / fact_experiment_trials | experiment cell / trial | written by `atpipe/experiments.py` from trial manifests; empty (schema only) until a manifest exists |
| model_snapshot | 1 row | snapshot_id, inputs_hash, built_at |

`schema.yml` defines unique and not_null tests on keys, and `dbt build` runs them.

## Incident detectors (P4)
`atpipe/detectors.py` runs after dbt on every newly built snapshot and rewrites `fact_incidents.parquet` in it. Each detector is a pure function over modeled facts (runs, hops, model requests, tool calls, idle gaps), clean receipts and `provider.error.*` attributes on launcher root spans. Harness-specific marts with the same grain are unioned into the same inputs (`kilo_fact_runs/_hops/_model_requests/_tool_calls/_idle_gaps` from `atpipe/ingest_kilo.py`), so every signature also runs over Kilo sessions and sub-agents; Kilo evidence ids appear as `evidence:<id>`.

- **Catalog:** `detectors/catalog.json` (`ire-incident-signatures/v1`). Per signature: `incident_type`, `detector_version` (`<type>/<n>`), severity, MAST mode (or `infra: ...`), thresholds (`params`), description and a proposed fix. Every fix starts as `proposed`; only a verification experiment may change that. The catalog version and sha256 are snapshot inputs and are stamped on every incident row. Detectors and catalog must match one-to-one (`validate_catalog`).
- **Ids:** `incident_id` = `INC-` + sha256(type, run, hop, evidence refs); `fingerprint` (`fp/v1`) = sha256 of the type plus signature-specific fields (provider, route, harness, code, status...), so recurring failures share a fingerprint across runs.
- **Evidence:** `evidence_refs` cites `run:<run_id>`, `span:<trace>/<span>`, `trace:<trace>`, `receipt:<stamp>/<file>:L<line>` (1-based physical line in the receipt file), `gap:`, `hop:` and `tool_call:` ids. `metrics_json` holds bounded, text-free metrics.
- **Exclusions:** pipeline verification traces (`task.id` starting `telemetry-`, harness `verify`, e2e/wrapper test runs) are not agent runs and are skipped.
- **Signatures (v1):** unjustified_kill, terminated_without_closeout, provider_param_rejection, blind_resend, resume_retry_churn, launcher_error_or_early_exit, unattributed_failure, supervisor_polling_loop, long_idle_gap, permission_denied, unverified_hop, one_shot_capture.
- `receipt_events` carries `line_no` and a coarse `command_class` (sleep_wait, status_poll, vcs_read, forge_read, file_read, search, build_test, ...; `atpipe/cmdclass.py`). Command text is still not stored.

Example queries:
```sql
SELECT incident_type, severity, count(*) FROM 'data/lake/modeled/current/fact_incidents.parquet' GROUP BY ALL ORDER BY 3 DESC;
SELECT fingerprint, incident_type, count(DISTINCT root_run_id) runs FROM 'data/lake/modeled/current/fact_incidents.parquet' GROUP BY ALL HAVING runs > 1;
SELECT run_role, harness, incident_type, count(*) FROM 'data/lake/modeled/current/fact_incidents.parquet' GROUP BY ALL;  -- main vs child
```

## Experiment manifests (#40 M0.5)
Drop one JSON file per preregistered experiment into `$AT_ROOT/data/experiments/` (schema `experiments/trial-manifest.schema.json`, example `experiments/example.trial-manifest.json`). The next pipeline run hashes the manifest set into the snapshot inputs, writes one `dim_experiment` row per cell and one `fact_experiment_trials` row per trial, links trials to runs by `run_id` or launcher `receipt_stamp`, and fills `kills_at_cap` / `incident_count` from `fact_incidents`. `success` comes from the manifest, else from the run outcome. Invalid manifests are skipped with a warning in the run log. With no manifest both tables stay empty; rows are never invented.

## Noise rules (`dbt/seeds/noise_rules.csv`)
- N001 persist_rollout_items, N002 realtime_conversation.running_state, N003 append_items: Codex housekeeping, ~32k spans per run. They are **not** liveness.
- N004 receiving, N005 handle_responses: per-SSE-chunk spans; noise, but they prove the stream is alive, so they count as liveness.
- N006–N008: logs of stream delta events (liveness).
- N009 "flushing OTEL metrics": not liveness.
- N010 OpenCode `http.server GET` UI polling: not liveness.
Noise rows are kept in raw and clean with `is_noise` and `noise_rule`. To change the rules, edit the CSV; this changes the code hash and triggers a rebuild.

## Operations
```
systemctl list-timers agent-telemetry-pipeline.timer    # *:0/15, Persistent=true
journalctl -u agent-telemetry-pipeline -n 30
cd /srv/agent-telemetry/pipeline && /usr/bin/time -v venv/bin/python -m atpipe.run     # manual run (flock-guarded)
    --reingest-all   re-parse every segment + SQLite (dedup proves idempotence)
    --rebuild-clean  rebuild every clean day      --force-model  rebuild the dbt snapshot
    --prune-rotated  delete sealed+final rotated collector files (the timer passes this)
bin/at-duck.py tables | stats | etl | runs-by-harness | killed | idle-gaps | errors | run <id> | trace <tid> [--live] | sql "<q>" | examples
```
Deploy: copy the code into `pipeline/`, then `git commit` in pipeline/ (HEAD is part of the snapshot id), then run once. If a run fails, `etl_runs.jsonl` records `status=error` and the previous snapshot stays `current`.

### Loader retirement
`agent-telemetry-loader.service` was **stopped and disabled** after parity was verified: every SQLite span and log key was found in raw Parquet with an identical content hash (see Measurements). The loader code stays in `loader/` and SQLite stays as a frozen, read-only archive. To roll back: `sudo systemctl enable --now agent-telemetry-loader`. It resumes from `loader_state` (inode + offset). Rows it adds are picked up as SQLite dups by the next pipeline run.
`bin/verify-trace.sh` now checks the lake plus the live collector files (`at-duck.py trace <tid> --live`, visible within ~1 s) instead of SQLite. `bin/at-query.py` still reads the frozen SQLite and prints a warning.

## Measurements (gravebuster, 2026-09-24 ~21:50 UTC)
- Input: traces.jsonl 33,751,237 B (176 lines, 41,098 spans) and logs.jsonl 2,780,070 B (114 lines, 2,059 logs). No rotated files or metrics yet.
- Counts:
  - raw (file) 41,098 spans / 2,059 logs;
  - SQLite 41,098 / 2,059;
  - staged 82,196 / 4,118;
  - deduped raw Parquet 41,098 / 2,059;
  - all SQLite rows were `dup` with the same hash (100% parity), 0 conflicts.
- Idempotence:
  - runs 2 and 3: 0 staged, 0 new, snapshot unchanged (`f3ef2ff779b1b598`), not rebuilt;
  - `--reingest-all`: 82,196 spans / 4,118 logs staged, 0 new, 0 conflicts, same snapshot.
- Peak RSS (`/usr/bin/time -v`):
  - first full run 613 MB, 49 s wall;
  - no-op run 190 MB, 0.7 s;
  - `--reingest-all` 494 MB, 7.7 s.
- On disk:
  - raw .zst segments 1,638,825 B; compression 21.8× for traces and 31.4× for logs;
  - raw Parquet 5,650,689 B (6.5× smaller than the JSON);
  - clean 3,984,511 B;
  - modeled snapshot 85,712 B.
- Per ~20k-span Codex run: ~0.8 MB zst + ~2.8 MB raw Parquet + ~2.0 MB clean.
- Rows: raw_spans 41,098, raw_logs 2,059, receipt_runs 19, receipt_events 1,040, fact_runs 30, fact_tool_calls 514, fact_model_requests 73, fact_idle_gaps 19, fact_hops 3, dim_harness 6, dim_task 6, dim_model 4, dim_route 4, dim_github_ref 1, stubs 0.

## Known gaps
- Experiments stay empty until the first #40 M0.5 manifest is registered. There are no permission-prompt, cost, resume or retry sources in fact_runs (those columns are NULL); resume churn is derived from receipt session chains by the detectors.
- `justified` in fact_incidents is always NULL: detectors do not adjudicate; that belongs to the later evidence/hypothesis records (P5).
- Receipt durations are stamp → latest file mtime; they are not precise end times.
- On any input change the marts are fully rebuilt. This is cheap at the current volume; switch to incremental builds when a build exceeds a few minutes.
- Raw Parquet part files accumulate, one per run with new rows per day. There is no compaction yet. `clean` is compacted per day.
- Hops come only from telemetry. Receipt-only launcher runs (before tracing) have no child rows.
- The telemetry `codex.api_request` 400 has no InferHub 11133 code; that code exists only in the receipt, and the span of the failing 11133 request never reached the collector. Receipts and telemetry are joined on `astra-<stamp>`.
- The sealed segments and the frozen SQLite may contain identity plaintext from before redaction (see above).
