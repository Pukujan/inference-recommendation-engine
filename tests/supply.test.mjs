import test from 'node:test';
import assert from 'node:assert/strict';
import {
  asymmetricPriceUtility,
  buildAvailabilityDistribution,
  evaluatePriceSupply,
  rankPriceSupplyCandidates,
  weightPriceLadder
} from '../src/index.mjs';

const policy = { nearFreeCostCapPerMillion: 0.1 };

test('availability weighting is empirical rather than a fixed count cutoff', () => {
  const distribution = buildAvailabilityDistribution([
    [[0.01, 2], [0.09, 50]],
    [[0.01, 200], [0.09, 400]]
  ]);
  const result = weightPriceLadder([[0.01, 2], [0.09, 50]], distribution, policy);
  assert.ok(result.effectivePrice > 0.01);
  assert.equal(result.profiles.length, 2);
});

test('the full 0.0x band receives near-free utility', () => {
  const result = asymmetricPriceUtility(0.09, policy);
  assert.equal(result.regime, 'near_free');
  assert.ok(result.score >= 0.95 && result.score <= 1);
});

test('a strongly supplied higher ask can move effective cost upward', () => {
  const sparse = evaluatePriceSupply({ id: 'sparse', price: {
    inputLadder: [[0.01, 1], [0.09, 1]],
    outputLadder: [[0.04, 1], [0.36, 1]]
  } }, null, policy);
  const supplied = evaluatePriceSupply({ id: 'supplied', price: {
    inputLadder: [[0.01, 1], [0.09, 500]],
    outputLadder: [[0.04, 1], [0.36, 500]]
  } }, null, policy);
  assert.ok(supplied.effectiveCost > sparse.effectiveCost);
});

test('pool ranking is deterministic and preserves raw ladder points in profiles', () => {
  const candidates = [
    { id: 'a', price: { inputLadder: [[0.01, 3], [0.09, 300]], outputLadder: [[0.04, 3], [0.36, 300]] } },
    { id: 'b', price: { inputLadder: [[0.02, 20], [0.1, 20]], outputLadder: [[0.08, 20], [0.4, 20]] } }
  ];
  const first = rankPriceSupplyCandidates(candidates, policy);
  const second = rankPriceSupplyCandidates([...candidates].reverse(), policy);
  assert.deepEqual(first.map((row) => row.id), second.map((row) => row.id));
  assert.equal(first[0].input.profiles.length, 2);
});
