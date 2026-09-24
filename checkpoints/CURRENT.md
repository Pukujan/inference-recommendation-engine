---
kind: current
version: 1
project: inference-recommendation-engine
status: in_progress
active_task: IRE-0009
updated_at: 2026-09-24T18:16:00Z
---

## State

IRE-0003 static-quality checks are merged and issue #15 is closed. IRE-0004's API setup guide and verified closeout merged in PRs #22 and #23; issue #21 is closed. IRE-0005's runner and policy merged in PR #25; closeout PR #26 is merged and issue #24 is closed. IRE-0006's BYOK CLI runbook merged in PR #28 and issue #27 is closed. IRE-0007 tracks the blind BYOK agent benchmark in issue #29; its documentation and price-preference correction is merged. IRE-0008's copyable Astra owner and Kilo background-staff setup merged in PR #34 and issue #33 is closed. IRE-0009 imports Codex launch receipts into the local ledger under issue #36.

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

IRE-0009 adds Codex receipt capture under [issue #36](https://github.com/Pukujan/inference-recommendation-engine/issues/36). The importer is `operational/scripts/codex_receipt_import.py`. It filters to `cb/gpt-6-astra` and does not read Kilo transcripts. The blinded benchmark remains open under [issue #29](https://github.com/Pukujan/inference-recommendation-engine/issues/29) and is not part of this checkpoint.

## Next

Deliver this explicit-file checkpoint and confirm its required checks and merge before closing issue #36. After that merge, return to the issue #29 benchmark fixture.
