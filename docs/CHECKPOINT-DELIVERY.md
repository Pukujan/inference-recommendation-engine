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
  --issue 8 `
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

The helper runs `uv sync --locked`, then the pnpm frozen install and the same
local gates as CI. It stages only the named paths, checks for high-confidence
credential patterns, commits and pushes to `codex/checkpoint/<name>`, opens a
PR to `main`, waits for the required checks, and includes a `Part of #<issue>`
reference when `--issue` is supplied. After a successful merge it deletes the
temporary remote and local branches and synchronizes this checkout back to
`main`. It stops without merging if a check fails, the PR head changes, or the
working tree contains changes outside the explicit checkpoint paths. Resume a
failed checkpoint on its existing branch with the same `--name` after fixing
the cause.

The `main` branch must require a pull request and the `test` status check, with
strict up-to-date checks and administrator enforcement. No review approvals are
required so the authenticated checkpoint helper can merge a green checkpoint
without a manual handoff. The helper verifies these settings and never uses an
administrator bypass. The normal GitHub CI workflow validates each push and PR;
this is source validation and merge automation, not a package publish or
deployment.

Use explicit paths. Never include `.ire/`, credentials, or private source
records. Do not start the next implementation checkpoint until this one is
merged and the local `main` is synchronized.
