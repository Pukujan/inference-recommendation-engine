# IRE-0003 — Static quality checks in CI

## Status

Implementation is complete locally and ready for its asynchronous checkpoint PR. GitHub issue [#15](https://github.com/Pukujan/inference-recommendation-engine/issues/15) remains open beneath project issue #8.

## Goal

Add repeatable local and CI static-quality gates for Python operational tools and JavaScript modules, alongside the existing tests and package checks.

## Decisions

- Ruff checks Python lint rules and formatting; its version is locked through `uv.lock`.
- ESLint's JavaScript recommended rules check `.mjs` files; versions are locked through `pnpm-lock.yaml`.
- Mypy checks annotated operational Python modules and requires function annotations; there are no broad suppression rules.
- No TypeScript or broad JavaScript migration was introduced. Reassess JS type checking separately if the code adopts JSDoc annotations or TypeScript.
- Existing tests, public-surface scan, operational contract tests, and package dry run remain required.
- Ruff and mypy cache directories are ignored by Git and excluded from the public-surface scan.

## Files in scope

- `tasks/IRE-0003-static-quality-checks.md`
- `checkpoints/CURRENT.md`
- `AGENTS.md`
- `.github/workflows/ci.yml`
- `.gitignore` and `src/check-public.mjs`
- `package.json` and `pnpm-lock.yaml`
- `pyproject.toml` and `uv.lock`
- `eslint.config.mjs`
- JavaScript and Python modules requiring lint, formatting, or type corrections

## Acceptance criteria

- `pnpm check:static` exposes JS linting, Python linting/format verification, and the scoped Python type check.
- CI runs all new checks on pushes and pull requests.
- New tools are version-locked and installed reproducibly in CI.
- Existing functional and operational tests, public-surface check, and package dry run remain in CI.
- The checks pass without broad suppressions.

## Checkpoint log

- 2026-09-24: Confirmed the canonical checkout was clean on `main`, synchronized with `origin/main` at `19ce57a390e81f440a3aca87edd4fdcb665b0a1c`.
- 2026-09-24: Created GitHub issue #15 and attached it beneath project issue #8 before implementation.
- 2026-09-24: Planning checkpoint PR [#16](https://github.com/Pukujan/inference-recommendation-engine/pull/16) merged as `7215768c45ae969b5a56099f8f0a85309ded7659` after both required CI checks passed. One initial run exposed the existing intermittent concurrent-appender test failure; rerunning that CI job passed.
- 2026-09-24: Added locked Ruff, mypy, and ESLint dependencies; configured `pnpm check:static`; added it to CI and the required local gates. Ruff formatting was applied to the existing Python files so format verification starts cleanly.
- 2026-09-24: Ruff identified two unused local assignments. Mypy identified object narrowing gaps; explicit object validation fixed those without suppressions. ESLint found an unnecessary regular-expression escape, which was removed.
- 2026-09-24: Added ignore/exclusion rules for Ruff and mypy caches after the public-surface scan correctly detected their generated contents.
- 2026-09-24: Locked dependency installs passed. `pnpm check:static` passed (ESLint, Ruff lint, Ruff format check, mypy: six modules); `pnpm test` passed (25); `pnpm check:public` passed; `pnpm test:operational` passed (41); `pnpm pack --dry-run` passed; `git diff --check` passed.

## Next action

Publish the implementation checkpoint through the asynchronous checkpoint helper, wait for all required CI checks and merge, then finalize the canonical checkout and record closeout evidence before closing issue #15.
