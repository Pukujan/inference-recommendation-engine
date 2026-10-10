import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import {
  OBSERVATION_CONTRACT_VERSION,
  OBSERVATION_REQUIRED_FIELDS,
  RESULT_CLASSES,
  ROUTE_CONTRACT_VERSION,
  ROUTE_REQUIRED_FIELDS,
  validateObservationRecord,
  validateRouteRecord,
  validateRouteRecords
} from '../src/index.mjs';

function routeRecord(overrides = {}) {
  return {
    schemaVersion: ROUTE_CONTRACT_VERSION,
    id: 'route-a',
    currency: 'USD',
    providerCount: 3,
    routeCount: 6,
    price: { inputPerMillion: 0.2, outputPerMillion: 0.8 },
    ...overrides
  };
}

function observationRecord(overrides = {}) {
  return {
    schemaVersion: OBSERVATION_CONTRACT_VERSION,
    eventId: 'event-1',
    result: 'success',
    clientExcluded: false,
    validAt: '2026-01-01T00:00:00Z',
    knownAt: '2026-01-01T00:01:00Z',
    ...overrides
  };
}

function loadSchema(name) {
  return JSON.parse(readFileSync(new URL(`../schemas/adapter/${name}`, import.meta.url), 'utf8'));
}

test('a well-formed route record passes', () => {
  assert.deepEqual(validateRouteRecord(routeRecord()), { valid: true, errors: [] });
});

test('a well-formed observation record passes', () => {
  assert.deepEqual(validateObservationRecord(observationRecord()), { valid: true, errors: [] });
});

test('optional route fields are accepted and bitemporal bounds are enforced', () => {
  const full = routeRecord({
    discountPct: 10,
    validAt: '2026-01-01T00:00:00Z',
    knownAt: '2026-01-01T00:05:00Z',
    sourceRevision: { sourceId: 'catalogue', revision: 'rev-7', retrievedAt: '2026-01-01T00:05:00Z' }
  });
  assert.equal(validateRouteRecord(full).valid, true);
  const result = validateRouteRecord(routeRecord({ validAt: '2026-01-01T00:00:00Z' }));
  assert.equal(result.valid, false);
  assert.ok(result.errors.includes('validAt and knownAt must be provided together'));
});

test('a route record missing required fields reports each one', () => {
  const result = validateRouteRecord({ schemaVersion: ROUTE_CONTRACT_VERSION });
  assert.equal(result.valid, false);
  for (const field of ROUTE_REQUIRED_FIELDS) {
    if (field === 'schemaVersion') continue;
    assert.ok(result.errors.includes(`missing required field: ${field}`), field);
  }
});

test('a wrong currency is rejected', () => {
  const result = validateRouteRecord(routeRecord({ currency: 'EUR' }));
  assert.equal(result.valid, false);
  assert.ok(result.errors.includes('currency must be USD'));
});

test('a negative price is rejected', () => {
  const result = validateRouteRecord(routeRecord({ price: { inputPerMillion: -1, outputPerMillion: 0.8 } }));
  assert.equal(result.valid, false);
  assert.ok(result.errors.includes('price.inputPerMillion must be a non-negative finite number'));
});

test('an unknown outcome class is rejected', () => {
  const result = validateObservationRecord(observationRecord({ result: 'error' }));
  assert.equal(result.valid, false);
  assert.ok(result.errors.some((error) => error.startsWith('result must be one of')));
});

test('an unknown observation field is rejected', () => {
  const result = validateObservationRecord(observationRecord({ adapterNote: 'x' }));
  assert.equal(result.valid, false);
  assert.ok(result.errors.includes('unknown observation field: adapterNote'));
});

test('an unknown contract major reports version drift', () => {
  const route = validateRouteRecord(routeRecord({ schemaVersion: 'ire-adapter-route/v2' }));
  assert.equal(route.valid, false);
  assert.ok(route.errors.some((error) => error.includes('unknown contract major')));
  const observation = validateObservationRecord(observationRecord({ schemaVersion: 'ire-adapter-observation/v2' }));
  assert.ok(observation.errors.some((error) => error.includes('unknown contract major')));
});

test('batch validation prefixes each error with its index', () => {
  const result = validateRouteRecords([routeRecord(), { id: 'broken' }]);
  assert.equal(result.valid, false);
  assert.ok(result.errors.length > 0);
  assert.ok(result.errors.every((error) => error.startsWith('routes[1]: ')));
});

test('the schemas and the runtime validators agree', () => {
  const route = loadSchema('v1.route.schema.json');
  const observation = loadSchema('v1.observation.schema.json');
  assert.equal(route.$schema, 'https://json-schema.org/draft/2020-12/schema');
  assert.equal(observation.$schema, 'https://json-schema.org/draft/2020-12/schema');
  assert.equal(route.$id, 'https://inference-recommendation-engine.local/schemas/adapter/v1.route.schema.json');
  assert.equal(observation.$id, 'https://inference-recommendation-engine.local/schemas/adapter/v1.observation.schema.json');
  assert.equal(route.properties.schemaVersion.const, ROUTE_CONTRACT_VERSION);
  assert.equal(observation.properties.schemaVersion.const, OBSERVATION_CONTRACT_VERSION);
  assert.deepEqual([...route.required].sort(), [...ROUTE_REQUIRED_FIELDS].sort());
  assert.deepEqual([...observation.required].sort(), [...OBSERVATION_REQUIRED_FIELDS].sort());
  assert.deepEqual(observation.properties.result.enum, [...RESULT_CLASSES]);
});

test('the shipped example satisfies the route contract', () => {
  const example = JSON.parse(readFileSync(new URL('../examples/routes.json', import.meta.url), 'utf8'));
  assert.deepEqual(validateRouteRecords(example), { valid: true, errors: [] });
});
