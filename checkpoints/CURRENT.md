---
kind: current
version: 1
project: inference-recommendation-engine
status: in_progress
active_task: IRE-0010
updated_at: 2026-09-24T22:30:00Z
---

## State

IRE-0003 static-quality checks are merged and issue #15 is closed. IRE-0004's API setup guide and verified closeout merged in PRs #22 and #23; issue #21 is closed. IRE-0005's runner and policy merged in PR #25; closeout PR #26 is merged and issue #24 is closed. IRE-0006's BYOK CLI runbook merged in PR #28 and issue #27 is closed. IRE-0007 tracks the blind BYOK agent benchmark in issue #29; its documentation and price-preference correction is merged. IRE-0008's copyable Astra owner and Kilo background-staff setup merged in PR #34 and issue #33 is closed. IRE-0009's Codex receipt importer merged in PR #37. Issue #36 is the closeout record.

## Verified

- Canonical GitHub repository: `https://github.com/Pukujan/inference-recommendation-engine`.
- PR #17 merged as `50b9353f41598a5a5afd2e79dc5f9e09f4411461`; PR #19 merged as `1e3c0a0698e892dd587712773afc78447163530b`. Both PRs passed both required CI checks. Issue #15 is closed.
- Canonical `main` and `origin/main` are synchronized at `2eab9ee9cd43effff3c47455a08c8ab4f43a3605`; PR #25 merged at this commit after required CI passed.
- GitHub Issues own plans and status; pull requests and commit history own code changes; private ledger evidence stays in ignored `.ire/issue-ledger/ledger.sqlite3`.
- Main requires strict CI check `test`, applies protection to administrators, disallows force pushes/deletion, and requires zero approvals. GitHub auto-merge and delete-branch-on-merge are enabled.
- The checkpoint publisher runs `pnpm check:static` before tests and publication, with a regression test.
- IRE-0004 PR #22 passed all local gates: static checks, 26 Node tests, public-surface scan, 41 operational tests, and package dry run. Both required GitHub CI checks passed. No model inference or API-key use occurred.
- IRE-0005 PR #25 passed all local gates: static checks, 26 Node tests, public-surface scan, 41 operational tests, and package dry run. Both required GitHub CI checks passed. The PowerShell parser and CLI flag checks passed; no model was called.
- Static checks pass: ESLint, Ruff lint/format, and mypy on six operational Python modules with untyped definitions disallowed.
- Required local gates pass: Node tests (26), public-surface scan, operational contract tests (41), package dry run, locked installs, and `git diff --check`.

## Current checkpoint

IRE-0010 checkpoint 1 merged in PR #42 at `2c45e74be95be9241fb2489abd195462af5a0450` and is deployed on the collector host from `main`. Checkpoint 2 puts provider errors (HTTP status, provider code such as 11133, request id) from Astra receipts on the launcher root span, for issue #41 in the #40 M0.6 context. IRE-0009 remains merged in PR #37; the blinded benchmark remains open under issue #29.

## Next

Merge IRE-0010 checkpoint 2, then checkpoint 3 (P4 incident detectors and a versioned signature catalog). Issue #29 benchmark work continues afterwards.
