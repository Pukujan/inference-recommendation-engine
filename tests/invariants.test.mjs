import test from 'node:test';
import assert from 'node:assert/strict';
import { defaultPolicy, evaluateCandidate, validatePolicy, wilsonLowerBound } from '../src/index.mjs';

test('policy weights are explicit and valid', () => {
  const policy = defaultPolicy();
  assert.equal(policy.price.rawWeight + policy.price.discountWeight, 1);
  assert.equal(policy.performance.reliabilityWeight + policy.performance.ttftWeight + policy.performance.durationWeight + policy.performance.throughputWeight + policy.performance.priceWeight + policy.performance.availabilityWeight, 1);
  assert.equal(validatePolicy(policy).policyVersion, '0.1.0');
});

test('a public route below breadth threshold is not qualified', () => {
  const result = evaluateCandidate({
    id: 'single',
    providerCount: 1,
    routeCount: 1,
    price: { inputPerMillion: 0.1, outputPerMillion: 0.1 },
    runtime: {
      public: {
        evidenceState: 'measured',
        eligibleAttempts: 100,
        reliabilityLcb: 0.99,
        timeoutRate: 0,
        ttftP95Ms: 1,
        durationP95Ms: 1,
        throughputTokensPerSecond: 100
      }
    }
  }, defaultPolicy(), 'public');
  assert.notEqual(result.status, 'qualified');
  assert.ok(result.reasons.includes('insufficient_provider_breadth') || result.reasons.includes('low_availability'));
});

test('confidence bound is conservative at low sample counts', () => {
  assert.ok(wilsonLowerBound(1, 1) < 1);
  assert.ok(wilsonLowerBound(100, 100) > wilsonLowerBound(1, 1));
});
