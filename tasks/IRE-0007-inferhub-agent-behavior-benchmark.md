# IRE-0007 — Blind BYOK coding-agent behavior benchmark

GitHub issue: [#29](https://github.com/Pukujan/inference-recommendation-engine/issues/29),
a child of [#8](https://github.com/Pukujan/inference-recommendation-engine/issues/8).

## Status

Issue #29 records the PDD, SDD, and TDD for the benchmark. This first
documentation checkpoint makes the model-price preference explicit in the
canonical BYOK runbook and corrects the issue's route-selection criteria
before any model runs. No inference request or credential access is part of
this checkpoint.

## Goal

Determine whether a fresh coding agent can discover and follow the repository's
BYOK model, tool-access, streaming, retry, delegation, and handoff guidance from
an ordinary coding task, using blinded tasks and objective private evidence.

## Durable model-selection rule

- For this project, current estimated model/route cost below $0.10 USDC per 1
  million tokens means “effectively free” for recommendation and long-running
  experiment selection. This does not mean zero-cost.
- Prefer currently recommendation-eligible entries in the project's Top 20 or
  Top 20+ lists when below that threshold. Verify the live route, route-level
  price, recommendation eligibility, and tool/stream support at selection
  time; record the snapshot and route used.
- Keep recommended/eligible routes distinct from research-only comparison
  routes. Do not call an unavailable, ineligible, or more expensive route free.
- The canonical operating guide is `docs/INFERHUB-API-SETUP.md`, linked from
  `AGENTS.md`; issue #29 owns the benchmark plan and results.

## Benchmark design

The GitHub issue contains the Product Design Document (PDD), System Design
Document (SDD), and Technical Design/Test plan (TDD), including blind task
criteria, golden agent topologies, evidence rules, and pass criteria. Runs use
remote inference through configured APIs only; no local model inference.
Credentials, prompts, source snapshots, raw event streams, and provider usage
receipts remain in the approved private receipt store.

## Files in scope for the documentation checkpoint

- `docs/INFERHUB-API-SETUP.md`
- `tasks/IRE-0007-inferhub-agent-behavior-benchmark.md`
- `checkpoints/CURRENT.md`
- `src/check-public.mjs`

## Acceptance criteria for this checkpoint

- The runbook defines the project's under-$0.10-per-million effective-free
  threshold, says that inference remains billed, and prefers eligible Top 20
  or Top 20+ entries subject to live validation.
- Issue #29 uses consistent terms and no longer requires a route to be exactly
  $0 or ambiguously labels research-only routes as eligible.
- The task/checkpoint records point to issue #29 and leave the benchmark as the
  next action after this documentation increment merges.
- Repository-required local gates and required GitHub CI pass; changes are
  delivered with the checkpoint helper using explicit paths.

## Checkpoint log

- 2026-09-24: Verified that the general BYOK runbook already durably records
  API URLs, secret-source handling, full authorized tool access, nested CLI and
  subagent routing, streaming/liveness, retry/resume, and GitHub handoff
  requirements. Found the under-$0.10 definition was missing, and issue #29
  still said “free eligible.”
- 2026-09-24: Added the effective-free threshold and Top 20/Top 20+ preference
  to the canonical runbook. Corrected issue #29 PDD/SDD/TDD before any model
  run. No model request or credential access occurred.
- 2026-09-24: Documentation checkpoint PR [#30](https://github.com/Pukujan/inference-recommendation-engine/pull/30)
  merged at `5dba69d3c666785057400065cc3677d4288b1dd0` after both required CI
  checks passed. `node scripts/finalize-checkpoint.mjs --pr 30` synchronized
  the canonical `main` checkout and removed only its verified local branch.
- 2026-09-24: Local validation passed: `pnpm check:static`, `pnpm test` (26),
  `pnpm check:public`, `pnpm test:operational` (41), `pnpm pack --dry-run`,
  and `git diff --check`. No model request or credential access occurred.

## Next action

Prepare the blinded benchmark fixture, current eligible route selection,
private receipt destination, and verifier under issue #29 before requesting
any inference. Then run the documented benchmark and append observed results
to the issue and durable task/checkpoint records.
