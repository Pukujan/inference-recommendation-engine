---
kind: current
version: 1
project: inference-recommendation-engine
status: published
active_task: IRE-0001
updated_at: 2026-09-22T14:30:00Z
---

## State

The v0.1 core is published with explicit policy, pure scoring functions, generated property tests, and metamorphic tests.

## Verified

- public surface is provider-neutral;
- raw price and bounded discount are separate components;
- availability uses capped logarithmic provider breadth;
- runtime evidence is kept separate by channel;
- policy and result revisions are visible.
- GitHub repository is public at `https://github.com/Pukujan/inference-recommendation-engine`;
- issues 1 through 5 track adapter, rolling-window, packaging, agent, and test expansion work.

## Next

Build the private local adapter against the public core contract.
