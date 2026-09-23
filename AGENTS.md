# Human-facing work

Before changing the public story, read the pinned adapter in `.content-system/` and use the exact helper commit recorded there.

For README, product, visual, or marketing work, load the helper's README playbook, image guide, and README contract. Keep claims tied to repository evidence, label experiments and plans, and never add credentials or private source records.

## Canonical checkout and checkpoint delivery

- Work in the user's existing canonical checkout. Do not create a worktree or a
  second clone for a task unless the user explicitly asks for isolation. A
  short-lived checkpoint branch is fine, but create and use it in this same
  folder.
- If a task explicitly authorizes an isolated worktree, record its exact path
  and branch in the checkpoint; after its changes merge and the worktree is
  verified clean, remove only that task-created worktree. Never prune or
  delete unrelated worktrees.
- Inspect the working tree before editing. Preserve user changes and never
  include unrelated work in a checkpoint.
- A checkpoint is one independently verified increment. Before continuing to
  the next increment, update the relevant task/checkpoint record, run the
  repository gates, and deliver it with `node scripts/checkpoint.mjs` using an
  explicit `--path` for each intended file or directory. Never use `git add
  -A` for checkpoint delivery.
- The checkpoint helper commits on `codex/checkpoint/<name>` in this checkout,
  pushes it to `origin`, opens a PR to `main`, waits for the required CI checks,
  merges only after they pass, and fast-forwards this checkout's `main`. Do not
  continue implementation while that checkpoint is unmerged or checks are
  failing. Fix a failed checkpoint on its existing branch and rerun the helper.
- Do not bypass required checks or push directly to `main`. The helper refuses
  pre-staged or leftover unstaged/untracked work, sensitive paths, and
  credential-like content. Never stage `.ire/`, credentials, or private source
  records.
- Use pnpm for Node.js and uv for Python. Keep `pnpm-lock.yaml` and `uv.lock`
  current and use frozen/locked installs in CI.
- Required local gates are `pnpm test`, `pnpm check:public`,
  `pnpm test:operational`, and `pnpm pack --dry-run`; CI runs the same gates.
- GitHub Issues, pull requests, and commit history own project plans, issue
  status, and code changes. Private operational records and evidence live only
  in the ignored `.ire/issue-ledger/ledger.sqlite3`; never commit or upload the
  database, its sidecar files, private source records, or credentials.
