import test from 'node:test';
import assert from 'node:assert/strict';
import fc from 'fast-check';
import { availabilityScore, defaultPolicy, evaluateCandidate, summarizeObservations } from '../src/index.mjs';

const policy = defaultPolicy();
const positive = fc.double({ min: 0.0001, max: 1000, noNaN: true, noDefaultInfinity: true });
const providerCount = fc.integer({ min: 0, max: 20 });

function candidate(id, inputPrice, outputPrice, providers = 3) {
  return {
    id,
    providerCount: providers,
    routeCount: Math.max(1, providers),
    price: { inputPerMillion: inputPrice, outputPerMillion: outputPrice },
    runtime: {
      public: {
        evidenceState: 'measured',
        eligibleAttempts: 100,
        reliabilityLcb: 0.98,
        timeoutRate: 0.01,
        ttftP95Ms: 1000,
        durationP95Ms: 8000,
        throughputTokensPerSecond: 80
      }
    }
  };
}

test('raw price utility is monotone under positive price scaling', () => {
  fc.assert(fc.property(positive, positive, (input, output) => {
    const lower = evaluateCandidate(candidate('lower', input, output), policy, 'public');
    const higher = evaluateCandidate(candidate('higher', input * 2, output * 2), policy, 'public');
    assert.ok(lower.price.rawScore >= higher.price.rawScore);
  }));
});

test('availability is monotone when an eligible provider is added', () => {
  fc.assert(fc.property(providerCount, (count) => {
    const before = availabilityScore({ providerCount: count, routeCount: count, ...policy.availability }).score;
    const after = availabilityScore({ providerCount: count + 1, routeCount: count + 1, ...policy.availability }).score;
    assert.ok(after >= before);
  }));
});

test('scores remain bounded and deterministic', () => {
  fc.assert(fc.property(positive, positive, providerCount, (input, output, providers) => {
    const route = candidate('stable', input, output, providers);
    const first = evaluateCandidate(route, policy, 'public');
    const second = evaluateCandidate(route, policy, 'public');
    assert.ok(first.score >= 0 && first.score <= 1);
    assert.deepEqual(first, second);
  }));
});

test('duplicate event identifiers are counted once', () => {
  const one = { eventId: 'same', result: 'success', durationMs: 100, ttftMs: 20, completionTokens: 10 };
  const summary = summarizeObservations([one, { ...one, durationMs: 900 }], { minimumObservations: 1 });
  assert.equal(summary.attempts, 1);
  assert.equal(summary.successes, 1);
});
