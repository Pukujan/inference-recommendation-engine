# Invariants and relations

This is the executable design contract for version 0.1. It protects the user-controlled policy from accidental changes during adapter work.

## Invariants

1. Every price has explicit input and output units. Missing or invalid prices are unavailable, never zero.
2. Raw price is the dominant cost component. Discount contributes only through its bounded policy weight.
3. With all else fixed, lowering raw price cannot lower raw-price utility.
4. Provider breadth is monotone: adding an eligible independent provider cannot reduce availability.
5. A route below the minimum provider breadth cannot be `qualified` in the public channel.
6. Missing runtime measurements cannot increase a score or create measured evidence.
7. Public and private evidence channels remain separate; a private result cannot silently become a public result.
8. Client exclusions are not service successes or failures, and the denominator is explicit.
9. Duplicate event identifiers are counted once by the default aggregation rule.
10. A versioned record preserves both `validAt` and `knownAt`; neither timestamp may replace the other.
11. Policy weights are non-negative, sum to one within each component group, and are returned with the result.
12. Ranking is deterministic for a fixed input and policy, including ties.
13. A score is bounded in `[0, 1]`.
14. A reliability gate is based on a lower confidence bound, not only on the observed success ratio.
15. A rolling window is an adapter concern; the core never silently mixes windows.

## Metamorphic relations

- Permuting route records preserves the ranking.
- Adding a more expensive route cannot improve an existing route’s raw cost utility.
- Adding an eligible provider preserves or increases availability.
- Multiplying every price by the same positive factor preserves raw-price ordering.
- Changing only discount changes only the bounded discount contribution and never the raw-price fields.
- Adding an unknown runtime field cannot improve a route.
- Adding a client-excluded attempt cannot change the service-success denominator.
- Repeating an existing event identifier does not change the aggregate.
- Changing policy weights changes only the weighted components and is visible through the policy revision.
- Public and private rankings can be compared, but their evidence fields are never merged implicitly.

## Required adapter fields

An adapter should map records to:

```json
{
  "eventId": "stable-id",
  "result": "success | failure | timeout | cancelled",
  "clientExcluded": false,
  "ttftMs": 1200,
  "durationMs": 8000,
  "routingMs": 80,
  "completionTokens": 500,
  "costUnits": 0.004,
  "validAt": "2026-01-01T00:00:00Z",
  "knownAt": "2026-01-01T00:01:00Z"
}
```

The engine does not require every optional measurement, but missing values remain visible as missing evidence.
