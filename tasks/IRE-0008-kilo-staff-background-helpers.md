# IRE-0008 — Copyable Astra owner and Kilo background-staff setup

GitHub issue: [#33](https://github.com/Pukujan/inference-recommendation-engine/issues/33),
a sub-issue of [#8](https://github.com/Pukujan/inference-recommendation-engine/issues/8).
Related open policy: [#32](https://github.com/Pukujan/inference-recommendation-engine/issues/32).
This task does not implement #32 and does not close #8 or #29.

## Status

Documentation-only. No model request and no credential access are part of this checkpoint.

## Goal

Preserve a copyable operating setup so a fresh agent can reproduce the Astra owner / Grok staff split and the Kilo background in-session spawn rules without reconstructing chat.

## Decisions

- Keep `docs/INFERHUB-API-SETUP.md` as the canonical guide linked from `AGENTS.md`.
- Cite the merged source record at [project-continuity-modules `docs/ASTRA_GROK_STAFF.md`](https://github.com/Pukujan/project-continuity-modules/blob/main/docs/ASTRA_GROK_STAFF.md), child issue [#69](https://github.com/Pukujan/project-continuity-modules/issues/69). Label it as a recorded operating model, not a measurement rerun in this repository.
- Astra on route `cb/gpt-6-astra` plans, researches, owns, and verifies. Kilo in-session subagents execute. Staff do not choose the next slice.
- Helpers the parent does not need immediately use `background: true`. A foreground `task` is only for a step that depends on the child.
- Agent Manager sessions are not staff.
- The parent records the result and closes the worker immediately.
- Do not copy key values, tokens, or another project's local launcher scripts.
- This repository's Codex runner stays workspace-write through `run_codex_harness.ps1`. A full-access relaunch recorded elsewhere is not authorization here.
- The nested CLI pattern remains a separate topology for another coding CLI's native subagents. Do not relabel a Kilo `task` child as that pattern.

## Files in scope

- `AGENTS.md`
- `docs/INFERHUB-API-SETUP.md`
- `tasks/IRE-0008-kilo-staff-background-helpers.md`
- `checkpoints/CURRENT.md`
- `src/check-public.mjs`
- `eslint.config.mjs`
- `package.json`

## Acceptance criteria

- `AGENTS.md` points future agents at the Kilo staff section before they spawn staff workers.
- The runbook states the command split, the `background: true` rule, the close-after-capture rule, and the Agent Manager exclusion.
- The runbook distinguishes Kilo in-session staff from the nested CLI / native-subagent topology.
- Issue #32 remains open. This guide does not authorize a subscription fallback or a full-access sandbox.
- No model request is made; repository gates and required GitHub CI pass.

## Checkpoint log

- 2026-09-24: Created issue #33 and linked it as a sub-issue of #8 before editing.
- 2026-09-24: Added the copyable Kilo staff section to the canonical runbook and the mandatory pointer in `AGENTS.md`.
- 2026-09-24: Ignored private `.ire/**` in ESLint and the public-surface walk, skipped local `.kilo` worktrees in that walk, and scoped `pnpm test` to `tests/*.test.mjs`, so untracked local artifacts cannot fail the publisher. Those files stay untracked. The scoped run is the same 26 tracked tests.
- 2026-09-24: No CLI was launched and no inference or credential access occurred during this documentation checkpoint.

## Next action

Run the required local gates, deliver this explicit-file checkpoint via the checkpoint helper, and confirm the GitHub PR's required checks and merge before closing issue #33. After that merge, return to the blinded benchmark on issue #29.
