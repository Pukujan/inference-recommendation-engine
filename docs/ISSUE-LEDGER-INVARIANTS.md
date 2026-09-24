# Issue Ledger Invariants and Validation Contract

These invariants are the initial property-driven development contract. They
are normative for the ledger implementation and its adapters.

## Authority and state

1. A lower-authority report cannot supersede a higher-authority accepted
   finding.
2. A worker claim of `complete` never implies `accepted`, `resolved`, or
   `regression_verified`.
3. Project checkpoint/handoff state, IRE Issue Ledger state, and IRE
   recommendation state remain separate projections.
4. An agent cannot directly write an accepted issue, accepted resolution, or
   recommendation change; it can only append a proposal/report event.
5. No destructive update removes an earlier report, receipt, counterexample,
   or resolution attempt.

## Identity and deduplication

6. Replaying the same event with the same idempotency key produces one durable
   event and one occurrence.
7. The same execution receipt cannot count twice because it arrived through
   two adapters.
8. Fingerprints are deterministic for a fixed fingerprint version and
   canonical input.
9. Changing only prose, formatting, or report author does not change the
   fingerprint of the underlying occurrence.
10. Changing provider, route, operation, stream mode, error class, or
    configuration hash creates a distinct fingerprint component.
11. A fingerprint-version migration creates linked projections and preserves
    the old grouping; it does not silently rewrite history.
12. Similarity may create a review candidate, never an automatic merge.

## Evidence and reliability

13. `reported_only` evidence contributes zero to accepted reliability metrics.
14. Missing, ambiguous, stale, partial, or uncorrelated evidence cannot count
    as success.
15. A deterministic verifier may accept a protocol failure from one exact
    receipt; model-quality claims require independent repeated executions.
16. Two reports from one retry chain, transcript, or session do not count as
    independent corroboration.
17. Multiple agents may witness one execution; the occurrence remains one, but
    distinct reporting-agent identities remain visible for epistemic review.
18. Multiple execution IDs sharing one correlation ID remain one retry/correlation
    group and cannot inflate independent corroboration.
19. Conflicting correlation IDs for one execution fail closed rather than being
    resolved by event arrival order.
20. Recommendation-affecting verifier events require both an allowlisted
    verifier identity and explicit provenance; a self-asserted identity alone
    cannot promote evidence.
21. Absence of an issue report cannot increase a route's reliability.
22. A counterexample can retract an accepted claim but cannot erase its prior
    existence or provenance.
23. A resolution requires replay of the original failure and a regression
    check under the declared environment/configuration; both receipt references
    and trusted verifier provenance are required.
24. An untrusted counterexample or reopen claim cannot mutate an accepted
    projection; it remains a report until trusted evidence verifies it.
25. A later trusted matching failure reopens a resolved issue instead of being
    hidden by the previous resolution.

## Bitemporal and projection behavior

26. `valid_at` describes target-system time; `known_at` describes ledger time.
    Late evidence never rewrites either original timestamp.
27. A rolling-window projection can remove an observation from current scoring
    without deleting it from history.
28. Rebuilding a projection from the same event set and policy hash produces
    the same issue state and recommendation inputs.
29. Changing only projection policy changes the projection hash and result, not
    the canonical event history.
30. The IRE projection is read-only with respect to the canonical ledger.
31. Static evaluations and dynamic operational observations remain separately
    addressable and separately weighted.

## Property-driven tests

The implementation should generate arbitrary sequences containing:

- duplicated events;
- retries and fallback routes;
- late-arriving evidence;
- contradictory reports;
- partial streams;
- provider and client timeouts;
- changing fingerprint versions;
- resolution followed by regression;
- multiple agents observing one execution;
- one agent observing many executions.

Each sequence must preserve invariants 1–27.

## Metamorphic tests

For a fixed canonical receipt, apply transformations and assert the required
relation:

| Transformation | Required relation |
|---|---|
| Duplicate the same event | issue/occurrence count unchanged |
| Change report wording only | fingerprint and lifecycle unchanged |
| Add an unverified report | accepted reliability unchanged |
| Add an independent verified reproduction | evidence level may promote; never demote |
| Move an observation outside the rolling window | history unchanged; dynamic projection may change |
| Reorder event delivery | final projection unchanged after replay |
| Replay through a second adapter | one correlated occurrence |
| Change only display formatting | hashes and state unchanged |
| Apply a new policy hash | history unchanged; projection hash/result may change |
| Add a counterexample | claim becomes disputed/retracted/reopened, never deleted |
| Resolve then replay the original failure | issue becomes regressed/reopened |
| Change provider or route identity | distinct subject/fingerprint |
| Import the same Codex thread twice | one stored report |
| Re-encode the same Codex receipt as UTF-16 | error code and outcome unchanged |
| Insert prompt or tool text into a Codex receipt | stored report does not contain that text |
| Change only the assistant wording | error code and outcome unchanged |
| Offer a non-matching model under the owner-route filter | no report stored |

## Test layers

1. Schema tests: JSON Schema and fixture validation.
2. Unit tests: fingerprinting, lifecycle transitions, authority gates.
3. Property tests: arbitrary event sequences and idempotency.
4. Metamorphic tests: transformations above.
5. Adapter conformance tests: every agent/runtime emits equivalent normalized
   receipts.
6. Offline replay tests: historical Hades/IRE receipts rebuild the same
   projection.
7. Fresh-receiver tests: a new agent can find an issue, its evidence level,
   runbook, and safe next diagnostic action without old chat.
