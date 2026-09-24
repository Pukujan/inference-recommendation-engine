# Issue Ledger operating procedure for agents

Run its offline contract suite from the repository root with
`pnpm test:operational`. This invokes Python through the locked uv project.

This is the adapter contract for any coding agent, runner, TUI, evaluator, or
provider wrapper that uses an IRE recommendation. The agent reports what it
observed; the ledger decides what is corroborated. The agent never edits a
recommendation score directly.

All report inputs use the closed `issue-ledger/report/v1` packet at
`schemas/issue-ledger/v1.report.schema.json`. Unknown packet fields are
rejected so adapter-specific data cannot silently change issue identity.
The packet can carry bounded receipt references, recovery status/result, and a
proposed next action; verification still requires later evidence events.

## Required agent behavior

### 1. Start with an execution identity

Every inference operation should have an execution ID and correlation ID. A
retry gets a new execution ID and points to its parent/retry chain. A second
adapter observing the same execution reuses the receipt or idempotency key; it
does not create a second occurrence.

Record the provider, route, model, operation, workload class, stream mode,
timeout policy, configuration hash, and runtime/environment hash.

### 2. Report an observation immediately

After a failure, timeout, partial capture, provider error, parse error, or
unexpected successful/failed predicate, append `report_submitted`. The report
must include the observed outcome and a bounded summary. It should reference a
receipt, trace, artifact, or hash when one exists.

`reported_only` means “an agent claims this happened.” It is useful for triage,
but it contributes zero to IRE reliability and does not blacklist or promote a
route.

Only an explicitly configured trusted verifier identity may promote a receipt
or reproduction. An event claiming `kind: system` or `verified: true` is still
untrusted when its actor identity is not in that allowlist. Recommendation-
affecting verifier events also require explicit `provenance.producer` and
`provenance.policy_hash`; an allowlisted name alone is not sufficient.

### 3. Attach evidence without rewriting the report

Append `evidence_attached` for a receipt, trace, artifact, or counterexample.
Never replace the original report. If the evidence contradicts it, retain both
and let the projection become disputed, rejected, or retracted.

### 4. Reproduce with a declared recipe

Append `reproduction_attempted` before running the recipe and
`reproduction_completed` afterward. The recipe must identify:

- the input/workload fixture or immutable reference;
- the route/model/provider and relevant configuration;
- the environment/runtime hash;
- the failure predicate being tested;
- the execution ID and receipt produced;
- whether the execution is independent of the original report.

An agent saying “I reproduced it” is not sufficient. The verifier must record
the predicate result and evidence reference. Same-session retries and copied
transcripts are not independent corroboration.

### 5. Separate a proposed fix from a verified resolution

Use `resolution_proposed` for a workaround or code/configuration change. Use
`resolution_verified` only after replaying the original failure and running a
regression check under a declared environment/configuration. A later matching
failure emits `issue_reopened` and returns the issue to an operational failure
state.

## Local commands

The current implementation is local-only and standard-library based. Private
operational records are persisted only in `.ire/issue-ledger/ledger.sqlite3`;
GitHub Issues/PRs own project plans, project issue status, and code changes.

```powershell
# Append one UTF-8 JSON event. Use - to read the event from stdin.
uv run --locked python -B operational/scripts/issue_ledger_store.py `
  --db .ire/issue-ledger/ledger.sqlite3 append event.json

# Normalize an adapter report packet and append report_submitted.
uv run --locked python -B operational/scripts/issue_ledger_store.py `
  --db .ire/issue-ledger/ledger.sqlite3 report report-packet.json

# Or emit the same closed packet from a runner/TUI sidecar using flags.
uv run --locked python -B operational/scripts/issue_ledger_agent.py `
  --db .ire/issue-ledger/ledger.sqlite3 report `
  --provider provider-id --route route-id --model model-id `
  --execution-id run-id --summary "bounded failure summary" `
  --outcome partial --stream-mode sse --failure-phase capture

# Wrap a runner/TUI process. Output stays live; no timeout or kill is added.
uv run --locked python -B operational/scripts/run_with_issue_ledger.py `
  --db .ire/issue-ledger/ledger.sqlite3 run `
  --provider provider-id --route route-id --execution-id run-id -- `
  opencode run "task"

# Import Codex launch receipts. This does not read Kilo transcripts.
# The default route filter is cb/gpt-6-astra. --once scans one time.
uv run --locked python -B operational/scripts/codex_receipt_import.py `
  --db .ire/issue-ledger/ledger.sqlite3 watch `
  --root <receipt-directory> --once

# Import secret-free agent telemetry; failures become report_submitted events.
uv run --locked python -B operational/scripts/telemetry_to_issue_events.py `
  --telemetry telemetry/agent-events.jsonl `
  --db .ire/issue-ledger/ledger.sqlite3

