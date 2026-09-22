# Research basis

This short note records why the public core has the signals and boundaries it does. The private integration can add source-specific fields without changing the core contract.

## Reliability and service objectives

Google’s SRE guidance recommends explicit service-level indicators and objectives rather than a single vague reliability label. The engine therefore keeps attempts, service outcomes, client exclusions, timeout rate, and a lower confidence bound as separate values.

- [Google SRE: Service Level Objectives](https://sre.google/sre-book/service-level-objectives/)
- [Google SRE: Measuring Reliability](https://sre.google/resources/practices-and-processes/measuring-reliability/)

## Percentiles and aggregatable observations

Percentiles are useful for first-token and completion latency, but a precomputed percentile cannot safely be averaged across workers. The public core accepts raw bounded measurements at the adapter boundary and emits p50, p95, and p99 summaries. A histogram or native histogram can be used when the local collector must aggregate across processes.

- [Prometheus histograms and summaries](https://prometheus.io/docs/practices/histograms/)
- [Prometheus metric types](https://prometheus.io/docs/concepts/metric_types/)

## Inference-operation measurements

Standard GenAI instrumentation identifies token usage, operation duration, time to first chunk, time per output chunk, server duration, and agent or tool spans as useful signals. This engine keeps its own small normalized contract so an adapter can map those conventions without coupling the scorer to one instrumentation vendor.

- [OpenInference specification](https://github.com/Arize-ai/openinference/blob/main/spec/README.md)

## Redundancy and scaling

Provider breadth is capped and transformed with `log1p`. This makes the first few independent routes valuable while preventing a very large pool from overwhelming price or measured performance. The cap and component weights are explicit policy so they can be changed and compared.

## Property and metamorphic testing

The test suite uses generated inputs for invariants and relation-based tests for transformations that should preserve ordering or bounded effects. This matches the purpose of property-based testing and metamorphic testing: expose assumptions that a small set of example fixtures would miss.

- [fast-check introduction](https://fast-check.dev/docs/introduction/)
- [NIST: Metamorphic Testing for Cybersecurity](https://www.nist.gov/publications/metamorphic-testing-cybersecurity)

## Versioning

The public package follows Semantic Versioning. Policy revisions are carried in every ranking result, and a policy change is treated as a reviewable contract change.

- [Semantic Versioning 2.0.0](https://github.com/semver/semver.org/blob/gh-pages/spec/v2.0.0.md)
