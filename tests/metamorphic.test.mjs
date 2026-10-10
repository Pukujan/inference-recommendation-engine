import test from 'node:test';
import assert from 'node:assert/strict';
import { defaultPolicy, prepareCandidate, rankCandidates } from '../src/index.mjs';

const policy = defaultPolicy();

function route(id, input, output, providers = 3) {
  return {
    id,
    providerCount: providers,
    routeCount: providers * 2,
    price: { inputPerMillion: input, outputPerMillion: output },
    discountPct: 5,
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

test('permuting routes preserves the ranking order', () => {
  const routes = [route('a', 0.1, 0.2), route('b', 0.3, 0.4), route('c', 0.2, 0.3)];
  const left = rankCandidates(routes, policy, 'public').map((item) => item.id);
  const right = rankCandidates([...routes].reverse(), policy, 'public').map((item) => item.id);
  assert.deepEqual(left, right);
});

test('adding an eligible provider preserves or increases availability', () => {
  const before = rankCandidates([route('a', 0.2, 0.2, 2)], policy, 'public')[0];
  const after = rankCandidates([route('a', 0.2, 0.2, 4)], policy, 'public')[0];
  assert.ok(after.availability.score >= before.availability.score);
  assert.ok(after.score >= before.score);
});

test('uniform price scaling preserves price ordering', () => {
  const routes = [route('a', 0.1, 0.2), route('b', 0.4, 0.8), route('c', 0.2, 0.3)];
  const original = rankCandidates(routes, policy, 'public').map((item) => item.id);
  const scaled = rankCandidates(routes.map((item) => ({ ...item, price: { inputPerMillion: item.price.inputPerMillion * 10, outputPerMillion: item.price.outputPerMillion * 10 } })), policy, 'public').map((item) => item.id);
  assert.deepEqual(original, scaled);
});

test('unknown runtime evidence cannot improve a route', () => {
  const measured = route('measured', 0.2, 0.2);
  const unknown = { ...route('unknown', 0.2, 0.2), runtime: { public: undefined } };
  const measuredResult = rankCandidates([measured], policy, 'public')[0];
  const unknownResult = rankCandidates([unknown], policy, 'public')[0];
  assert.ok(unknownResult.score <= measuredResult.score);
  assert.notEqual(unknownResult.evidence.state, 'measured');
});

test('discount changes do not alter raw price fields', () => {
  const original = rankCandidates([route('a', 0.2, 0.3)], policy, 'public')[0];
  const changed = rankCandidates([{ ...route('a', 0.2, 0.3), discountPct: 90 }], policy, 'public')[0];
  assert.equal(original.price.rawUnits, changed.price.rawUnits);
  assert.equal(original.price.rawScore, changed.price.rawScore);
  assert.ok(changed.price.discountScore > original.price.discountScore);
});

test('appending a duplicate observation leaves the summary unchanged', () => {
  const base = {
    id: 'duplicate',
    price: { inputPerMillion: 0.2, outputPerMillion: 0.8 },
    publicObservations: [
      { eventId: 'a', result: 'success', durationMs: 100, ttftMs: 20, completionTokens: 10 },
      { eventId: 'b', result: 'failure', durationMs: 300 }
    ]
  };
  const first = prepareCandidate(base, { minimumObservations: 1 });
  const second = prepareCandidate({ ...base, publicObservations: [...base.publicObservations, { ...base.publicObservations[0], durationMs: 999 }] }, { minimumObservations: 1 });
  assert.deepEqual(second.runtime.public, first.runtime.public);
});

test('equal scores fall back to ascending id under any input order', () => {
  const clones = ['delta', 'alpha', 'charlie', 'bravo'].map((id) => route(id, 0.2, 0.8));
  const expected = ['alpha', 'bravo', 'charlie', 'delta'];
  assert.deepEqual(rankCandidates(clones, policy, 'public').map((item) => item.id), expected);
  assert.deepEqual(rankCandidates([...clones].reverse(), policy, 'public').map((item) => item.id), expected);
});

test('duplicating a route id shifts every later rank by exactly one', () => {
  const routes = [route('a', 0.1, 0.2), route('b', 0.2, 0.3), route('c', 0.3, 0.4)];
  const baseline = rankCandidates(routes, policy, 'public');
  const withDuplicate = rankCandidates([routes[0], routes[1], { ...routes[1] }, routes[2]], policy, 'public');
  const rankOf = (rows, id) => rows.filter((row) => row.id === id).map((row) => row.rank);
  assert.deepEqual(rankOf(withDuplicate, 'a'), rankOf(baseline, 'a'));
  assert.deepEqual(rankOf(withDuplicate, 'b'), [rankOf(baseline, 'b')[0], rankOf(baseline, 'b')[0] + 1]);
  assert.deepEqual(rankOf(withDuplicate, 'c'), [rankOf(baseline, 'c')[0] + 1]);
});

test('an unpriced route ranks last at an equal score', () => {
  const priced = route('priced', 0.2, 0.8);
  const unpriced = { ...route('unpriced', 0.2, 0.8), price: { inputPerMillion: 'abc', outputPerMillion: 'abc' } };
  const ranked = rankCandidates([unpriced, priced], policy, 'public');
  assert.equal(ranked[0].id, 'priced');
  assert.equal(ranked[1].id, 'unpriced');
});
