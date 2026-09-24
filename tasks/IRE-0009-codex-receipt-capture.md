# IRE-0009 — Codex receipt capture for the operational ledger

GitHub issue: [#36](https://github.com/Pukujan/inference-recommendation-engine/issues/36),
a sub-issue of [#8](https://github.com/Pukujan/inference-recommendation-engine/issues/8).

## Status

Implementation. No model request and no credential access are part of this checkpoint.

## Goal

Import bounded Codex launch receipts into the local operational ledger without scraping Kilo transcripts.

## Decisions

- Capture at the Codex receipt written for a launch. Do not read Kilo chat transcripts.
- Default route filter is `cb/gpt-6-astra`. A missing or different model is skipped, not inferred.
- One thread imports once. The receipt has no trusted clock, so the stamp and idempotency key are stable.
- Stored reports keep an error code and outcome. They do not keep commands, tool output, assistant text, or prompt text.
- A directory watcher can scan once or until stopped. It does not modify receipts or install a Kilo config.
- A conflicting later outcome for the same thread fails closed.

## Files in scope

- `docs/ISSUE-LEDGER-SPEC.md`
- `docs/ISSUE-LEDGER-INVARIANTS.md`
- `docs/ISSUE-LEDGER-HOST-HOOKS.md`
- `docs/ISSUE-LEDGER-OPERATIONS.md`
- `operational/scripts/codex_receipt_import.py`
- `operational/tests/test_codex_receipt_import.py`
- `package.json`
- `tasks/IRE-0009-codex-receipt-capture.md`
- `checkpoints/CURRENT.md`

## Acceptance criteria

- Spec states the receipt boundary and the forbidden fields.
- Property tests cover duplicate import, UTF-16 independence, wording independence, route filtering, and an unchanged open receipt.
- Those tests failed before the importer existed and pass after it does.
- `pnpm test:operational` discovers `test_*.py`.
- No live Kilo config is edited. The private ledger database is not committed.

## Checkpoint log

- 2026-09-24: Issue #36 rewritten from an accidental note into this capture task and linked under #8.
- 2026-09-24: Red tests failed with `ModuleNotFoundError: codex_receipt_import`.
- 2026-09-24: Importer and five property tests passed locally. No Codex process was launched.

## Next action

Deliver this explicit-file checkpoint and confirm required checks and merge before closing issue #36.
