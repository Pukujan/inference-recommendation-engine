# Operational Issue Ledger — pre-build research record

Status: completed research pass, 2026-09-22
Scope: local-first, multi-agent operational issue reporting and evidence
projection; no live provider changes and no recommendation-score mutation.

## Research method and epistemic boundary

The first pass searched five GitHub API query families covering agent memory,
LLM observability, LLM evaluation, durable agent execution, and multi-agent
orchestration. The deduplicated triage corpus contained **120 repository
candidates**. This is a breadth inventory, not a claim that every candidate
received a full code audit.

The deeper review covered the representative systems below. Their documented
patterns were compared against the Project Continuity authority model and the
FOSSIL evidence/provenance boundary. Repository popularity, search rank, stars,
and README claims were treated as discovery signals, not validation evidence.

## Representative primary sources reviewed

| Area | System | Reusable pattern | Boundary or non-adoption |
|---|---|---|---|
| project continuity | [Project Continuity](https://github.com/biaobiao2233/project-continuity) | spine, handoff, work nodes, ownership, accepted-state separation | remains current-state and handoff authority; it does not become the issue evidence store |
| durable execution | [Temporal](https://github.com/temporalio/temporal) | replayable workflows, durable history, explicit activity boundaries | useful execution model; not a reason to add a daemon before local use justifies it |
| durable agent graph | [LangGraph](https://github.com/langchain-ai/langgraph) | checkpointed state and resumable graph execution | checkpoints are evidence pointers, not proof that a claim is true |
| agent ledger | [AgentLedger](https://github.com/yaogdu/AgentLedger) | structured agent/task records and lifecycle accounting | ledger fields still require independent evidence gates |
| agent memory | [Continuity](https://github.com/Noctilucenty/Continuity) | continuity-oriented context persistence | semantic memory cannot replace current repo/live verification |
| LLM tracing | [Langfuse](https://github.com/langfuse/langfuse) | traces, observations, scores, datasets | telemetry is an observation source, not an automatic incident verdict |
| LLM telemetry | [OpenLLMetry](https://github.com/traceloop/openllmetry) | OpenTelemetry-compatible spans and semantic attributes | normalize into the local receipt contract; preserve raw span references |
| evaluation | [Promptfoo](https://github.com/promptfoo/promptfoo) | repeatable assertions, red-team/eval workflows, regression checks | evaluator output must remain versioned evidence with declared predicates |
| experiment tracking | [MLflow](https://github.com/mlflow/mlflow) | run/artifact/metric lineage | experiment metadata does not itself establish operational causality |
| error grouping | [Sentry](https://www.sentry.help/en/articles/13964350-why-are-my-events-grouped-or-separated-incorrectly-in-sentry) | deterministic grouping plus merge/split review concepts | similarity may suggest related issues; it must not silently merge ledger issues |
| provenance | [W3C PROV-O](https://www.w3.org/TR/prov-o/) | entities, activities, agents, derivation relations | use for evidence lineage, not as the mutable issue projection |
| constraint validation | [W3C SHACL](https://www.w3.org/TR/shacl/) | graph shape validation | optional export/validation layer; JSON Schema remains the first local boundary |
| property testing | [Hypothesis](https://hypothesis.readthedocs.io/en/latest/) | generated sequences and shrinking counterexamples | add after the pure projection core exists |
| evidence store | [FOSSIL](https://github.com/Pukujan/fossil-core) | append-only artifacts, content addressing, provenance, rebuildable projections | FOSSIL is treated as the evidence/provenance layer; its public README states the software is proprietary |

The broader triage set also included Mem0, Letta, EverOS, Beads, Engram,
Cognee, Hindsight, Conductor, Opik, Helicone, TensorZero, RouteLLM, and other
systems in the memory, routing, evaluation, observability, and orchestration
categories. They informed comparison questions, but are not represented here
as deeply audited or adopted implementations.

## Findings that changed the design

1. **Reports and evidence must be different objects.** Most systems make it
   easy to emit a trace or issue. The important missing boundary for this use
   case is that an agent's “it failed” message is a proposal until a receipt,
   replay, or independent corroboration supports it.
2. **Deduplication must use execution identity before semantic similarity.**
   A stable, versioned fingerprint should use provider/route, operation,
   workload, phase, protocol, error/capture status, configuration, and runtime
   identity. Embeddings or LLM clustering can create a review link only.
3. **A retry chain is not independent evidence.** Attempts need execution,
   correlation, and parent/retry identifiers so the projection does not inflate
   reliability failures or successes.
4. **Reproduction is a first-class lifecycle.** A stored recipe and predicate
   are required before an agent's claim can be called reproducible.
5. **Resolution requires regression verification.** A workaround, a successful
   retry, and a fixed issue are distinct states.
6. **Telemetry helps detect and correlate; it does not recover lost truth.** A
   successful HTTP status or usage record cannot prove that the local wrapper
   captured the complete streamed response unless terminal/capture evidence is
   present.
7. **Bitemporal history is required.** The time an issue was valid and the time
   the ledger learned about it must remain separate so late receipts do not
   rewrite history.
8. **Recommendation input must be read-only.** IRE can consume accepted,
   policy-filtered issue projections alongside price, availability, recency,
   evaluation, and reliability data; it cannot edit the canonical ledger.

## Pre-build gates

The following gates must pass before adding a daemon or automatic host-native
adapters. The schema, pure reducer, locked local store, telemetry importer,
generic sidecar, and synthetic OpenCode/Claude adapter conformance checks have
now passed the first local gate:

1. event and projection JSON Schemas load and reject unknown top-level fields;
2. every event has stable identity, actor, subject, and provenance fields;
3. report-only evidence contributes zero to accepted recommendation reliability;
4. duplicate delivery and cross-adapter replay are idempotent;
5. fingerprint changes are versioned and do not rewrite historical grouping;
6. same-session retries are not counted as independent corroboration;
7. late and out-of-order events rebuild to the same projection;
8. counterexamples retract or reopen claims without deleting history;
9. resolution cannot be marked verified without replay and regression evidence;
10. an IRE export is demonstrably read-only and carries policy/source hashes;
11. a fresh receiver can locate the issue, evidence level, runbook pointer, and
    next safe diagnostic action from artifacts alone;
12. adapter conformance tests show equivalent normalized receipts for each
    supported agent/runtime.

These gates are represented in [ISSUE-LEDGER-SPEC.md](ISSUE-LEDGER-SPEC.md),
[ISSUE-LEDGER-INVARIANTS.md](ISSUE-LEDGER-INVARIANTS.md), and the first
contract tests at
`operational/tests/test_issue_ledger_contract.py`. The current slice
does not claim native automatic hooks inside Hermes, Codex, or any web client.
OpenCode and Claude Code now have optional, explicitly installed templates
documented in [ISSUE-LEDGER-HOST-HOOKS.md](ISSUE-LEDGER-HOST-HOOKS.md); their
live host installation and acceptance remain separate gates.

## Planned evidence architecture

```text
agent/runtime adapters
        │  append proposal/report + receipt refs
        ▼
FOSSIL or equivalent local evidence store
        │  immutable artifacts / provenance / content hashes
        ▼
Issue Ledger event reducer
        │  deterministic fingerprint + bitemporal projection
        ▼
accepted issue projection ───────► runbooks / fresh-agent diagnostics
        │
        └── read-only export ─────► IRE recommendation inputs
```

PROV/OWL is appropriate for exportable provenance relationships; SHACL is
appropriate for validating an RDF projection. Neither should be allowed to
write live state or replace the append-only event and reducer contract.
