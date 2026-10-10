import test from 'node:test';
import assert from 'node:assert/strict';
import {
  asymmetricPriceUtility,
  buildAvailabilityDistribution,
  defaultPolicy,
  evaluateCandidate,
  prepareCandidate,
  quantile,
  rankPriceSupplyCandidates,
  summarizeObservations,
  validatePolicy,
  weightPriceLadder,
  wilsonLowerBound
} from '../src/index.mjs';

const policy = defaultPolicy();

test('quantile handles empty, single, two-element and out-of-range inputs', () => {
  assert.equal(quantile([], 0.5), null);
  assert.equal(quantile([7], 0), 7);
  assert.equal(quantile([7], 0.5), 7);
  assert.equal(quantile([7], 1), 7);
  assert.equal(quantile([2, 4], 0), 2);
  assert.equal(quantile([2, 4], 1), 4);
  assert.equal(quantile([2, 4], 0.5), 3);
  assert.equal(quantile([2, 4], -1), 2);
  assert.equal(quantile([2, 4], 2), 4);
  assert.throws(() => quantile([1, 2], NaN), TypeError);
});

test('malformed ladder points are dropped before distribution building', () => {
  const distribution = buildAvailabilityDistribution([[[0.01, 2], [NaN, 5], [0.09, -3]]]);
  assert.equal(distribution.pointCount, 1);
  const empty = weightPriceLadder([[NaN, 1]], buildAvailabilityDistribution([]), policy);
  assert.equal(empty.effectivePrice, null);
  assert.equal(empty.totalWeight, 0);
});

test('asymmetric price utility stays within its bounds at the corners', () => {
  for (const cost of [0, NaN, -1]) {
    assert.deepEqual(asymmetricPriceUtility(cost, policy), { score: 0.01, regime: 'unpriced' });
  }
  const infinite = asymmetricPriceUtility(Infinity, policy);
  assert.equal(infinite.regime, 'priced');
  assert.equal(infinite.score, 0.01);
  for (const cost of [0, 0.05, 0.1, 1, 100, Infinity]) {
    const { score } = asymmetricPriceUtility(cost, policy);
    assert.ok(score >= 0.01 && score <= 1);
  }
});

test('the Wilson lower bound is zero with no trials and never reaches one', () => {
  assert.equal(wilsonLowerBound(0, 0), 0);
  assert.ok(wilsonLowerBound(100, 100) < 1);
  assert.ok(wilsonLowerBound(1, 1) < 1);
  assert.throws(() => wilsonLowerBound(3, 2), RangeError);
});

test('an empty observation set yields a well-formed empty summary', () => {
  const summary = summarizeObservations([]);
  assert.equal(summary.attempts, 0);
  assert.equal(summary.successRate, null);
  assert.equal(summary.timeoutRate, null);
  assert.equal(summary.ttftP95Ms, null);
  assert.equal(summary.durationP95Ms, null);
  assert.equal(summary.evidenceState, 'insufficient_evidence');
  const prepared = prepareCandidate({});
  assert.equal(prepared.runtime.public.attempts, 0);
  assert.equal(prepared.runtime.private.attempts, 0);
});

test('fully identical candidates rank deterministically across reruns', () => {
  const route = (id) => ({
    id,
    providerCount: 3,
    routeCount: 6,
    price: { inputPerMillion: 0.2, outputPerMillion: 0.8 },
    runtime: { public: { evidenceState: 'measured', eligibleAttempts: 100, reliabilityLcb: 0.98, timeoutRate: 0.01, ttftP95Ms: 1000, durationP95Ms: 8000, throughputTokensPerSecond: 80 } }
  });
  const left = [route('same'), route('same')];
  const first = evaluateCandidate(left[0], policy, 'public');
  const second = evaluateCandidate(left[1], policy, 'public');
  assert.deepEqual(first, second);
});

test('supply ranking breaks ties by ascending id and is rerun-stable', () => {
  const ladder = { inputLadder: [[0.01, 5], [0.09, 50]], outputLadder: [[0.04, 5], [0.36, 50]] };
  const candidates = [{ id: 'zulu', price: ladder }, { id: 'alpha', price: ladder }];
  const first = rankPriceSupplyCandidates(candidates, policy);
  const second = rankPriceSupplyCandidates([...candidates].reverse(), policy);
  assert.deepEqual(first.map((row) => row.id), ['alpha', 'zulu']);
  assert.deepEqual(first.map((row) => row.id), second.map((row) => row.id));
});

test('invalid policies throw with their documented messages', () => {
  assert.throws(() => validatePolicy({ price: { rawWeight: 0.5 } }), /price weights must sum to 1/);
  assert.throws(() => validatePolicy({ performance: { reliabilityWeight: 0.5 } }), /performance weights must sum to 1/);
  assert.throws(() => validatePolicy({ price: { rawWeight: NaN, discountWeight: 0.1 } }), TypeError);
  assert.throws(() => validatePolicy({ availability: { minimumProviders: 0 } }), /minimumProviders must be at least 1/);
});

test('malformed price shapes are unavailable rather than fatal', () => {
  const base = {
    id: 'route',
    providerCount: 3,
    routeCount: 6,
    runtime: { public: { evidenceState: 'measured', eligibleAttempts: 100, reliabilityLcb: 0.98, timeoutRate: 0.01, ttftP95Ms: 1000, durationP95Ms: 8000, throughputTokensPerSecond: 80 } }
  };
  const nan = evaluateCandidate({ ...base, price: { inputPerMillion: NaN, outputPerMillion: 0.8 } }, policy, 'public');
  assert.equal(nan.price.available, false);
  assert.equal(nan.price.rawUnits, null);
  assert.ok(nan.score >= 0 && nan.score <= 1);
  const missing = evaluateCandidate(base, policy, 'public');
  assert.equal(missing.price.available, false);
  assert.equal(missing.status, 'unknown');
  assert.ok(missing.reasons.includes('missing_price'));
});