# Rebuild the operational issue projection from SQLite event history.
uv run --locked python -B operational/scripts/issue_ledger_store.py `
  --db .ire/issue-ledger/ledger.sqlite3 project

# Export only accepted, read-only operational inputs for IRE.
uv run --locked python -B operational/scripts/issue_ledger_store.py `
  --db .ire/issue-ledger/ledger.sqlite3 ire

# Optional explicit seven-day operational view; history is unchanged.
uv run --locked python -B operational/scripts/issue_ledger_store.py `
  --db .ire/issue-ledger/ledger.sqlite3 --window-days 7 `
  --as-of 2026-09-22T23:59:59Z `
  --trusted-verifier-id system:issue-ledger-verifier ire

# Diagnose all issues or one stable issue ID.
uv run --locked python -B operational/scripts/issue_ledger_store.py `
  --db .ire/issue-ledger/ledger.sqlite3 diagnose ISSUE-...

# Record a proposed fix, after an agent has actually made or identified it.
uv run --locked python -B operational/scripts/issue_ledger_actions.py `
  --db .ire/issue-ledger/ledger.sqlite3 propose-fix `
  --provider provider-id --route route-id --execution-id run-id `
  --summary "capture fix proposed" --fix-ref commit:abc123

# Attach a bounded receipt without rewriting the original report.
uv run --locked python -B operational/scripts/issue_ledger_actions.py `
  --db .ire/issue-ledger/ledger.sqlite3 attach-evidence `
  --provider provider-id --route route-id --execution-id run-id `
  --summary "receipt captured" --receipt-ref sha256:receipt `
  --evidence-role receipt

# Mark a declared reproduction recipe as started.
uv run --locked python -B operational/scripts/issue_ledger_actions.py `
  --db .ire/issue-ledger/ledger.sqlite3 start-reproduction `
  --provider provider-id --route route-id --execution-id replay-id `
  --summary "reproduction started" --recipe-ref recipe:abc

# Record an already-run reproduction. This command does not run the recipe.
uv run --locked python -B operational/scripts/issue_ledger_actions.py `
  --db .ire/issue-ledger/ledger.sqlite3 record-reproduction `
  --provider provider-id --route route-id --execution-id replay-id `
  --summary "failure reproduced" --result reproduced `
  --recipe-ref recipe:abc --receipt-ref sha256:receipt `
  --independent --verified  # only a configured trusted verifier can promote

# A trusted verifier records both replay and regression receipts.
uv run --locked python -B operational/scripts/issue_ledger_actions.py `
  --db .ire/issue-ledger/ledger.sqlite3 verify-resolution `
  --provider provider-id --route route-id --execution-id verify-id `
  --summary "replay and regression passed" --fix-ref commit:abc123 `
  --replay-receipt-ref sha256:replay --regression-receipt-ref sha256:regression `
  --provenance-producer verifier --policy-hash policy:issue-ledger-v1
```

SQLite stores events in append-only tables with a transaction per batch,
`BEGIN IMMEDIATE` serialization, WAL journaling, full synchronous durability,
and database triggers that reject ordinary event updates or deletes. Identity
conflicts fail closed; projections are rebuilt deterministically from the
database event table. Do not copy the database or its `-wal`/`-shm` sidecars to
GitHub or another service.

## How deduplication works

1. Exact `event_id` replay is one event.
2. The same `idempotency_key` from another adapter is one event if its
   canonical semantics match.
3. The same execution ID is one occurrence even when several event types add
   evidence to it.
4. A different execution ID is a new occurrence, even if text is identical.
5. A retry with a new execution ID but the same correlation ID is a new
   occurrence in one correlation group; it is not independent corroboration.
6. Multiple agents reporting one execution remain one occurrence, but their
   distinct actor identities remain visible in the evidence summary.
7. A changed provider, route, model, operation, stream mode, error class,
   configuration hash, or environment hash changes the deterministic
   fingerprint component.
8. Similarity or embeddings can suggest a review relation but cannot merge
   issues.

## False-positive and false-negative policy

The ledger never deletes a weak claim merely because it is probably wrong.
One or two isolated reports remain low-confidence observations. A
counterexample records a false-positive path and removes recommendation
eligibility. A later receipt can reopen a false-negative path. Reliability
promotion requires either an authoritative deterministic protocol receipt or
independent verified reproduction; the threshold is policy-controlled and
versioned.

## Receiver-facing diagnostic packet

Any future adapter’s `issue diagnose` response should include only:

- stable issue ID and fingerprint;
- affected provider/route/model/configuration;
- the latest bounded next action, receipt references, and runbook references;
- lifecycle and evidence level;
- distinct execution, correlation-group, reporting-agent, and independent
  reproduction counts;
- valid/known time bounds;
- linked receipt/reproduction/fix/regression references;
- the next safe diagnostic action;
- the projection policy hash.

This makes the ledger useful to a fresh agent without treating old prose,
telemetry, or an LLM summary as authority.
