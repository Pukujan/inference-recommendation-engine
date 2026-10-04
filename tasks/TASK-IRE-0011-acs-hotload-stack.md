# TASK-IRE-0011 — Adopt the current ACS hot-load stack

<!-- continuity:task {"acceptance":["the CGM adapter and AGENTS.md pin block name CGM 0.5.12 at 6831f91e and list all eight modules, and the pinned validate_content_system.py prints VALID","`.continuity/` matches the PCM 0.6.0 config schema, the nine pinned schemas are present, and `continuity validate` prints VALID","the OIO 0.1.0 installer output is present and `oio_installer.py --check` prints VALID","`.coord/assignment.json`, `.coord/boss_claim.json` and `stack-manifest.json` are pinned to release train 2026-10-01, and the train's check_manifest.py accepts the manifest","the pinned ACS hotload_check.py passes against this repository","CI `test` runs all of the above validators and passes on the pull request","no secrets are stored, and the protected list paths and price policy are untouched"],"depends_on":[],"goal":"Install the ACS multi-agent-hotload 0.1.0 stack (PCM 0.6.0, CGM 0.5.12, OIO 0.1.0) pinned to release train 2026-10-01 and prove it with the pinned validators in the required `test` check.","id":"IRE-0011","issue_url":"https://github.com/Pukujan/inference-recommendation-engine/issues/71","next_action":"Squash-merge the PR once CI `test` passes, then close #71.","owner":"Alex; executor agent installs","priority":"P0","protocol_version":"0.1.0-draft","schema":"project-continuity.task.v1","status":"active","why":"IRE is being opened to people outside Alex's own setup (#70). The agent stack has to be current before the onboarding work lands on top of it."} -->

- Status: active
- Owner: Alex; executor agent installs
- Priority: P0
- Depends on: none

## Goal

Install the ACS multi-agent-hotload 0.1.0 stack (PCM 0.6.0, CGM 0.5.12, OIO 0.1.0) pinned to release train 2026-10-01 and prove it with the pinned validators in the required `test` check.

## Why

IRE is being opened to people outside Alex's own setup (#70). The agent stack has to be current before the onboarding work lands on top of it.

## Allowed files

- `.continuity/`, `schemas/v1/`, `PROJECT.md`, `AGENTS.md`, `HANDOFF.md`, `checkpoints/CURRENT.md`, `tasks/`
- `.content-system/`, `.oio/`, `.coord/`, `stack-manifest.json`
- `.github/ISSUE_TEMPLATE/`, `.github/scripts/`, `.github/workflows/`

## Human outcome

A fresh agent opening IRE finds the same pinned stack as Alex's other repos, and a change that breaks any of the stack validators can't reach `main`, because `test` has to pass first.

## Scope and boundaries

- In scope: the stack install, following the claude-code-launcher adopter (TASK-CCL-0001) and the recipe-box reproduction notes.
- Out of scope: README and product copy, the CGM html-demo refresh, the recommendation list files, and the price policy doc. The protected paths are named in #70 and are unchanged here.
- IRE already had a README and an older continuity config, so PCM was initialised in a scratch folder and its config shape and schemas were laid over the existing files. `workspace.mode` is `single-checkout`, to match the canonical-checkout rule in AGENTS.md.

## Acceptance criteria

- [ ] the CGM adapter and AGENTS.md pin block name CGM 0.5.12 at 6831f91e and list all eight modules, and the pinned validate_content_system.py prints VALID
- [ ] `.continuity/` matches the PCM 0.6.0 config schema, the nine pinned schemas are present, and `continuity validate` prints VALID
- [ ] the OIO 0.1.0 installer output is present and `oio_installer.py --check` prints VALID
- [ ] `.coord/assignment.json`, `.coord/boss_claim.json` and `stack-manifest.json` are pinned to release train 2026-10-01, and the train's check_manifest.py accepts the manifest
- [ ] the pinned ACS hotload_check.py passes against this repository
- [ ] CI `test` runs all of the above validators and passes on the pull request
- [ ] no secrets are stored, and the protected list paths and price policy are untouched

## Related records

- Leaf issue: https://github.com/Pukujan/inference-recommendation-engine/issues/71. Parent: https://github.com/Pukujan/inference-recommendation-engine/issues/70.
- Branch: `task/IRE-71-acs-hotload-stack`.

## Checkpoint log

No checkpoints yet.

## Handoff

Read PROJECT → CURRENT → this task → minimum relevant spec. Checkpoint before stopping.
