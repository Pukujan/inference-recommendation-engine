---
kind: current
version: 1
project: inference-recommendation-engine
status: in_progress
active_task: IRE-0004
updated_at: 2026-09-24T03:47:17Z
---

## State

IRE-0003 static-quality checks are merged and issue #15 is closed. IRE-0004's InferHub API setup guide merged in PR #22; this closeout record is pending publication before issue #21 is closed.

## Verified

- Canonical GitHub repository: `https://github.com/Pukujan/inference-recommendation-engine`.
- PR #17 merged as `50b9353f41598a5a5afd2e79dc5f9e09f4411461`; PR #19 merged as `1e3c0a0698e892dd587712773afc78447163530b`. Both PRs passed both required CI checks. Issue #15 is closed.
- Canonical `main` and `origin/main` are synchronized at `9b16c2b7964023adb3259a8a1d61da86bb3d89a1`; PR #22 merged at this commit after required CI passed.
- GitHub Issues own plans and status; pull requests and commit history own code changes; private ledger evidence stays in ignored `.ire/issue-ledger/ledger.sqlite3`.
- Main requires strict CI check `test`, applies protection to administrators, disallows force pushes/deletion, and requires zero approvals. GitHub auto-merge and delete-branch-on-merge are enabled.
- The checkpoint publisher runs `pnpm check:static` before tests and publication, with a regression test.
- IRE-0004 PR #22 passed all local gates: static checks, 26 Node tests, public-surface scan, 41 operational tests, and package dry run. Both required GitHub CI checks passed. No model inference or API-key use occurred.
- Static checks pass: ESLint, Ruff lint/format, and mypy on six operational Python modules with untyped definitions disallowed.
- Required local gates pass: Node tests (26), public-surface scan, operational contract tests (41), package dry run, locked installs, and `git diff --check`.

## Current checkpoint

IRE-0004 records the distinct InferHub base URLs for OpenAI-compatible and Anthropic-compatible clients, safe API key handling, and the distinction between remote inference and the Codex CLI host. PR #22 is merged; this checkpoint records closeout before issue #21 is closed. The public-surface scan permits the provider name only in the setup guide, its task record, and this checkpoint. See `tasks/IRE-0004-inferhub-api-setup.md` and `docs/INFERHUB-API-SETUP.md`.

## Next

Publish the verified IRE-0004 closeout record and close issue #21. Then create a child issue and task file for the streaming/liveness and task-resume guidance planned under issue #8.
