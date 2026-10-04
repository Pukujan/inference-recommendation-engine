#!/usr/bin/env node
// IRE first-run doctor (#72). Run: pnpm ire:doctor   (add --json for agents)
//
// Tells a newcomer with their own InferHub account whether IRE works for them and what to run
// next. It never prints the key, never sends it anywhere, and makes only GET requests to public
// URLs: InferHub's public status page and IRE's daily feed.
import { pathToFileURL } from 'node:url';

export const FEED_URL =
  'https://raw.githubusercontent.com/Pukujan/inference-recommendation-engine/data/ire-feed/feed/v1/today.json';
export const STATUS_URL = 'https://inferhub.dev/api/status';
export const KEY_VARS = ['INFERHUB_API_KEY', 'ANTHROPIC_API_KEY', 'OPENAI_API_KEY'];
const MIN_NODE_MAJOR = 20;

function check(id, status, summary, next = null, detail = {}) {
  return { id, status, summary, next, ...detail };
}

export function checkNode(version = process.versions.node) {
  const major = Number(String(version).split('.')[0]);
  return major >= MIN_NODE_MAJOR
    ? check('node', 'pass', `Node ${version}`)
    : check('node', 'fail', `Node ${version} is older than ${MIN_NODE_MAJOR}`, `Install Node ${MIN_NODE_MAJOR} or newer.`);
}

// Presence only. The value is read into a boolean and dropped; it is never logged or returned.
export function checkKey(env = process.env) {
  const own = typeof env.INFERHUB_API_KEY === 'string' && env.INFERHUB_API_KEY.trim().length > 0;
  if (own) {
    const clean = !/\s/.test(env.INFERHUB_API_KEY.trim());
    return clean
      ? check('key', 'pass', 'INFERHUB_API_KEY is set (value not shown)')
      : check('key', 'warn', 'INFERHUB_API_KEY is set but contains whitespace (value not shown)', 'Re-copy the key from your InferHub dashboard; it should be one token with no spaces.');
  }
  const other = KEY_VARS.slice(1).filter((name) => typeof env[name] === 'string' && env[name].trim());
  return check(
    'key',
    'warn',
    other.length ? `INFERHUB_API_KEY is not set (found ${other.join(', ')}, values not shown)` : 'INFERHUB_API_KEY is not set',
    'Create a key at https://inferhub.dev, then set it in your shell: export INFERHUB_API_KEY=... (PowerShell: $env:INFERHUB_API_KEY = "...")',
    { optional_for_reading_picks: true },
  );
}

