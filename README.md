# Inference Recommendation Engine

A small, provider-neutral engine for ranking inference routes by raw price, bounded discount benefit, route breadth, and measured performance.

The core is deliberately local-first. Integrations supply normalized route records; the engine returns deterministic rankings. Provider-specific credentials, source clients, and operational records stay outside this public package.

The package also exposes an empirical price-supply layer. Give a candidate input/output price ladder as `[pricePerMillion, availableCount]` pairs (or `{ price, available }` objects); the engine pools observed availability across candidates, applies distribution and local-derivative weights, saturates the configurable `0.0x` near-free band, and preserves every raw point for audit.

## Fast start

```bash
npm install
npm test
npm run check:public
npm run demo
```

The demo reads `examples/routes.json` and `policy.example.json`, then writes a local recommendation file. To embed the engine:

```js
import { prepareCandidate, rankCandidates } from 'inference-recommendation-engine';

const candidate = prepareCandidate({
  id: 'route-a',
  providerCount: 3,
  routeCount: 6,
  price: { inputPerMillion: 0.2, outputPerMillion: 0.8 },
  privateObservations: [
    { eventId: 'one', result: 'success', ttftMs: 900, durationMs: 4200, completionTokens: 300 }
  ]
}, { minimumObservations: 1 });

const ranking = rankCandidates([candidate], { performance: { minimumObservations: 1 } }, 'private');
```

For a ladder-only view:

```js
import { rankPriceSupplyCandidates } from 'inference-recommendation-engine';

const ranked = rankPriceSupplyCandidates([
  { id: 'route-a', price: {
    inputLadder: [[0.01, 4], [0.09, 500]],
    outputLadder: [[0.04, 4], [0.36, 500]]
  } }
], { nearFreeCostCapPerMillion: 0.1 });
```

## Contract

The public contract is in [spec/INVARIANTS.md](spec/INVARIANTS.md) and [spec/policy.schema.json](spec/policy.schema.json). The important defaults are:

- raw price is the primary cost signal; discounts are bounded and secondary;
- provider breadth and measured runtime reliability are separate signals;
- missing measurements cannot improve a route;
- public and private evidence channels are ranked separately;
- lower confidence is represented explicitly with `provisional` or `unknown` status;
- score weights and gates are versioned policy, not hidden constants;
- ties resolve deterministically by score, raw cost, and stable identifier.

## Local integration boundary

An adapter may read any compatible source and map it to the route shape accepted by `prepareCandidate`. It should preserve:

- cost units and currency;
- provider and route counts;
- result classification and client exclusions;
- first-token, total-duration, routing, output-token, and cost measurements;
- `validAt` and `knownAt` timestamps when records are versioned;
- source and policy revisions.

The public engine never contacts a provider, stores credentials, or assumes a source format.

## Installation and export

For an application:

```bash
npm install github:Pukujan/inference-recommendation-engine
```

For a portable archive:

```bash
npm pack
```

The package exposes `src/index.mjs` and requires Node.js 20 or newer. Adapter modules can remain private or live in a separate repository.

## Research basis

The design follows service-level objectives, lower-confidence reliability bounds, aggregatable histograms, and standard inference-operation measurements. The rationale and links are recorded in [RESEARCH-BASIS.md](RESEARCH-BASIS.md).

## Scope

This repository is the reusable engine and its executable contract. It is not a provider catalog, a credential store, a hosted control plane, or an agent-specific plugin.
