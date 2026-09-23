# Operational Issue Ledger Specification

Status: Draft v0.1

This specification defines the operational issue ledger for the
Inference Recommendation Engine (IRE). It owns operational issue evidence for
inference routes and is designed to interoperate with separate project
checkpoint and handoff systems.

## Authority

GitHub Issues, pull requests, and commit history are authoritative for project
plans, project-wide issue status, and code changes. The local Issue Ledger is
authoritative only for private operational observations, evidence, attempted
fixes, reproductions, and regressions related to inference use. It is stored in
the ignored `.ire/issue-ledger/ledger.sqlite3` SQLite database; it is never
uploaded or committed. Public GitHub records may cite a redacted content ID or
hash, but must not contain private records or source evidence.

It does not supersede:

1. current user intent or authorization;
2. immutable raw receipts and artifacts;
3. repository/live-state verification;
4. accepted release or deployment gates.

The operational-evidence authority boundary is:

```text
user intent / authorization
    > immutable evidence and reproducible receipts
    > accepted Issue Ledger findings
    > agent reports and executor claims
    > derived memory and summaries
```

GitHub owns project state and its public work history. Checkpoint/handoff
records own current local resume context. The SQLite Issue Ledger owns private
operational evidence and its derived operational issue lifecycle. The ranking
core consumes only explicitly accepted, read-only projections; it never treats
a raw agent report as a reliability measurement. The ledger is guidance for
configuring and operating agent systems, not a new authority for model
selection.

## Objects

### Report

An agent, human, adapter, or service may submit a report. A report is a claim,
not a confirmed incident. Reports are immutable and idempotent by `event_id`
and `idempotency_key`.

### Occurrence

An occurrence is one execution observation linked to an execution ID, route,
configuration, environment, correlation ID, and evidence reference. Multiple
prose reports about one occurrence count as one occurrence, while their
distinct reporting agents remain visible as witness metadata.

### Issue projection

An issue projection groups occurrences that share a versioned deterministic
fingerprint. It keeps related, contradictory, superseded, and duplicate
relationships without deleting history.

### Reproduction attempt

A reproduction attempt executes a stored recipe against a declared environment
and evaluates a declared predicate. “The agent knows how to reproduce it” is
not evidence until the recipe has actually run.

### Resolution

A proposed fix is not a resolution. A resolution requires a successful replay
of the original failure and a regression check showing that the fix does not
reintroduce the failure. A `resolution_verified` payload must carry distinct
`replay_receipt_ref` and `regression_receipt_ref` values, with evidence refs
and verifier provenance.

## Evidence lifecycle

```text
reported_only
  -> observed_once
  -> receipt_backed
  -> replayable
  -> operationally_reproduced
  -> independently_corroborated
  -> accepted
  -> mitigated / resolved
  -> regression_verified
```

Any state may also become `retracted`, `superseded`, or `stale_pending_review`.
Those transitions append an event; they never rewrite the previous claim.

### Promotion rules

- `reported_only` may open an issue candidate but cannot change IRE ranking.
- One exact deterministic receipt may establish a protocol or parser failure
  when the verifier is authoritative.
- A self-reported timeout, quality failure, or model-behavior claim requires
  replay or independent corroboration before it changes reliability.
- Two reports from the same session, retry chain, or copied transcript are not
  two independent observations.
- A retry chain may contain multiple execution IDs but one correlation ID; it
  remains one independence group until a verifier establishes otherwise.
- Absence of a report is not evidence of success.
- An unresolved or partially captured stream is `unknown` or `failed_capture`,
  never implicit success.

## Fingerprinting and deduplication

The primary fingerprint is deterministic and versioned. It includes the
normalized values of:

```text
provider
route/model
operation
workload_class
failure_phase
protocol / stream_mode
HTTP status or provider error code
finish reason / capture status
configuration hash
environment/runtime hash
```

The fingerprint version and canonical value are stored with every issue. If
normalization changes, a new fingerprint version is created and linked to the
old projection; history is not silently regrouped.

String similarity, embeddings, and LLM clustering may suggest `related_to`
links. They must not silently create `same_as` or merge two issues.

## False positives and false negatives

The ledger preserves both the original claim and later counterevidence.

- A false positive becomes `retracted` or `rejected` with a counterexample and
  verifier reference.
- A false negative is represented by a later receipt or reproduction that
  reopens or escalates the issue.
- One or two isolated reports are quarantined as low-confidence observations;
  they are not erased and do not permanently blacklist a route.
- A verified provider-side or protocol-side failure may be accepted with one
  authoritative receipt when its predicate is deterministic.
- Verifier authority is an explicit, versioned allowlist/configuration
  boundary; `actor.kind`, a self-asserted `verified` flag, or an agent-written
  summary never grants acceptance authority.
- A model-quality or agent-behavior claim requires repeated, independently
  attributable executions with a declared evaluator.

## Bitemporal semantics

Every durable event records:

- `valid_at`: when the observation or claim was true in the target system;
- `known_at`: when this repository learned or recorded it.

Rolling operational projections may use a seven-day window, but historical
events remain queryable. Late-arriving evidence updates the current projection
without changing the original `known_at` timestamp.

## Agent reporting contract

Every supported agent adapter must expose a local report operation equivalent to:

```text
issue report
issue attach-evidence
issue reproduce
issue propose-fix
issue verify-resolution
issue diagnose
```

The input to `issue report` is the closed, versioned
`issue-ledger/report/v1` packet defined by
`schemas/issue-ledger/v1.report.schema.json`. Adapters must reject unknown
packet fields before append; adapter-specific details belong in a referenced
receipt or artifact.

When available, the packet carries `receipt_refs`, `attempted_recovery`, and
`proposed_next_action`; later evidence and resolution events remain the
authoritative record for verification.

The adapter must report at least:

- agent and harness identity;
- execution and correlation IDs;
- provider, route, model, operation, and workload class;
- stream/capture/timeout mode;
- configuration and environment hashes;
- observed outcome and error classification;
- evidence or receipt references;
- attempted recovery and its result;
- proposed next action.

Full prompts and outputs are optional local artifacts. Their hashes and
content-addressed references are sufficient for the operational ledger unless
the user explicitly enables content capture.

## Recommendation boundary

IRE receives a read-only projection containing only accepted or explicitly
weighted observations. The projection must carry:

- issue and evidence IDs;
- evidence level;
  - sample, correlation-group, reporting-agent, and independent-execution counts;
- valid/known time bounds;
- policy and projection hashes;
- route/model/configuration identity;
- diagnostic receipt pointers, runbook pointers, and the latest proposed next
  action for a fresh agent.

The read-only export is versioned as `ire/issue-ledger/v1` and is validated by
`schemas/issue-ledger/v1.ire.schema.json`; consumers must treat it as an input
projection, never as an authority that can write back to the ledger.

Static model evaluations, release recency, price, provider availability, and
dynamic operational reliability remain separate components. An agent report
cannot directly lower or raise a route score.

## Required outputs for every accepted issue

An accepted issue must have:

1. a stable issue ID;
2. a versioned fingerprint;
3. at least one occurrence reference;
4. evidence level and provenance;
5. a reproduction status and recipe reference when reproduction is required;
6. explicit affected route/configuration scope;
7. linked attempted fixes or a recorded reason that no fix is known;
8. a resolution or supersession decision;
9. a regression reference before it can be marked resolved;
10. a route-card/runbook reference if it affects an IRE recommendation.
