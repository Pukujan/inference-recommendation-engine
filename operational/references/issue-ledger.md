# Issue Ledger adapter reference

When the project enables the local Operational Issue Ledger, every agent or
runner using an IRE recommendation must report operational observations as
append-only events. Reports are proposals; they do not directly change IRE
ranking.

GitHub Issues, pull requests, and commit history own project plans, project
issue status, and code changes. This ledger stores private operational evidence
only in the ignored `.ire/issue-ledger/ledger.sqlite3` database. Do not sync
the database or its sidecars to a remote service.

The adapter input is the closed `issue-ledger/report/v1` packet defined at
`schemas/issue-ledger/v1.report.schema.json`; validate it before invoking the
store. When available, include receipt references, attempted recovery status
and result, and the proposed next action; these remain report metadata until
trusted evidence verifies them.

## Required sequence

1. Give each inference operation an execution ID and correlation ID.
2. After a timeout, provider error, parse error, partial stream, failed
   predicate, or unexpected result, emit `report_submitted` with provider,
   route, model, operation, workload class, stream mode, timeout policy,
   configuration/runtime hashes, outcome, and receipt pointers.
3. Attach receipts or counterexamples with `evidence_attached`; never rewrite
   the original report.
4. Reproduce through a declared recipe and predicate using
   `reproduction_attempted` and `reproduction_completed`. Same-session retries
   are not independent evidence.
5. Use `resolution_proposed` for a workaround and `resolution_verified` only
   after replay plus regression verification. A later matching failure emits
   `issue_reopened`.

`reported_only` remains low-confidence and contributes zero to reliability.
Stable fingerprints and idempotency keys deduplicate adapter copies; semantic
similarity may suggest a relation but cannot merge issues.

Only explicitly configured trusted verifier identities can promote evidence;
an agent cannot self-label as a system verifier.

## Local commands

From the repository root:

```powershell
uv run --locked python -B operational/scripts/issue_ledger_store.py `
  --db .ire/issue-ledger/ledger.sqlite3 report report-packet.json
uv run --locked python -B operational/scripts/issue_ledger_agent.py `
  --db .ire/issue-ledger/ledger.sqlite3 report `
  --provider provider-id --route route-id --execution-id run-id `
  --summary "bounded failure summary" --outcome partial --stream-mode sse
uv run --locked python -B operational/scripts/run_with_issue_ledger.py `
  --db .ire/issue-ledger/ledger.sqlite3 run `
  --provider provider-id --route route-id --execution-id run-id -- `
  opencode run "task"
uv run --locked python -B operational/scripts/issue_ledger_actions.py `
  --db .ire/issue-ledger/ledger.sqlite3 propose-fix `
  --provider provider-id --route route-id --execution-id run-id `
  --summary "bounded fix proposal" --fix-ref commit:abc123
uv run --locked python -B operational/scripts/issue_ledger_actions.py `
  --db .ire/issue-ledger/ledger.sqlite3 attach-evidence `
  --provider provider-id --route route-id --execution-id run-id `
  --summary "receipt captured" --receipt-ref sha256:receipt `
  --evidence-role receipt
uv run --locked python -B operational/scripts/issue_ledger_actions.py `
  --db .ire/issue-ledger/ledger.sqlite3 start-reproduction `
  --provider provider-id --route route-id --execution-id replay-id `
  --summary "reproduction started" --recipe-ref recipe:abc
uv run --locked python -B operational/scripts/telemetry_to_issue_events.py `
  --telemetry telemetry/agent-events.jsonl `
  --db .ire/issue-ledger/ledger.sqlite3
uv run --locked python -B operational/scripts/issue_ledger_store.py `
  --db .ire/issue-ledger/ledger.sqlite3 project
uv run --locked python -B operational/scripts/issue_ledger_store.py `
  --db .ire/issue-ledger/ledger.sqlite3 ire
uv run --locked python -B operational/scripts/issue_ledger_store.py `
  --db .ire/issue-ledger/ledger.sqlite3 --window-days 7 `
  --as-of 2026-09-22T23:59:59Z `
  --trusted-verifier-id system:issue-ledger-verifier ire
uv run --locked python -B operational/scripts/issue_ledger_store.py `
  --db .ire/issue-ledger/ledger.sqlite3 diagnose ISSUE-...
```

The SQLite store is local, transactional, append-only, and rebuildable. It does not contact
providers, execute a reproduction, or grant recommendation authority.

Optional host-native templates and their bounded event mappings are documented
in `docs/ISSUE-LEDGER-HOST-HOOKS.md`. They are not installed automatically;
Codex, Hermes, web agents, and other hosts can use the same sidecar contract
until a separately verified native adapter exists.
