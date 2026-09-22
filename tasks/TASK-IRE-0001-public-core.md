# IRE-0001: Public core

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
