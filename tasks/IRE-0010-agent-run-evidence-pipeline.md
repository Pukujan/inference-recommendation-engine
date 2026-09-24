# IRE-0010 — Agent run evidence pipeline: source in GitHub, provider-error capture, incident detectors

GitHub issue: [#41](https://github.com/Pukujan/inference-recommendation-engine/issues/41),
part of [#40](https://github.com/Pukujan/inference-recommendation-engine/issues/40).

## Status

- Checkpoint 1 (source sync): in review. P2/P3 were built and ticked on #41 while their code lived only on
  the self-hosted host; this checkpoint puts that code in GitHub and makes the host deploy from `main`.
- Checkpoint 2 (provider-error capture, #40 M0.6 context): pending.
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

## Files in scope

- `operational/telemetry/**`
- `src/check-public.mjs`
- `tasks/IRE-0010-agent-run-evidence-pipeline.md`
- `checkpoints/CURRENT.md`

## Acceptance criteria

- Repository gates pass; required CI check `test` passes.
- The host has a checkout at `/srv/agent-telemetry/src` tracking `main`; `deploy.sh` runs cleanly and the
  next timer-driven pipeline run succeeds.
