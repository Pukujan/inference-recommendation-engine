# Host notes (verbatim operator notes from /srv/agent-telemetry/README.md, IPs redacted)

> Deploy/update instructions live in `README.md` next to this file. These notes describe how the host
> was set up on 2026-09-24 and are kept for reference.

# agent-telemetry (gravebuster)

> **2026-09-24 update (IRE #41 P2/P3):** the batch pipeline in `pipeline/` (systemd timer `agent-telemetry-pipeline.timer`, every 15 min)
> now owns storage: sealed zstd raw segments -> deduped raw Parquet -> clean Parquet -> dbt-duckdb modeled snapshots under `data/lake/`.
> `agent-telemetry-loader.service` is **stopped and disabled** after verified 100% parity; `data/sqlite/traces.sqlite3` is a frozen archive.
> Query with `bin/at-duck.py` (`trace <tid> --live` sees new data within ~1s; `bin/verify-trace.sh` uses it). The SQLite sections below are historical.
> Details, identity-scrub notes and measurements: `pipeline/README.md`.

Set up 2026-09-24 for IRE issue 40. Collects OpenTelemetry from Alex's PC (tailnet client) coding
harnesses (Codex / Astra launchers, Claude Code, OpenCode) and fans it out to Langfuse, Phoenix and an
append-only SQLite ledger. Nothing runs on the PC except per-run launcher code (no daemons).

## Layout
    /srv/agent-telemetry/
      docker-compose.yml        # one stack, project name "agent-telemetry"
      .env                      # chmod 600: Langfuse/Postgres/ClickHouse/MinIO/Redis settings (never commit)
      collector/config.yaml     # otelcol-contrib config (no literal secrets)
      collector/langfuse-auth.env   # chmod 600: LANGFUSE_OTEL_BASIC_AUTH=base64(pk:sk) for project "cortex"
      loader/loader.py          # stdlib tailer -> SQLite  (systemd: agent-telemetry-loader.service)
      data/otel/                # file-exporter OTLP JSON lines: traces.jsonl, logs.jsonl, metrics.jsonl (+ rotated)
      data/sqlite/traces.sqlite3    # append-only ledger (spans / logs / metrics)
      bin/                      # verify-trace.sh, at-query.py, lf-keycheck.sh, pg-set-password.sh,
                                #   verify-inferhub-filter.py, verify-identity-redaction.py
      langfuse/docker-compose.upstream.yml   # upstream reference copy only (not used)

## Services and ports (host binds 0.0.0.0)
| service | host port | notes |
|---|---|---|
| otel-collector (0.155.0) | 4317 gRPC, 4318 HTTP, 13133 health | receivers for all harnesses |
| langfuse-web v3 (3.212.0) | 3000 | UI + /api/public/otel |
| phoenix | 6006 | UI + OTLP/HTTP; Phoenix gRPC 4317 is NOT published (collector owns 4317) |
| langfuse-worker, postgres 17, clickhouse, redis 7, minio | none | internal network only (9090 is Cockpit on this host) |

UIs (from the tailnet): Langfuse http://<collector-tailnet-ip>:3000  -  Phoenix http://<collector-tailnet-ip>:6006
(gravebuster's own tailnet address is <host-tailnet-ip>; Alex's tailnet sees the shared node as <collector-tailnet-ip>.)

## Data flow
    harness --OTLP--> collector :4317/:4318
        -> memory_limiter -> filter/inferhub-only -> transform/identity -> transform/redact -> redaction -> batch
        traces/ledger -> file/traces  -> loader -> SQLite (everything)
        traces/ui     -> filter/ui-noise -> Langfuse (/api/public/otel, Basic auth) + Phoenix (:6006/v1/traces)
        logs, metrics -> file/logs, file/metrics -> SQLite   (Langfuse/Phoenix accept traces only)

filter/ui-noise drops only Codex's orphan root spans `persist_rollout_items` and
`realtime_conversation.running_state` (thousands per run) from the UI sinks; they remain in SQLite.

## Redaction (collector)
* Deletes attribute keys matching `authorization|x-api-key|api[._-]?key|secret|password|passwd|cookie|credential`
  and bare/auth token keys (`token`, `access_token`, `id_token`, `auth.token`, ...), on resource, span,
  span-event, log and datapoint attributes.
  Deliberate deviation from a blanket `*token*` rule: token COUNT keys (`input_tokens`, `gen_ai.usage.output_tokens`,
  `claude_code.token.usage`) are kept, because they are the usage data.
* Masks values containing `sk-`, `sk-lf-`, `pk-lf-`, `ghp_/gho_/ghu_/ghs_/ghr_`, `github_pat_`, `Bearer ...`,
  `Basic ...` in attributes, span names and string log bodies -> `***REDACTED***`, plus the contrib
  `redaction` processor as a second pass.

## SQLite ledger
Tables `spans`, `logs`, `metrics` with trace_id, span_id, parent_span_id, service_name, harness, topology,
task_id, correlation_id, ire_issue, start/end (ISO + unix nanos), status, attributes / resource_attributes
JSON, source_file/offset. Triggers `*_no_update` / `*_no_delete` ABORT any UPDATE/DELETE.
`loader_state` (inode + offset per signal) is committed in the same transaction as rows, so restarts
neither lose nor duplicate data; rotated files (lumberjack, 100 MB, 20 backups, 30 days) are finished by inode.

    python3 bin/at-query.py stats
    sudo python3 bin/at-query.py trace <trace_id>     # sudo only to read Phoenix's DB in its docker volume
    python3 bin/at-query.py corr astra-<receipt-stamp>
    bin/verify-trace.sh <trace_id>                     # SQLite + Phoenix API + Langfuse API

## Operations
    cd /srv/agent-telemetry
    docker compose ps
    docker compose logs -f otel-collector
    docker compose up -d                  # (re)start everything; restart: unless-stopped for boot
    systemctl status agent-telemetry-loader   # enabled at boot
    curl -s localhost:13133/              # collector health
    curl -s localhost:3000/api/public/health

## Langfuse notes
* Restored the pre-existing `cortex-langfuse_*` volumes (July 2026 data: projects "Cortex Runs" and
  "cortex"); declared `external` in compose, so `docker compose down -v` will NOT delete them.
* The original compose/.env was gone; the Postgres role password was rotated with bin/pg-set-password.sh
  (value only in .env). SALT / ENCRYPTION_KEY must stay equal to the values the restored volumes were
  initialised with (API-key hashes depend on SALT); the PC's existing project keys authenticate fine.
* Images are the local `langfuse/langfuse:3` and `langfuse-worker:3` (upstream main is now v4; do not
  switch to :4 without a backup). ClickHouse/Phoenix use the local `:latest` images; a `docker pull` would
  upgrade them.

## Exposure
Ports are bound on 0.0.0.0. ufw is inactive and the iptables INPUT policy is ACCEPT, so the services are
reachable from the tailnet AND from the LAN (LAN interface), the same as the host's other docker
services. There is no public IP on the host (behind NAT); router port-forwards were not checked.
Phoenix and the OTLP receivers have no auth. To restrict to Tailscale only, change the binds to
<host-tailnet-ip>:PORT (note: docker then needs tailscaled up before it starts).

## PC side (for reference)
* D:\claude\_workspace\telemetry\astra_otel.py + astra-telemetry.ps1, dot-sourced by
  D:\claude\_workspace\pcm-astra-owner\launch-astra.ps1 / resume-astra.ps1 (root span per run, TRACEPARENT
  export, Codex --config otel overrides).
* %USERPROFILE%\.claude\settings.json env block, %USERPROFILE%\.config\opencode\opencode.json.
* Backups: *.bak-20260924 and D:\claude\_workspace\telemetry-backup-20260924.txt.

## InferHub-only scope (2026-09-24)
`filter/inferhub-only` (collector/config.yaml, first processor after memory_limiter in ALL pipelines) drops every
span/log/metric whose resource lacks `llm.provider=inferhub`, so Langfuse, Phoenix, the file exporters and
therefore SQLite only receive InferHub-key runs. The Astra launcher helper
(D:\claude\_workspace\telemetry\astra-telemetry.ps1 on the PC) sets `llm.provider=inferhub,
llm.base_url_host=api.inferhub.dev, llm.model_route=<model>` in OTEL_RESOURCE_ATTRIBUTES for every run.
Other OTel-aware tools on the PC still export (user env OTEL_EXPORTER_OTLP_ENDPOINT=http://<collector-tailnet-ip>:4318
predates this stack) and are dropped here. Previous config: collector/config.yaml.bak-20260924-preinferhub.
Verify: `python3 bin/verify-inferhub-filter.py` then `bin/verify-trace.sh <id>` for each id (tagged kept, untagged absent).
Receipts copied read-only from the PC (for historical analysis): data/receipts/<stamp>/ (UTF-16LE files).

## Identity scrub (2026-09-24)
`transform/identity` (collector/config.yaml; right after filter/inferhub-only, before transform/redact, in ALL
pipelines, so before every exporter) handles identity attributes that Codex attaches to logs/spans:
* `user.email`, `user.account_id` -> `sha256:<64 hex>` (unsalted SHA-256 of the original string; keeps per-user
  correlation, but a known email can be confirmed by hashing it). Non-string values, or anything that did not end up
  as a `sha256:` hash, are deleted (fail closed). Already-hashed values are not re-hashed.
* Any attribute key matching `(?i).*tailscale[._-]?user.*` is deleted (e.g. `http.request.header.tailscale-user-login`,
  `-user-name`, `-user-profile-pic`).
* Applies to resource, scope, span, span-event, log and datapoint attributes. String log BODIES are not scanned for
  emails.
Previous config: collector/config.yaml.bak-20260924-redact.
Verify: `python3 bin/verify-identity-redaction.py` (inferhub-tagged span+log, task.id=telemetry-verify-redaction, with
user.email / user.account_id / tailscale headers on resource, span, event and log), then `bin/verify-trace.sh <id>`
or `sqlite3 data/sqlite/traces.sqlite3 "select attributes, resource_attributes from logs where trace_id='<id>'"`:
only `sha256:` values, no tailscale keys.
NOTE: rows ingested BEFORE this change (Codex logs on 2026-09-24, ~2044 rows in `logs`, plus data/otel/logs.jsonl
and rotated copies) still contain plaintext user.email / user.account_id; the ledger is append-only (triggers), so
they were left as-is.