async function getJson(fetchImpl, url, timeoutMs) {
  const res = await fetchImpl(url, {
    method: 'GET',
    headers: { Accept: 'application/json', 'User-Agent': 'ire-doctor/1 (GET-only)' },
    signal: AbortSignal.timeout(timeoutMs),
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

export async function checkStatus(fetchImpl = fetch, timeoutMs = 15000) {
  try {
    const body = await getJson(fetchImpl, STATUS_URL, timeoutMs);
    const rails = Array.isArray(body?.families) ? body.families : [];
    const up = rails.filter((r) => ['operational', 'available'].includes(String(r?.state).toLowerCase())).length;
    return check('inferhub_status', 'pass', `InferHub public status reachable: ${up}/${rails.length} route families operational`);
  } catch (error) {
    return check('inferhub_status', 'fail', `Could not reach ${STATUS_URL} (${error.message})`, 'Check your internet connection or proxy, then run the doctor again.');
  }
}

export async function checkFeed(fetchImpl = fetch, now = new Date(), timeoutMs = 15000) {
  let doc;
  try {
    doc = await getJson(fetchImpl, FEED_URL, timeoutMs);
  } catch (error) {
    return check('feed', 'fail', `Could not fetch today's picks (${error.message})`, `Open ${FEED_URL} in a browser; if it fails there too, the daily feed has not been published.`);
  }
  if (doc?.schema_version !== 'ire-feed/v1') {
    return check('feed', 'fail', `Unexpected feed schema ${doc?.schema_version}`, 'Update your IRE checkout (git pull) and run the doctor again.');
  }
  const pick = (tier) => (doc.tiers?.[tier]?.entries ?? []).find((e) => e.recommended) ?? null;
  const cheap = pick('cheap');
  const frontier = pick('frontier');
  const generated = new Date(doc.generated_at);
  const ageHours = Math.round(((now - generated) / 36e5) * 10) / 10;
  const stale = now > new Date(doc.stale_after);
  const picks = {
    cheap: cheap && { model_family: cheap.model_family, best_route: cheap.best_route, price_usd_per_mtok: cheap.price_usd_per_mtok },
    frontier: frontier && { model_family: frontier.model_family, best_route: frontier.best_route, price_usd_per_mtok: frontier.price_usd_per_mtok },
  };
  const detail = { day_et: doc.day_et, generated_at: doc.generated_at, stale_after: doc.stale_after, age_hours: ageHours, stale, picks };
  if (stale) {
    return check('feed', 'warn', `Today's picks are stale: generated ${ageHours} h ago (stale after ${doc.stale_after})`, 'You can still use them, but check the route price on InferHub before a long run.', detail);
  }
  return check('feed', 'pass', `Today's picks for ${doc.day_et} are fresh (${ageHours} h old)`, null, detail);
}

export function nextSteps(results) {
  const by = Object.fromEntries(results.map((r) => [r.id, r]));
  const steps = results.filter((r) => r.next && r.status !== 'pass').map((r) => r.next);
  const cheap = by.feed?.picks?.cheap;
  if (cheap) {
    const route = cheap.best_route;
    steps.push(
      `Try today's cheap pick (${cheap.model_family}) with curl: curl https://api.inferhub.dev/v1/chat/completions -H "Authorization: Bearer $INFERHUB_API_KEY" -H "Content-Type: application/json" -d '{"model":"${route}","messages":[{"role":"user","content":"hi"}]}'`,
      `Or in Claude Code: ANTHROPIC_BASE_URL=https://api.inferhub.dev ANTHROPIC_API_KEY=$INFERHUB_API_KEY ANTHROPIC_MODEL=${route} claude`,
    );
  }
  steps.push('Read START-HERE.md (people) or docs/AGENT-QUICKSTART.md (agents) for the rest.');
  return steps;
}

export async function runDoctor({ env = process.env, fetchImpl = fetch, now = new Date(), nodeVersion = process.versions.node } = {}) {
  const results = [checkNode(nodeVersion), checkKey(env), await checkStatus(fetchImpl), await checkFeed(fetchImpl, now)];
  const ok = results.every((r) => r.status !== 'fail');
  return { schema_version: 'ire-doctor/v1', ok, checked_at: now.toISOString(), results, next_steps: nextSteps(results) };
}

export function render(report) {
  const mark = { pass: 'PASS', warn: 'WARN', fail: 'FAIL' };
  const lines = ['IRE doctor', ''];
  for (const r of report.results) lines.push(`  ${mark[r.status]}  ${r.summary}`);
  const p = report.results.find((r) => r.id === 'feed')?.picks;
  if (p?.cheap || p?.frontier) {
    lines.push('', "Today's picks:");
    for (const tier of ['cheap', 'frontier']) {
      const e = p[tier];
      if (e) lines.push(`  ${tier.padEnd(8)} ${e.model_family} via ${e.best_route} ($${e.price_usd_per_mtok?.input} in / $${e.price_usd_per_mtok?.output} out per 1M tokens)`);
    }
  }
  lines.push('', report.ok ? 'IRE works for you. Next:' : 'Something needs fixing first. Next:');
  report.next_steps.forEach((s, i) => lines.push(`  ${i + 1}. ${s}`));
  return `${lines.join('\n')}\n`;
}

async function main(argv) {
  const report = await runDoctor();
  process.stdout.write(argv.includes('--json') ? `${JSON.stringify(report, null, 2)}\n` : render(report));
  process.exitCode = report.ok ? 0 : 1;
}

if (import.meta.url === pathToFileURL(process.argv[1] ?? '').href) await main(process.argv.slice(2));
