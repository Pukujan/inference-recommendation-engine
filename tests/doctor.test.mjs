import assert from 'node:assert/strict';
import test from 'node:test';
import { FEED_URL, STATUS_URL, checkKey, checkNode, render, runDoctor } from '../scripts/doctor.mjs';

const SECRET = ['s', 'k-', 'doctor-test-value-123456'].join('');
const feed = (over = {}) => ({
  schema_version: 'ire-feed/v1',
  generated_at: '2026-10-04T18:00:00Z',
  stale_after: '2026-10-06T06:00:00Z',
  day_et: '2026-10-04',
  tiers: {
    cheap: { entries: [
      { rank: 1, model_family: 'Gated One', recommended: false, best_route: 'aa/gated', price_usd_per_mtok: { input: 0.001, output: 0.002 } },
      { rank: 2, model_family: 'Cheap Pick', recommended: true, best_route: 'bb/cheap', price_usd_per_mtok: { input: 0.01, output: 0.04 } },
    ] },
    frontier: { entries: [{ rank: 1, model_family: 'Big Pick', recommended: true, best_route: 'cx/big', price_usd_per_mtok: { input: 0.1, output: 0.5 } }] },
  },
  ...over,
});
function fakeFetch(map, calls = []) {
  return async (url, init) => {
    calls.push({ url, init });
    const v = map[url];
    if (v instanceof Error) throw v;
    return { ok: v !== undefined, status: v === undefined ? 404 : 200, json: async () => v };
  };
}
const statusBody = { families: [{ state: 'operational' }, { state: 'degraded' }] };

test('key check reports presence only and never echoes the value', () => {
  const r = checkKey({ INFERHUB_API_KEY: SECRET });
  assert.equal(r.status, 'pass');
  assert.ok(!JSON.stringify(r).includes(SECRET));
  const missing = checkKey({ OPENAI_API_KEY: SECRET });
  assert.equal(missing.status, 'warn');
  assert.match(missing.summary, /OPENAI_API_KEY/);
  assert.ok(!JSON.stringify(missing).includes(SECRET));
});

test('node version gate', () => {
  assert.equal(checkNode('24.1.0').status, 'pass');
  assert.equal(checkNode('18.2.0').status, 'fail');
});

test('healthy run: GET only, no key sent, first recommended picks shown', async () => {
  const calls = [];
  const report = await runDoctor({
    env: { INFERHUB_API_KEY: SECRET },
    fetchImpl: fakeFetch({ [STATUS_URL]: statusBody, [FEED_URL]: feed() }, calls),
    now: new Date('2026-10-04T20:00:00Z'),
    nodeVersion: '24.0.0',
  });
  assert.equal(report.ok, true);
  assert.deepEqual(calls.map((c) => c.url).sort(), [FEED_URL, STATUS_URL].sort());
  for (const c of calls) {
    assert.equal(c.init.method, 'GET');
    assert.ok(!JSON.stringify(c.init.headers).includes(SECRET));
    assert.ok(!('Authorization' in c.init.headers));
  }
  const fd = report.results.find((r) => r.id === 'feed');
  assert.equal(fd.stale, false);
  assert.equal(fd.age_hours, 2);
  assert.equal(fd.picks.cheap.model_family, 'Cheap Pick');
  const text = render(report);
  assert.match(text, /PASS {2}Today's picks for 2026-10-04 are fresh/);
  assert.match(text, /bb\/cheap/);
  assert.ok(!text.includes(SECRET));
  assert.ok(!JSON.stringify(report).includes(SECRET));
});

test('stale feed warns, unreachable status fails, missing key still lists next steps', async () => {
  const report = await runDoctor({
    env: {},
    fetchImpl: fakeFetch({ [STATUS_URL]: new Error('offline'), [FEED_URL]: feed() }),
    now: new Date('2026-10-07T00:00:00Z'),
    nodeVersion: '24.0.0',
  });
  assert.equal(report.ok, false);
  const by = Object.fromEntries(report.results.map((r) => [r.id, r]));
  assert.equal(by.feed.status, 'warn');
  assert.equal(by.feed.stale, true);
  assert.equal(by.inferhub_status.status, 'fail');
  assert.equal(by.key.status, 'warn');
  assert.ok(report.next_steps.some((s) => s.includes('INFERHUB_API_KEY')));
  assert.ok(report.next_steps.at(-1).includes('START-HERE.md'));
});

test('unknown feed schema fails clearly', async () => {
  const report = await runDoctor({
    env: {},
    fetchImpl: fakeFetch({ [STATUS_URL]: statusBody, [FEED_URL]: feed({ schema_version: 'ire-feed/v9' }) }),
    nodeVersion: '24.0.0',
  });
  const fd = report.results.find((r) => r.id === 'feed');
  assert.equal(fd.status, 'fail');
  assert.match(fd.summary, /ire-feed\/v9/);
});
