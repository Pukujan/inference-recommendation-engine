# Operational agent telemetry (issue #41, part of #40)

Source of the self-hosted agent-telemetry stack. GitHub `main` is the source of truth; the telemetry
host deploys from a checkout of this repository (see `gravebuster/README.md`). Nothing here is part of
the ranking core, and no data, credentials, or receipts are stored in the repository.

| Path | What | Runs on |
|---|---|---|
| `gravebuster/docker-compose.yml`, `env.example` | OTel collector, Langfuse v3, Phoenix (secrets only via `${VARS}` from an untracked `.env`) | telemetry host |
| `gravebuster/collector/config.yaml` | collector: `memory_limiter` → `filter/inferhub-only` → `transform/identity` → `transform/redact` → exporters | telemetry host |
| `gravebuster/pipeline/` | batch pipeline (`atpipe` + dbt-duckdb): sealed raw segments → deduped raw Parquet → clean Parquet → modeled star-schema snapshots | telemetry host, systemd timer every 15 min |
| `gravebuster/systemd/` | `agent-telemetry-pipeline.{service,timer}`; legacy `agent-telemetry-loader.service` (disabled) | telemetry host |
| `gravebuster/bin/` | query and verification helpers (`at-duck.py`, `verify-*.py/sh`) | telemetry host |
| `gravebuster/loader/` | retired SQLite loader, kept for rollback | telemetry host (disabled) |
| `gravebuster/deploy.sh` | deploys a checkout to `/srv/agent-telemetry`, preserving data and secrets | telemetry host |
| `pc/astra-telemetry.ps1`, `pc/astra_otel.py` | per-run root span emitter for the Astra launchers (stdlib only, no daemon) | developer PC |
| `pc/launch-astra.ps1`, `pc/resume-astra.ps1`, `pc/astra-retry11133.ps1` | Astra launchers (read the provider key from a local env file at run time; no secrets in the files) | developer PC |

The PC scripts are copies of the deployed files; machine paths and the collector's tailnet address are
the author's defaults. Update the PC copy and this directory together.

Python here follows the repository gates (`ruff check`, `ruff format --check`). Runtime dependencies of
the pipeline (`duckdb`, `pyarrow`, `dbt-duckdb`, ...) live only in the host's `pipeline/venv`
(`gravebuster/pipeline/requirements.txt`), not in the repository's uv environment.
