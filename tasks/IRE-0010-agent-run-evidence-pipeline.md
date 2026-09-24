# IRE-0010 — Agent run evidence pipeline: source in GitHub, provider-error capture, incident detectors

GitHub issue: [#41](https://github.com/Pukujan/inference-recommendation-engine/issues/41),
part of [#40](https://github.com/Pukujan/inference-recommendation-engine/issues/40).

## Status

- Checkpoint 1 (source sync): merged in PR #42 at `2c45e74be95be9241fb2489abd195462af5a0450`; required
  check `test` passed. Deployed to the collector host from `main` with `deploy.sh`
  (`DEPLOYED sha=2c45e74…`); the next pipeline run succeeded. P2/P3 code is now in GitHub.
- Checkpoint 2 (provider-error capture, #40 M0.6 context): in review.
- Checkpoint 3 (P4 incident detectors + versioned signature catalog): pending.

## Goal

GitHub owns the collector, pipeline and launcher helper source under `operational/telemetry/`. The
telemetry host runs whatever `main` holds, deployed by `operational/telemetry/gravebuster/deploy.sh`.

## Decisions

- Secrets stay on the host: compose uses `${VARS}`; `env.example` and `langfuse-auth.env.example` carry
  placeholders only. The file is named `env.example` because the checkpoint helper refuses `.env*` paths.
- No data, receipts, virtualenv, or `*.bak-*` files are committed. A gitleaks scan and the checkpoint
  helper's credential scan run before publication.
- The public-surface check allows the integration name under `operational/telemetry/`, which configures
  that integration by design.
- Deploys never delete host files that are not in the repository, so host-only modules survive.
- Python is formatted to the repository's ruff gates; changes are formatting and lint only (imports
  split, two lambdas turned into functions, two variables renamed), plus `git_head()` reading `DEPLOYED`.

## Checkpoint 2: provider-error capture

- Codex spans carry no response body, and the span of a failing request is usually lost when the
  process exits before its exporter flushes, so the provider code only survives in the receipt.
- `astra_otel.py end` now scans `codex-events.jsonl`, `codex-stderr.txt` and `launcher-error.txt`
  (from `--receipt-dir`, which `Stop-AstraTelemetry` passes) and adds `provider.error.*` attributes
  (count, occurrences, code, type, http_status, message, request id, retry hint, request-shape counts,
  receipt source file:line) plus one `provider.error` span event per distinct request. `scan` prints the
  same result without network access; `end --dry-run` prints the span.
- Only `error` / `turn.failed` / `stream_error` events count; tool output that mentions a code is ignored.
- Verified against real receipts on the PC and end-to-end through the collector with two synthetic
  root spans (task ids `telemetry-verify-11133-*`). No Astra run was launched.

## Files in scope

- `operational/telemetry/**`
- `src/check-public.mjs`
- `operational/tests/test_astra_otel_provider_errors.py`
- `tasks/IRE-0010-agent-run-evidence-pipeline.md`
- `checkpoints/CURRENT.md`

## Acceptance criteria

- Repository gates pass; required CI check `test` passes.
- The host has a checkout at `/srv/agent-telemetry/src` tracking `main`; `deploy.sh` runs cleanly and the
  next timer-driven pipeline run succeeds.
