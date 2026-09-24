# IRE-0003 — Static quality checks in CI

## Status

Implementation and the local publisher follow-up are merged. PR [#17](https://github.com/Pukujan/inference-recommendation-engine/pull/17) merged as `50b9353f41598a5a5afd2e79dc5f9e09f4411461`; the publisher regression fix in PR [#19](https://github.com/Pukujan/inference-recommendation-engine/pull/19) merged as `1e3c0a0698e892dd587712773afc78447163530b`. Both PRs passed both required CI checks. Issue [#15](https://github.com/Pukujan/inference-recommendation-engine/issues/15) remains open until this final closeout record is merged.

## Goal

Add repeatable local and CI static-quality gates for Python operational tools and JavaScript modules, alongside the existing tests and package checks.

## Decisions

- Ruff checks Python lint rules and formatting; its version is locked through `uv.lock`.
- ESLint's JavaScript recommended rules check `.mjs` files; versions are locked through `pnpm-lock.yaml`.
- Mypy checks annotated operational Python modules and requires function annotations; there are no broad suppression rules.
- No TypeScript or broad JavaScript migration was introduced. Reassess JS type checking separately if the code adopts JSDoc annotations or TypeScript.
- Existing tests, public-surface scan, operational contract tests, and package dry run remain required.
- Ruff and mypy cache directories are ignored by Git and excluded from the public-surface scan.
- The checkpoint publisher runs `pnpm check:static` before other tests and before any push.

## Files in scope

- `tasks/IRE-0003-static-quality-checks.md`
- `checkpoints/CURRENT.md`
- `AGENTS.md`
- `.github/workflows/ci.yml`
- `.gitignore` and `src/check-public.mjs`
- `package.json` and `pnpm-lock.yaml`
- `pyproject.toml` and `uv.lock`
- `eslint.config.mjs`
- `scripts/checkpoint.mjs` and `tests/checkpoint.test.mjs`
- JavaScript and Python modules requiring lint, formatting, or type corrections

## Acceptance criteria

- `pnpm check:static` exposes JS linting, Python linting/format verification, and the scoped Python type check.
- CI runs all new checks on pushes and pull requests.
- The checkpoint publisher runs `pnpm check:static` before publishing.
- New tools are version-locked and installed reproducibly in CI.
- Existing functional and operational tests, public-surface check, and package dry run remain in CI and the publisher's gates.
- The checks pass without broad suppressions.

## Checkpoint log

- 2026-09-24: Confirmed the canonical checkout was clean on `main`, synchronized with `origin/main` at `19ce57a390e81f440a3aca87edd4fdcb665b0a1c`.
- 2026-09-24: Created GitHub issue #15 and attached it beneath project issue #8 before implementation.
- 2026-09-24: Planning checkpoint PR [#16](https://github.com/Pukujan/inference-recommendation-engine/pull/16) merged as `7215768c45ae969b5a56099f8f0a85309ded7659` after both required CI checks passed. One initial run exposed the existing intermittent concurrent-appender test failure; rerunning that CI job passed.
- 2026-09-24: Added locked Ruff, mypy, and ESLint dependencies; configured `pnpm check:static`; added it to CI and the required local gates. Ruff formatting was applied to the existing Python files so format verification starts cleanly.
- 2026-09-24: Ruff identified two unused local assignments. Mypy identified object narrowing gaps; explicit object validation fixed those without suppressions. ESLint found an unnecessary regular-expression escape, which was removed.
- 2026-09-24: Added ignore/exclusion rules for Ruff and mypy caches after the public-surface scan correctly detected their generated contents.
- 2026-09-24: PR #17 merged at `2026-09-24T02:59:23Z` as `50b9353f41598a5a5afd2e79dc5f9e09f4411461`; both required CI checks passed. The finalizer synchronized canonical `main` and removed only this checkpoint's local branch.
- 2026-09-24: Reopened issue #15 after finding the publisher omitted the new static gate. Added `pnpm check:static` to `scripts/checkpoint.mjs` and a regression test asserting it runs before test gates.
- 2026-09-24: Correction PR [#19](https://github.com/Pukujan/inference-recommendation-engine/pull/19) merged at `2026-09-24T03:04:48Z` as `1e3c0a0698e892dd587712773afc78447163530b`; both required CI checks passed. The finalizer synchronized canonical `main` and removed only its verified local checkpoint branch.
- 2026-09-24: All required local gates pass: `pnpm check:static` (ESLint, Ruff lint and format check, mypy on six modules); `pnpm test` (26); `pnpm check:public`; `pnpm test:operational` (41); `pnpm pack --dry-run`; locked installs; and `git diff --check`.

## Next action

Publish this final closeout record, then close issue #15 with both merge SHAs and CI evidence. The next project-level work remains streaming/liveness and task-resume guidance under issue #8.
