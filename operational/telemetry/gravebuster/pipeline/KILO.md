# Kilo Code sessions (`atpipe/ingest_kilo.py`, issue #41)

Kilo Code 7.x (the VS Code extension and the `kilo` CLI) stores every session in one SQLite file on
the PC: `%USERPROFILE%\.local\share\kilo\kilo.db` (tables `session`, `message`, `part`, JSON in
`data`). The legacy `globalStorage/kilocode.kilo-code/tasks/<taskId>/` layout is not used any more.

## Scope rule

Only sessions whose model requests **all** went through the InferHub endpoint (`api.inferhub.dev`)
enter the lake. The provider of each request is the assistant message's `providerID`, resolved to an
endpoint host through the `provider.<id>.options.baseURL` block of `kilo.jsonc` (pass it with
`--config`) or the built-in provider hosts (`xai` → `api.x.ai`, `litellm` → `localhost:4000`, ...).

| scope | meaning | stored |
|---|---|---|
| `inferhub` | every request (and selected model) resolves to `api.inferhub.dev` | archive + lake |
| `excluded` | any other provider or proxy, or a mix | inventory line only |
| `unknown` | no provider recorded, or a provider id with no known endpoint | inventory line only |

## Flow

```
PC kilo.db (+ -wal) --copy--> data/kilo/_source/<snap>/   (temporary; delete after export)
ingest_kilo export --db <copy> --snapshot <snap> [--config provider.json]
    -> data/kilo/<sessionId>/session-<sha16>.json.zst  (0444, content-addressed, scrubbed)
    -> data/kilo/<sessionId>/SHA256SUMS                (append-only: json/zst sha256, bytes, snapshot)
    -> data/kilo/_inventory/<snap>.json                (every session: scope, reason, provider, errors)
atpipe.run (every 15 min) -> ingest_kilo.run() -> clean/kilo/kilo_{runs,hops,model_requests,tool_calls,events}.parquet
    -> dbt models/kilo/kilo_fact_{runs,hops,model_requests,tool_calls,idle_gaps}  (harness = 'kilo')
```

Export scrub: `common.mask_emails` plus the collector secret masks (`sk-`, `Bearer`, GitHub tokens,
key-named fields). Session file diffs (`summary_diffs`) are dropped. The marts keep no tool
arguments or outputs, only `*_bytes` and `*_sha256`.

Idempotence: an unchanged session re-exports to the same file (skipped); `build` is skipped when the
archive set hash and the module hash are unchanged; rows are keyed on `taskId#m<msg>[.s<step>|.p<part>|.e]`
and deduplicated on that key. The archive set hash is part of the snapshot inputs (`kilo_set_sha256`).

## Grain

| mart | grain | notes |
|---|---|---|
| `kilo_fact_runs` | one Kilo session | parent/root run for sub-agent sessions, outcome (completed / failed / aborted / no_terminal_event), tokens, cost, request/tool/denial counts, max idle gap |
| `kilo_fact_hops` | parent session → `task` sub-agent session | launch evidence id, subagent type, background flag, route match |
| `kilo_fact_model_requests` | one step (step-start/step-finish) of an assistant message, plus one row per errored message | tokens, cost, finish reason, HTTP status, provider error code (e.g. 11133), retryable |
| `kilo_fact_tool_calls` | one tool part | status, outcome, `permission_denied` + `denial_kind` (`user_rejected` / `auto_deny`), duration |
| `kilo_fact_idle_gaps` | gap between consecutive session events | rank-1 gap per run plus every gap ≥ `idle_gap_min_s` |

Timestamps are UTC in the lake; convert to ET in reports.

## Operations

```
cd /srv/agent-telemetry/pipeline
venv/bin/python -m atpipe.ingest_kilo export --db ../data/kilo/_source/<snap>/kilo.db --snapshot <snap>
venv/bin/python -m atpipe.ingest_kilo build [--force]      # also runs inside atpipe.run
```

Verification without touching the lake: point `AT_KILO_DIR`, `AT_LAKE` and `AT_PIPE` at a scratch
copy and pass `--scope-override <provider ids>`; delete the scratch copy afterwards.
