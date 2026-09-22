# Versioning

The package follows [Semantic Versioning](https://semver.org/).

- MAJOR changes break the exported API or ranking contract.
- MINOR adds a backward-compatible function, adapter interface, or policy field.
- PATCH fixes an implementation defect without changing the intended contract.

Every ranking result includes the policy revision. A policy revision is required when weights, gates, normalization anchors, or evidence semantics change.
