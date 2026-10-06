---
kind: current
version: 1
project: inference-recommendation-engine
status: complete
active_task: null
updated_at: 2026-10-06T05:37:54Z
---

<!-- continuity:current {"active_task":null,"active_task_file":null,"protocol_version":"0.1.0-draft","schema":"project-continuity.current.v1"} -->

## State

No task is active. The three-repo fix in issue #94 and the utility tier in issue #99 are complete and both issues are closed. The text picks now name the route the engine actually picks, the price shown is the chosen route's own ask with its basis labelled, closed and unverified families no longer reach a picker as eligible, and the feed carries an optional utility tier for image and multimodal families.

IRE-0012 (issue #94) made the open-weight rule one shared check (`licences.py`), gated every listed family with `open_weight_unverified` in `top20.py`, and regenerated the committed Top 20 so no closed or unlisted family is `recommendation_eligible`. Its checkpoint merged in PR #98 (`f472065`). The rest of #94 landed alongside it: route selection prefers the route with more sellers (#96) and the cheapest well-supplied ask (#97), every price surface names its basis (#97), the shortlist weights lean toward reliability and catalog availability (#97), the launcher warns on a stale list, and the utility tier landed in PR #100 (`2bac3a7`). The launcher picker tables were refreshed in `claude-code-launcher` PR #85 without moving any seat or fallback chain.

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
- The 2026-10-06 live feed (`generated_at 2026-10-06T05:35:06Z`) names DeepSeek V4.1 Flash at rank 1 on `cb/deepseek-v4.1-flash` with `health.status = healthy` and output price `0.0006` per million, basis "best route lowest listed ask at as_of". Its tiers are `cheap`, `strongest_open` and `utility`. The `utility` tier carries one entry, Qwen3.8 Omni Flash, with `open_weight = null` and `recommended = false`.
- No committed list row is closed-weight: the Top 20 CSV's five eligible families are all licence-verified, and every other row carries `recommendation_eligible = false` with a gate reason.
- `claude-code-launcher` PR #85 (`288fa4e`) refreshed the four picker tables to IRE's current list without moving a seat or fallback chain; `shared/litellm/config/inferhub_fallbacks.yaml` was not touched.

## Current checkpoint

Closeout of issue #94 and issue #99: `tasks/TASK-IRE-0012-open-weight-eligibility.md` is marked completed with its acceptance boxes ticked and a closeout note, and this record moves to no active task. Both issues are closed on GitHub.

## Next

None. The IRE-0011 closeout (issue #71) remains the only open thread; it needs the operator to direct the ACS train inconsistency filing to `agent-stack-train`.
