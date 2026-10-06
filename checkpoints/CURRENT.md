---
kind: current
version: 1
project: inference-recommendation-engine
status: in_progress
active_task: IRE-0012
updated_at: 2026-10-06T02:20:02Z
---

<!-- continuity:current {"active_task":"IRE-0012","active_task_file":"tasks/TASK-IRE-0012-open-weight-eligibility.md","protocol_version":"0.1.0-draft","schema":"project-continuity.current.v1"} -->

## State

IRE-0012 (issue #94) is active: the open-weight rule becomes one shared check (`licences.py`), `top20.py` gates every listed family with `open_weight_unverified` when the licence map does not verify it, and the committed Top 20 is regenerated so no closed or unlisted family is `recommendation_eligible`.

IRE-0011 (issue #71, parent #70) moved IRE onto the current ACS hot-load stack (PCM 0.7.0, CGM 0.5.12, OIO 0.1.0, ACS multi-agent-hotload 0.2.0) through release train `current`; its pin checkpoint merged in PR #95 (`075e252`) and the validators pass in CI. Closing #71 is the remaining closeout.

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
- IRE-0011 stack pins follow release train `current` (PCM 0.7.0 `851bcf7`, CGM 0.5.12 `62340f3`, OIO 0.1.0 `a4bba77`, ACS 0.2.0 `25be219`). The pinned validators pass against this checkout: `continuity validate` VALID, CGM `validate_content_system` and `verify_adopter_content` VALID, `check_manifest.py` OK (agrees with train `current`, four components), and ACS `hotload_check` OK. ACS is pinned one commit past the train's own ACS entry (`589b0a9`) because that entry's `stack-mesh.json` still requires the previous PCM/CGM and fails its own check.
- IRE-0011's pin checkpoint merged in PR #95 as `075e252`, and the stack validators run in the required `test` check.

## Current checkpoint

IRE-0012 checkpoint 1 (issue #94) adds `ihub/licences.py` as the single open-weight check, points `feed.py` at it, adds the `open_weight_unverified` gate to `top20.py`, adds the gate tests, and regenerates the committed Top 20. Before the change, the 2026-10-05 committed list marked GPT 5.6 Luna `recommendation_eligible = true`; after it, the family is gated.

## Next

Merge the IRE-0012 checkpoint PR once CI `test` passes, then refresh the launcher picker tables (`top20-builtin.csv`, `defaults.json`) from the corrected lists. Later checkpoints under #94: shortlist rebalancing, a launcher freshness guard, and the utility tier.
