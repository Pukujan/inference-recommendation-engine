# TASK-IRE-0012 — Enforce open-weight eligibility where the lists are written

<!-- continuity:task {"acceptance":["`licences.py` is the single open-weight check shared by the list builders and the public feed, and `feed.py` uses it instead of its own copy","`top20.py` gates every listed family with `open_weight_unverified` when the licence map does not verify it open-weight, so no closed or unlisted family is `recommendation_eligible`","the committed `lists/` CSV and JSON carry no closed-weight family marked eligible, and their manifest hashes match the files","the existing Top 20 tests pass, including a new test that an unlisted family is gated and a verified one is not","no secrets are committed, and the launcher configs, seat chains and price policy are untouched"],"depends_on":["IRE-0011"],"goal":"Make the open-weight rule one shared check, apply it where the Top 20 list is written, and regenerate the committed lists so closed and unlisted families stop being marked eligible.","id":"IRE-0012","issue_url":"https://github.com/Pukujan/inference-recommendation-engine/issues/94","next_action":"None. Merged in PR #98; the launcher picker tables were refreshed in the launcher's PR #85, and issue #94 is closed.","owner":"Alex; executor agent","priority":"P0","protocol_version":"0.1.0-draft","schema":"project-continuity.task.v1","status":"completed","why":"The feed already dropped closed-weight families, but the CSV and JSON the launcher reads did not. A closed model such as GPT 5.6 Luna stayed `recommendation_eligible = true` and reached the launcher picker."} -->

- Status: completed
- Owner: Alex; executor agent
- Priority: P0
- Depends on: IRE-0011

## Goal

Make the open-weight rule one shared check, apply it where the Top 20 list is written, and regenerate the committed lists so closed and unlisted families stop being marked eligible.

## Why

`feed.py` filtered closed-weight families with its own copy of the rule, so the public feed was clean. `top20.py` and `frontier.py` wrote the CSVs and JSON without that filter, so a closed family such as GPT 5.6 Luna stayed `recommendation_eligible = true` in the list the launcher picker reads (#94, symptom 3 and 4). One shared check removes the chance of the copies drifting apart.

## Allowed files

- The IRE pipeline's `licences.py` (new), `top20.py` and `feed.py`
- The IRE pipeline's `lists/` folder
- The Top 20 test module under `operational/tests/`
- `tasks/`, `checkpoints/CURRENT.md`

## Human outcome

The Top 20 a reader or a picker sees marks a family eligible only when its licence record verifies it open-weight. A closed or unlisted family can still be listed and ranked, but it is never recommended.

## Scope and boundaries

- In scope: the shared licence check, the Top 20 eligibility gate, and a regeneration of the committed Top 20 from the live catalogue.
- Out of scope: the frontier list (its eligibility stays capability-based so seat routing is unchanged), the launcher configs and fallback chains, the shortlist weights, the freshness guard, and the utility tier. Those are separate checkpoints under #94.

## Acceptance criteria

- [x] `licences.py` is the single open-weight check shared by the list builders and the public feed, and `feed.py` uses it instead of its own copy
- [x] `top20.py` gates every listed family with `open_weight_unverified` when the licence map does not verify it open-weight, so no closed or unlisted family is `recommendation_eligible`
- [x] the committed `lists/` CSV and JSON carry no closed-weight family marked eligible, and their manifest hashes match the files
- [x] the existing Top 20 tests pass, including a new test that an unlisted family is gated and a verified one is not
- [x] no secrets are committed, and the launcher configs, seat chains and price policy are untouched

## Related records

- Leaf issue: https://github.com/Pukujan/inference-recommendation-engine/issues/94.
- Branch: `codex/checkpoint/open-weight-eligibility`.

## Checkpoint log

- Checkpoint 1 (this one): adds `licences.py`, points `feed.py` at it, adds the `open_weight_unverified` gate to `top20.py`, adds the gate tests, and regenerates the committed Top 20 so no closed family is eligible. Merged in PR #98 as `f472065` (2026-10-06).
- Closeout: verified against the live feed on 2026-10-06 — `tiers.cheap.entries[0]` is DeepSeek V4.1 Flash on `cb/deepseek-v4.1-flash`, health `healthy`, and no committed list row is closed-weight. The launcher picker tables were refreshed in `claude-code-launcher` PR #85. Issue #94 is closed.

## Handoff

Read PROJECT → CURRENT → this task → minimum relevant spec. Checkpoint before stopping.
