# IRE-0005 — Default Codex harness permissions for authorized runs

GitHub issue: [#24](https://github.com/Pukujan/inference-recommendation-engine/issues/24),
a sub-issue of [#8](https://github.com/Pukujan/inference-recommendation-engine/issues/8).

## Status

The runner and policy merged in PR [#25](https://github.com/Pukujan/inference-recommendation-engine/pull/25)
as `2eab9ee9cd43effff3c47455a08c8ab4f43a3605`; both required CI checks passed.
This follow-up records verified closeout before issue #24 is closed.

## Goal

Make the supported long-running Codex runner writable and non-interactive by
default for user-authorized repository tasks, so agents do not silently choose
read-only mode or ask repeatedly for routine tool permission.

## Decisions

- Use Codex CLI `exec` with `workspace-write`, `approval_policy=never`,
  workspace network enabled, and JSONL progress output.
- Run from the Git repository root so tool writes stay within the authorized
  repository workspace.
- Keep the existing model-provider configuration, API credential source, and
  receipt procedures unchanged.
- Do not silently fall back to read-only if the installed CLI or a managed
  policy cannot honor the requested settings; report the concrete error.
- Do not use unrestricted machine-wide access for a repository-scoped task.
- Do not invoke a model during this configuration checkpoint.

## Files in scope

- `tasks/IRE-0005-codex-harness-permissions.md`
- `checkpoints/CURRENT.md`
- `AGENTS.md`
- `docs/INFERHUB-API-SETUP.md`
- `run_codex_harness.ps1`
- `src/check-public.mjs`

## Acceptance criteria

- The runner explicitly passes workspace-write and no-approval settings; it
  enables network access for authorized remote-provider tool work.
- The runner streams Codex JSONL events and propagates its exit code.
- The public-surface check allows the API name in this issue task record only
  while checking all other restricted terms as usual.
- Repository instructions require this runner for authorized long-running
  model-agent tasks and forbid self-imposed read-only downgrades.
- Credential source and receipt rules are unchanged.
- CLI flags are checked without making a model request.

## Checkpoint log

- 2026-09-24: Confirmed the canonical checkout was clean on `main`. Created
  GitHub issue #24 and linked it as a sub-issue of #8 before implementation.
- 2026-09-24: Official Codex docs and the installed CLI help show `codex exec`
  defaults to read-only; the CLI supports explicit sandbox and approval
  overrides. Verified CLI help only; no model was called.
- 2026-09-24: Added a repository runner with explicit workspace-write,
  noninteractive approvals, network-enabled tool sandboxing, JSONL output,
  and process exit propagation. Documented the required policy in `AGENTS.md`.
  The public scan requires a narrow exception for this task record's API name;
  no broader text checks are disabled.
- 2026-09-24: PowerShell parser accepted the runner, the installed CLI help
  accepted the explicit `exec` options, and `pnpm check:public` passed. These
  checks did not call a model. Full repository gates and checkpoint delivery
  remain.
- 2026-09-24: All publisher gates passed: static checks, 26 Node tests, public
  surface scan, 41 operational tests, and package dry run. PR #25 passed both
  required CI checks and merged as `2eab9ee9cd43effff3c47455a08c8ab4f43a3605`.
  The finalizer synchronized canonical `main` and removed only this checkpoint
  branch. No model was called and no credential or receipt source was changed.

## Next action

Publish this verified closeout record and close issue #24. Then create the next
issue for streaming/liveness and task-resume behavior under issue #8.
