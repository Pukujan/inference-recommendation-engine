---
kind: current
version: 1
project: inference-recommendation-engine
status: in_progress
active_task: IRE-0005
updated_at: 2026-09-24T04:00:55Z
---

## State

IRE-0003 static-quality checks are merged and issue #15 is closed. IRE-0004's InferHub API setup guide and verified closeout merged in PRs #22 and #23; issue #21 is closed. IRE-0005 standardizes permissions for authorized long-running Codex runs.

## Verified

- Canonical GitHub repository: `https://github.com/Pukujan/inference-recommendation-engine`.
- PR #17 merged as `50b9353f41598a5a5afd2e79dc5f9e09f4411461`; PR #19 merged as `1e3c0a0698e892dd587712773afc78447163530b`. Both PRs passed both required CI checks. Issue #15 is closed.
- Canonical `main` and `origin/main` are synchronized at `29c2afb25bd64d5a39dff7a610e58efeeec886f0`; PR #23 merged at this commit after required CI passed.
- GitHub Issues own plans and status; pull requests and commit history own code changes; private ledger evidence stays in ignored `.ire/issue-ledger/ledger.sqlite3`.
- Main requires strict CI check `test`, applies protection to administrators, disallows force pushes/deletion, and requires zero approvals. GitHub auto-merge and delete-branch-on-merge are enabled.
- The checkpoint publisher runs `pnpm check:static` before tests and publication, with a regression test.
- IRE-0004 PR #22 passed all local gates: static checks, 26 Node tests, public-surface scan, 41 operational tests, and package dry run. Both required GitHub CI checks passed. No model inference or API-key use occurred.
- IRE-0005 implementation is ready for local gates: the PowerShell parser accepted the runner, installed CLI help confirmed the explicit sandbox, config, model, JSONL, and working-directory options, and `pnpm check:public` passed. No model was called.
- Static checks pass: ESLint, Ruff lint/format, and mypy on six operational Python modules with untyped definitions disallowed.
- Required local gates pass: Node tests (26), public-surface scan, operational contract tests (41), package dry run, locked installs, and `git diff --check`.

## Current checkpoint

IRE-0005 standardizes the Codex CLI runner for authorized remote-model tasks: writable repository workspace, no routine approval prompts, network-enabled tool execution, and streamed JSONL events. See `tasks/IRE-0005-codex-harness-permissions.md`.

## Next

Complete IRE-0005 implementation and checkpoint delivery. Then create a child issue and task file for streaming/liveness and task-resume guidance under issue #8.
