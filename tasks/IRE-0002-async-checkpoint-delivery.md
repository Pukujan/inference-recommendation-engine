# IRE-0002 — Asynchronous CI-gated checkpoint delivery

## Status

Ready for closeout checkpoint — implementation is merged; tracked by GitHub issue [#11](https://github.com/Pukujan/inference-recommendation-engine/issues/11), a sub-issue of project issue #8.

## Goal

Let agents publish a verified checkpoint and return while GitHub runs required checks and auto-merges only when protected-branch requirements pass. Keep the canonical checkout, explicit-path staging, local gates, and credential safeguards.

## Decisions

- GitHub issue #8 remains the project-level parent; issue #11 tracks this independently verifiable workflow change.
- The publisher will not wait for CI. A separate finalizer confirms the PR merge before syncing canonical `main` and deleting only this checkpoint's local branch.
- No automatic worktree creation or parallel implementation is added. An unmerged checkpoint must be confirmed before dependent work continues.
- Enable repository auto-merge while preserving strict required `test`, administrator enforcement, disabled force-push/deletion, and zero required approvals.
- This workflow change does not run paid inference, alter ranking behavior, or authorize provider spending.

## Files in scope

- `tasks/IRE-0002-async-checkpoint-delivery.md`
- `checkpoints/CURRENT.md`
- `AGENTS.md`
- `docs/CHECKPOINT-DELIVERY.md`
- `scripts/checkpoint.mjs`
- `scripts/finalize-checkpoint.mjs`
- `tests/checkpoint.test.mjs`
- `tests/finalize-checkpoint.test.mjs`

Update this list before editing any additional file.

## Acceptance criteria

- The publisher runs existing local gates, explicit-path and sensitive-content checks, commits and pushes only its checkpoint branch, creates or updates a PR, and requests GitHub auto-merge without waiting for CI.
- The PR references issue #11 and describes the checkpoint. Publication stops on failed gates, missing authentication/protection, unexpected branch/remote, staged or unselected changes, and sensitive paths/content.
- The finalizer confirms the exact PR is merged into `main`, fast-forwards the clean canonical checkout, and removes only its own merged local branch. It never bypasses checks or force-removes state.
- GitHub auto-merge is enabled; existing branch-protection settings and required CI remain intact.
- Tests cover publication and finalization gates without real pushes or merges.

## Checkpoint log

- 2026-09-23: Confirmed the canonical checkout is clean on `main` at `2d89346`, synchronized with `origin/main`; issue #8 is open and is the appropriate parent.
- 2026-09-23: Created issue #11 and attached it beneath issue #8 before implementation.
- 2026-09-23: The current helper waits on `gh pr checks --watch`, then merges synchronously. Repository auto-merge is disabled; `main` requires strict `test`, enforces protection for administrators, disallows force pushes/deletion, and requires zero approvals.
- 2026-09-23: Implemented asynchronous publishing and merge finalization. Enabled repository auto-merge and delete-branch-on-merge; verified both settings and confirmed the original strict `test` protection and zero-approval rule remain unchanged.
- 2026-09-23: `uv sync --locked` passed; `pnpm test` passed (25); `pnpm check:public` passed; `pnpm test:operational` passed (41); `pnpm pack --dry-run` passed; focused checkpoint tests passed (9); `git diff --check` passed.
- 2026-09-24: PR [#12](https://github.com/Pukujan/inference-recommendation-engine/pull/12) merged at `2026-09-24T02:19:43Z` as `0a7fdc2d10c09ca6d9a9dfea30f4132c25b77b37`; both required `test` checks passed. The publisher returned after requesting auto-merge; the finalizer verified PR head `8683ddaad9517bda1d43ee2ae42546bd7c76a3ee`, synchronized canonical `main`, and removed its exact local checkpoint branch.

## Next action

Deliver this merge-evidence closeout record through a final checkpoint under issue #11, confirm that PR merges, run the finalizer, then close issue #11. Only then begin IRE-0003 streaming/liveness and resume guidance.
