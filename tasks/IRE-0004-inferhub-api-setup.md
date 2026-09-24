# IRE-0004 — Document InferHub API base URL setup

GitHub issue: [#21](https://github.com/Pukujan/inference-recommendation-engine/issues/21),
a sub-issue of [#8](https://github.com/Pukujan/inference-recommendation-engine/issues/8).

## Status

The guide and scanner exception merged in PR [#22](https://github.com/Pukujan/inference-recommendation-engine/pull/22)
as `9b16c2b7964023adb3259a8a1d61da86bb3d89a1`. Required GitHub CI passed.
This follow-up records final verification before closing issue #21.

## Goal

Record how to configure OpenAI-compatible and Anthropic-compatible clients to
use InferHub, including the different base URL forms and safe API key handling.

## Decisions

- OpenAI-compatible SDKs use `https://api.inferhub.dev/v1`.
- Anthropic-compatible SDKs and Claude Code use `https://api.inferhub.dev`
  because those clients add `/v1` on their own.
- Codex CLI may be the harness while inference is remote through InferHub; its
  workspace and tools run in the CLI host environment.
- Do not record, test, or commit a real API key as part of this documentation
  task. Documentation does not approve paid inference.

## Files in scope

- `tasks/IRE-0004-inferhub-api-setup.md`
- `docs/INFERHUB-API-SETUP.md`
- `checkpoints/CURRENT.md`
- `src/check-public.mjs`

## Acceptance criteria

- The exact OpenAI and Anthropic base URLs are documented with the reason for
  the difference.
- The guide links to InferHub's documentation and describes safe key handling.
- The guide distinguishes remote inference from where Codex CLI tools execute.
- The public-surface scan allows the provider name only in this setup guide,
  its task record, and the current checkpoint; all other restricted terms
  remain checked in those files.
- No API key or paid inference call is included.

## Checkpoint log

- 2026-09-24: Confirmed the canonical checkout was clean on `main` before
  editing. Created GitHub issue #21 and linked it as a sub-issue of #8.
- 2026-09-24: Added the setup guide and verified the documentation against
  InferHub's published API and integration docs. No API requests were made.
- 2026-09-24: The public-surface scan correctly blocked the provider name by
  default. Added a path-specific exception for this setup guide only; the
  first required gate run stopped at that scan before any checkpoint was
  published.
- 2026-09-24: The next scan run also flagged the task/checkpoint records and
  scanner source. Narrowed the exception to the setup guide, this task file,
  and `checkpoints/CURRENT.md`, while keeping the sensitive-term scan active
  for each of them.
- 2026-09-24: All publisher gates passed: static checks, 26 Node tests, public
  surface scan, 41 operational tests, and package dry run. PR #22 passed both
  required CI checks and merged as `9b16c2b7964023adb3259a8a1d61da86bb3d89a1`.
  The finalizer synchronized canonical `main` to the merge commit and removed
  only this checkpoint branch. No model inference or API-key use occurred.

## Next action

Publish this verified closeout record, then close issue #21. Next, create a
child issue and task file for issue #8's planned streaming/liveness and
task-resume guidance.
