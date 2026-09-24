# IRE-0003 — Static quality checks in CI

## Status

In progress — GitHub issue [#15](https://github.com/Pukujan/inference-recommendation-engine/issues/15) is attached beneath project issue #8. Planning checkpoint is being recorded before implementation.

## Goal

Add repeatable local and CI static-quality gates for Python operational code and JavaScript modules, alongside the existing tests and package checks.

## Decisions

- Use Ruff for Python linting and format verification; pin it through `uv.lock`.
- Use ESLint's JavaScript recommended rules for `.mjs` source, tests, and repository scripts; pin it through `pnpm-lock.yaml`.
- Add mypy as a targeted type check for the operational Python modules, then fix real findings or document narrow, justified exclusions.
- Do not introduce TypeScript or perform a broad JavaScript migration. Reassess JS type checking separately after annotations or TypeScript adoption.
- Keep existing tests and public-surface/package gates unchanged.

## Files in scope

- `tasks/IRE-0003-static-quality-checks.md`
- `checkpoints/CURRENT.md`
- `AGENTS.md`
- `.github/workflows/ci.yml`
- `package.json` and `pnpm-lock.yaml`
- `pyproject.toml` and `uv.lock`
- ESLint configuration
- Any source files requiring narrow lint/type corrections

Update this list before editing additional files.

## Acceptance criteria

- Local package scripts expose JS linting, Python linting/format verification, and the scoped Python type check.
- CI runs all three new checks on pushes and pull requests.
- Python and JavaScript tools are lockfile-pinned and installed reproducibly in CI.
- Existing functional and operational tests, public-surface check, and package dry run remain in CI.
- The new checks pass without broad suppression rules.

## Checkpoint log

- 2026-09-24: Confirmed the canonical checkout was clean on `main`, synchronized with `origin/main` at `19ce57a390e81f440a3aca87edd4fdcb665b0a1c`.
- 2026-09-24: Created GitHub issue #15 and attached it beneath project issue #8 before implementation.

## Next action

Implement and run the new static checks locally, fix actionable findings, run all required repository gates, then publish this task's implementation checkpoint through the async checkpoint helper.
