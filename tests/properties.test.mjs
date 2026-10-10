import test from 'node:test';
import assert from 'node:assert/strict';
import fc from 'fast-check';
import { availabilityScore, defaultPolicy, evaluateCandidate, quantile, summarizeObservations, validatePolicy, wilsonLowerBound } from '../src/index.mjs';

const policy = defaultPolicy();
const positive = fc.double({ min: 0.0001, max: 1000, noNaN: true, noDefaultInfinity: true });
const nonNegative = fc.double({ min: 0, max: 1000, noNaN: true, noDefaultInfinity: true });
const unit = fc.double({ min: 0, max: 1, noNaN: true, noDefaultInfinity: true });
const providerCount = fc.integer({ min: 0, max: 20 });
// Number(null) and Number('') are 0, so "malformed" must be a non-numeric value.
const malformedPrice = fc.constantFrom(NaN, Infinity, -Infinity, -1, undefined, 'abc');
const observation = fc.record({
  result: fc.constantFrom('success', 'failure', 'timeout', 'cancelled'),
  clientExcluded: fc.boolean(),
  durationMs: fc.option(nonNegative, { nil: undefined }),
  ttftMs: fc.option(nonNegative, { nil: undefined }),
  completionTokens: fc.option(nonNegative, { nil: undefined })
});
const weights = (count) => fc
  .array(fc.double({ min: 0.01, max: 1, noNaN: true, noDefaultInfinity: true }), { minLength: count, maxLength: count })
  .map((drawn) => {
    const total = drawn.reduce((sum, value) => sum + value, 0);
    return drawn.map((value) => value / total);
  });

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

test('a malformed price field makes the price component unavailable, never throws', () => {
  fc.assert(fc.property(malformedPrice, malformedPrice, (first, second) => {
    const result = evaluateCandidate(candidate('malformed', first, second), policy, 'public');
    assert.equal(result.price.available, false);
    assert.equal(result.price.score, 0);
    assert.equal(result.price.rawUnits, null);
  }));
});

test('a zero price is priced, not missing, and is the best raw score', () => {
  fc.assert(fc.property(positive, positive, (input, output) => {
    const free = evaluateCandidate(candidate('free', 0, 0), policy, 'public');
    const paid = evaluateCandidate(candidate('paid', input, output), policy, 'public');
    assert.equal(free.price.available, true);
    assert.ok(free.price.rawUnits > 0);
    assert.ok(free.price.rawScore >= paid.price.rawScore);
  }));
});

test('missing runtime measurements leave performance unavailable but keep the score bounded', () => {
  const fields = ['reliabilityLcb', 'ttftP95Ms', 'durationP95Ms', 'throughputTokensPerSecond'];
  const measured = { evidenceState: 'measured', eligibleAttempts: 100, reliabilityLcb: 0.98, timeoutRate: 0.01, ttftP95Ms: 1000, durationP95Ms: 8000, throughputTokensPerSecond: 80 };
  fc.assert(fc.property(fc.subarray(fields, { minLength: 1, maxLength: fields.length }), (dropped) => {
    const runtime = { ...measured };
    for (const field of dropped) delete runtime[field];
    const result = evaluateCandidate({ ...candidate('partial', 0.2, 0.8), runtime: { public: runtime } }, policy, 'public');
    assert.equal(result.performance.available, false);
    assert.equal(result.performance.score, null);
    assert.ok(result.score >= 0 && result.score <= 1);
  }));
});

test('observation accounting is consistent and empty input is safe', () => {
  fc.assert(fc.property(fc.array(observation, { maxLength: 10 }), (rows) => {
    const summary = summarizeObservations(rows, { minimumObservations: 1 });
    assert.equal(summary.successes + summary.failures + summary.timeouts + summary.cancelled, summary.eligibleAttempts);
    assert.equal(summary.attempts - summary.eligibleAttempts, summary.clientExcluded);
    assert.equal(summary.evidenceState, summary.eligibleAttempts >= 1 ? 'measured' : 'insufficient_evidence');
  }));
  const empty = summarizeObservations([], { minimumObservations: 1 });
  assert.equal(empty.attempts, 0);
  assert.equal(empty.successRate, null);
  assert.equal(empty.reliabilityLcb, 0);
});

test('an appended duplicate event identifier is a no-op', () => {
  fc.assert(fc.property(fc.array(observation, { minLength: 1, maxLength: 6 }), (rows) => {
    const identified = rows.map((row, index) => ({ ...row, eventId: `e${index}` }));
    const base = summarizeObservations(identified, { minimumObservations: 1 });
    const withDuplicate = summarizeObservations([...identified, { ...identified[0] }], { minimumObservations: 1 });
    assert.deepEqual(withDuplicate, base);
  }));
});

test('observations without identifiers are never deduplicated', () => {
  fc.assert(fc.property(fc.array(observation, { maxLength: 6 }), (rows) => {
    const summary = summarizeObservations(rows, { minimumObservations: 1 });
    assert.equal(summary.attempts, rows.length);
  }));
});

test('quantile is monotone in the probability and bounded by the sample', () => {
  fc.assert(fc.property(fc.array(nonNegative, { minLength: 1, maxLength: 12 }), unit, unit, (values, p1, p2) => {
    const low = Math.min(p1, p2);
    const high = Math.max(p1, p2);
    assert.ok(quantile(values, low) <= quantile(values, high));
    assert.ok(Math.min(...values) <= quantile(values, p1) && quantile(values, p1) <= Math.max(...values));
  }));
});

test('the Wilson lower bound is bounded and monotone in successes', () => {
  fc.assert(fc.property(fc.integer({ min: 1, max: 200 }), (trials) => {
    let previous = 0;
    for (let wins = 0; wins <= trials; wins += 1) {
      const bound = wilsonLowerBound(wins, trials);
      assert.ok(bound >= 0 && bound <= 1);
      assert.ok(bound >= previous);
      previous = bound;
    }
  }));
  fc.assert(fc.property(fc.integer({ min: 0, max: 50 }), fc.integer({ min: 1, max: 50 }), (trials, extra) => {
    assert.throws(() => wilsonLowerBound(trials + extra, trials), RangeError);
  }));
});

test('a policy with non-negative weights summing to one is accepted and stays deterministic', () => {
  fc.assert(fc.property(weights(6), weights(2), (performanceWeights, priceWeights) => {
    const [reliabilityWeight, ttftWeight, durationWeight, throughputWeight, priceWeight, availabilityWeight] = performanceWeights;
    const [rawWeight, discountWeight] = priceWeights;
    const requested = {
      price: { rawWeight, discountWeight },
      performance: { reliabilityWeight, ttftWeight, durationWeight, throughputWeight, priceWeight, availabilityWeight }
    };
    const merged = validatePolicy(requested);
    assert.ok(merged.performance.reliabilityWeight >= 0);
    const route = candidate('stable', 0.2, 0.8);
    const first = evaluateCandidate(route, requested, 'public');
    const second = evaluateCandidate(route, requested, 'public');
    assert.ok(first.score >= 0 && first.score <= 1);
    assert.deepEqual(first, second);
  }));
});

test('invalid policies throw the documented error types', () => {
  assert.throws(() => validatePolicy({ price: { rawWeight: 0.5 } }), RangeError);
  assert.throws(() => validatePolicy({ performance: { reliabilityWeight: 0.5 } }), RangeError);
  assert.throws(() => validatePolicy({ price: { rawWeight: NaN, discountWeight: 0.1 } }), TypeError);
  assert.throws(() => validatePolicy({ availability: { minimumProviders: 0 } }), RangeError);
});
