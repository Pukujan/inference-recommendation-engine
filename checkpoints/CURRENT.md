---
kind: current
version: 1
project: inference-recommendation-engine
status: ready_for_checkpoint
active_task: IRE-0002-streaming-liveness-and-resume-guidance
updated_at: 2026-09-23T23:50:16Z
---

## State

The v0.1 ranking core remains published and unchanged. The operational-ledger
relocation, local SQLite storage, pnpm/uv toolchain, and CI-gated checkpoint
workflow are integrated in the canonical checkout and pass their local gates.
The private ledger projection remains separate from ranking behavior. GitHub
issue #8 is the public plan and project-issue record for this work.

## Verified

- GitHub repository: `https://github.com/Pukujan/inference-recommendation-engine`;
- relocation checkpoint PR [#9](https://github.com/Pukujan/inference-recommendation-engine/pull/9)
  merged at `2026-09-23T23:48:37Z` as
  `4a4fbbb81921312356badb78d751f4f35cace731`; both required `test` CI checks
  passed before merge;
- canonical `main` is synchronized with `origin/main` at
  `4a4fbbb81921312356badb78d751f4f35cace731`; the temporary local and remote
  checkpoint branches are gone and the checkout is clean;
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
- a pre-sync safety stash remains available and has not been dropped;
- no credential, provider inference call, or host-wide integration setting was
  used or changed.

## Next

First deliver this merge-evidence update as a small checkpoint using the same
helper, `--issue 8`, and only the two checkpoint-record paths. Then research and
document evidence-based streaming/liveness and resume guidance as IRE-0002,
keeping it separate from ranking. Treat the configured inference-gateway-only
premium catalog lane as a later, separate increment; paid inference requires
explicit human authorization and must not be called without it.
