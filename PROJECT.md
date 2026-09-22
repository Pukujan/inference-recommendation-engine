# Project

Inference Recommendation Engine is a local-first, provider-neutral ranking core for inference routes.

## Stable goals

- Make price, breadth, performance, and reliability trade-offs explicit.
- Keep policy weights editable and versioned.
- Preserve reproducible rankings across agents and sessions.
- Keep the public package independent of credentials and provider-specific clients.
- Prefer small pure functions with executable invariants.

## Non-goals for v0.1

- hosted control plane;
- provider credentials;
- agent-specific integrations;
- automatic source discovery;
- hidden model or vendor preferences.
