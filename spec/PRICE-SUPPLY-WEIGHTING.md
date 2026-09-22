# Empirical price-supply weighting

The public engine supports a price ladder for each side of a model route:

```json
{
  "inputLadder": [[0.01, 4], [0.09, 500]],
  "outputLadder": [[0.04, 4], [0.36, 500]]
}
```

Each pair is `price per million tokens` and `available count`. The count is treated as a supply/redundancy signal, not as a concurrency or success guarantee.

For every candidate pool, the engine:

1. transforms counts with `log1p`;
2. computes each point's empirical percentile against the pooled distribution;
3. computes the local derivative `Δlog1p(availability) / Δlog(price)`;
4. combines relative supply, distribution percentile, derivative uplift, and a lower-price preference into a point weight;
5. calculates effective input/output prices and combines them with a weighted geometric mean.

The default near-free boundary is `$0.10` per million, so the entire `0.0x` band qualifies. Price utility saturates in that band; availability, recency, strength, and measured reliability separate candidates. The boundary and all weights are policy controls.

The API is provider-neutral. Adapters own source collection, rolling windows, runtime observations, credentials, and temporal storage. The core receives normalized ladders and returns deterministic, auditable values.
