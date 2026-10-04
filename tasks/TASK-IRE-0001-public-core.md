# IRE-0001: Public core

<!-- continuity:task {"acceptance":["`npm test` passes.","property and metamorphic tests cover the invariant list.","public-surface scan passes.","package can be installed and packed without local-only files.","repository contains no credentials or provider-specific source clients."],"depends_on":[],"goal":"Publish the provider-neutral engine with an executable contract and a fast local demo.","id":"IRE-0001","next_action":"None for this task.","owner":"Alex","priority":"P0","protocol_version":"0.1.0-draft","schema":"project-continuity.task.v1","status":"completed","why":"The ranking core has to be public, testable and free of credentials before anything builds on it."} -->

## Goal

Publish the provider-neutral engine with an executable contract and a fast local demo.

## Acceptance

- [x] `npm test` passes.
- [x] property and metamorphic tests cover the invariant list.
- [x] public-surface scan passes.
- [x] package can be installed and packed without local-only files.
- [x] repository contains no credentials or provider-specific source clients.

## Evidence

Verified on 2026-09-22 with `npm test` (12 passed), `npm run check:public`, `npm run demo`, and `npm pack --dry-run`.

## Next action

Add an adapter contract issue after the public core is published.

## Checkpoint log

- 2026-09-22: public core verified (see Evidence).
