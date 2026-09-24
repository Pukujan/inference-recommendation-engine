# Checkpoint delivery

This repository uses one canonical checkout and a short-lived branch per
verified checkpoint. Branches are created in the existing folder; task
worktrees and duplicate clones are not part of the normal workflow.

From the repository root, deliver a checkpoint by naming its branch slug,
commit/PR title, and the exact files or directories it owns:

```powershell
node scripts/checkpoint.mjs `
  --name operational-ledger-relocation `
  --message "feat: relocate operational issue ledger" `
  --issue 11 `
  --path .gitignore `
  --path AGENTS.md `
  --path README.md `
  --path CONTRIBUTING.md `
  --path HANDOFF.md `
  --path .github/workflows/ci.yml `
  --path docs `
  --path operational `
  --path integrations `
  --path schemas `
  --path checkpoints `
  --path package.json `
  --path package-lock.json `
  --path pnpm-lock.yaml `
  --path pnpm-workspace.yaml `
  --path pyproject.toml `
  --path scripts `
  --path src/check-public.mjs `
  --path tests/checkpoint.test.mjs `
  --path uv.lock
```

The helper requires a GitHub task issue, runs `uv sync --locked`, then the pnpm
frozen install and the same local gates as CI. It stages only the named paths,
checks for high-confidence credential patterns, commits and pushes to
`codex/checkpoint/<name>`, opens or updates a PR to `main`, and requests GitHub
auto-merge. It returns without waiting for CI. The PR includes a `Part of
#<issue>` reference. It stops before publishing if a check fails, the PR head
or target is unexpected, or the working tree contains changes outside the
explicit checkpoint paths. Resume a failed checkpoint on its existing branch
with the same `--name` after fixing the cause.

After GitHub confirms the PR merged, run:

```powershell
node scripts/finalize-checkpoint.mjs --pr <number>
```

The finalizer verifies that the PR merged into `main`, its required `test`
check passed, and the local checkpoint branch still points at the PR's exact
head commit. It then fast-forwards the clean canonical checkout and removes
only that verified local branch. GitHub deletes the remote branch after merge.
Do not mark a checkpoint complete or begin dependent implementation before
this confirmation.

The `main` branch must require a pull request and the `test` status check, with
strict up-to-date checks and administrator enforcement. No review approvals are
required so GitHub can auto-merge a green checkpoint without a manual merge
step. Repository auto-merge and delete-branch-on-merge must be enabled. The
helper verifies these settings and never uses an administrator bypass. The
normal GitHub CI workflow validates each push and PR; this is source validation
and merge automation, not a package publish or deployment.

Use explicit paths. Never include `.ire/`, credentials, or private source
records. Do not start the next implementation checkpoint until this one is
merged and the local `main` is synchronized.
