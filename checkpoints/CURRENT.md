---
kind: current
version: 1
project: inference-recommendation-engine
status: ready_for_checkpoint
active_task: IRE-0001-operational-ledger-relocation
updated_at: 2026-09-23T23:43:57Z
---

## State

The v0.1 ranking core remains published and unchanged. The operational-ledger
relocation, local SQLite storage, pnpm/uv toolchain, and CI-gated checkpoint
workflow are integrated in the canonical checkout and pass their local gates.
The private ledger projection remains separate from ranking behavior. GitHub
issue #8 is the public plan and project-issue record for this work.

## Verified

- GitHub repository: `https://github.com/Pukujan/inference-recommendation-engine`;
- canonical `main` is at `2e6438802f4ae55a13d81bb2965bdbcfecaf0001`, synchronized
  with `origin/main`; no relocation checkpoint commit, branch, or PR exists yet;
- main protection requires strict status check `test`, applies to administrators,
  disallows force-push and deletion, and requires zero review approvals;
- only the canonical checkout is registered as a Git worktree; no task worktree
  was created;
- GitHub Issues own project plans and project-wide issue status; pull requests
  and commit history own code changes;
- private operational event storage defaults to ignored
  `.ire/issue-ledger/ledger.sqlite3`; that directory/database did not exist when
  this task resumed, so no local event history needed migration;
- SQLite uses transactional batches, WAL journaling, full synchronous durability,
  and update/delete guards; tests cover SQLite format, immutability, replay,
  deduplication, and concurrent writers;
- repository development pins pnpm `11.19.0` and uv `0.12.7`; `pnpm-lock.yaml`
  and `uv.lock` are current, and CI uses frozen/locked installs;
- pnpm's hoisted linker is configured because this canonical checkout resides
  on a filesystem that does not support symlinks;
- `pnpm test`: 20 tests passed;
- `pnpm check:public`: passed, with the provider-name guard still global;
- `pnpm test:operational`: 41 tests passed;
- `pnpm pack --dry-run`: passed; it did not create a tarball or include `.ire/`;
- `project-continuity` has no remaining moved ledger source files; its unrelated
  prior working-tree change and generated Python caches remain untouched;
- no credential, provider inference call, or host-wide integration setting was
  used or changed.

## Next

Run `node scripts/checkpoint.mjs` in this same folder with `--issue 8` and the
explicit paths listed in `docs/CHECKPOINT-DELIVERY.md`. Wait for required GitHub
CI, merge only after it passes, verify branch cleanup and fast-forwarded `main`,
then record the merge SHA/time here before beginning the next checkpoint.
