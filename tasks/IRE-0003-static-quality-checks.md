# IRE-0003 — Static quality checks in CI

## Status

Follow-up required — PR [#17](https://github.com/Pukujan/inference-recommendation-engine/pull/17) merged as `50b9353f41598a5a5afd2e79dc5f9e09f4411461`, but review of the completed workflow found the checkpoint publisher did not yet run the new static gate locally. Issue [#15](https://github.com/Pukujan/inference-recommendation-engine/issues/15) was reopened; do not close it until the helper and its regression test are merged.

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
- `scripts/checkpoint.mjs` and `tests/checkpoint.test.mjs` to enforce all required local gates before publication
- `.gitignore` and `src/check-public.mjs`
- `package.json` and `pnpm-lock.yaml`
- `pyproject.toml` and `uv.lock`
- `eslint.config.mjs`
- JavaScript and Python modules requiring lint, formatting, or type corrections

## Acceptance criteria

- `pnpm check:static` exposes JS linting, Python linting/format verification, and the scoped Python type check.
- CI runs all new checks on pushes and pull requests.
- The checkpoint publisher runs `pnpm check:static` before publishing a checkpoint.
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
- 2026-09-24: Implementation PR [#17](https://github.com/Pukujan/inference-recommendation-engine/pull/17) merged at `2026-09-24T02:59:23Z` as `50b9353f41598a5a5afd2e79dc5f9e09f4411461`; both required CI checks passed. The finalizer synchronized canonical `main` and removed only this checkpoint's local branch.
- 2026-09-24: Reopened issue #15 after finding `scripts/checkpoint.mjs` did not run `pnpm check:static`; the prior closeout PR #18 is merged but this required local enforcement and a regression test remain outstanding.
- 2026-09-24: Added `pnpm check:static` to the publisher's local gate sequence and a regression test asserting it runs before the Node tests; the Node suite now passes (26) and static checks plus `git diff --check` pass.

## Next action

Add the static gate to the publisher's local gate list and test that requirement. Run all repository gates, publish the correction through issue #15, finalize the merge, update the closeout evidence, and then close issue #15. The next project-level work remains the streaming/liveness and task-resume guidance under issue #8.
