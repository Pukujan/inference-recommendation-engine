# Contributing

Keep changes small and contract-led.

Before opening a change:

```bash
pnpm install --frozen-lockfile
uv sync --locked
pnpm test
pnpm check:public
pnpm test:operational
pnpm pack --dry-run
```

Any scoring change must update the invariant or relation it affects, add a generated or metamorphic test, and change the policy or engine revision when behavior changes.

GitHub Issues, pull requests, and commit history own project plans, issue
status, and code changes. Do not add credentials, private operational records,
or source captures to this repository. Private operational events belong only
in `.ire/issue-ledger/ledger.sqlite3`, which is ignored by Git.
