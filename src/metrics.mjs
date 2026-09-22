import { clamp, nonNegative, quantile, wilsonLowerBound } from './numeric.mjs';

const SERVICE_RESULTS = new Set(['success', 'failure', 'timeout', 'cancelled']);

export function dedupeObservations(observations) {
  const seen = new Set();
  const result = [];
  for (const observation of observations ?? []) {
    const key = observation?.eventId ?? Symbol('anonymous');
    if (typeof key === 'string' && seen.has(key)) continue;
    if (typeof key === 'string') seen.add(key);
    result.push(observation);
  }
  return result;
}

function numericValues(observations, field) {
  return observations
    .map((item) => Number(item?.[field]))
    .filter((value) => Number.isFinite(value) && value >= 0);
}

export function summarizeObservations(input, options = {}) {
  const observations = dedupeObservations(input);
  const eligible = observations.filter((item) => !item?.clientExcluded && SERVICE_RESULTS.has(item?.result));
  const successes = eligible.filter((item) => item.result === 'success').length;
  const failures = eligible.filter((item) => item.result === 'failure').length;
  const timeouts = eligible.filter((item) => item.result === 'timeout').length;
  const cancelled = eligible.filter((item) => item.result === 'cancelled').length;
  const serviceTrials = eligible.length;
  const timeoutRate = serviceTrials === 0 ? null : timeouts / serviceTrials;
  const z = options.z ?? 1.96;
  const successful = eligible.filter((item) => item.result === 'success');
  const p95Duration = quantile(numericValues(successful, 'durationMs'), 0.95);
  const p95Ttft = quantile(numericValues(successful, 'ttftMs'), 0.95);
  const p50Ttft = quantile(numericValues(successful, 'ttftMs'), 0.50);
  const p99Duration = quantile(numericValues(successful, 'durationMs'), 0.99);
  const completionTokens = numericValues(successful, 'completionTokens');
  const durationSeconds = numericValues(successful, 'durationMs').map((value) => value / 1000);
  const totalCompletionTokens = completionTokens.reduce((total, value) => total + value, 0);
  const totalDurationSeconds = durationSeconds.reduce((total, value) => total + value, 0);
  const totalCost = numericValues(successful, 'costUnits').reduce((total, value) => total + value, 0);
  return {
    attempts: observations.length,
    eligibleAttempts: serviceTrials,
    clientExcluded: observations.length - eligible.length,
    successes,
    failures,
    timeouts,
    cancelled,
    successRate: serviceTrials === 0 ? null : successes / serviceTrials,
    reliabilityLcb: wilsonLowerBound(successes, serviceTrials, z),
    timeoutRate,
    ttftP50Ms: p50Ttft,
    ttftP95Ms: p95Ttft,
    durationP95Ms: p95Duration,
    durationP99Ms: p99Duration,
    totalCompletionTokens,
    totalDurationSeconds,
    throughputTokensPerSecond: totalDurationSeconds > 0 ? totalCompletionTokens / totalDurationSeconds : null,
    totalCostUnits: totalCost,
    evidenceState: serviceTrials >= (options.minimumObservations ?? 30) ? 'measured' : 'insufficient_evidence'
  };
}

export function availabilityScore({ providerCount = 0, routeCount = 0, providerCap = 5, providerWeight = 0.7, routeWeight = 0.3 } = {}) {
  const providers = nonNegative(providerCount, 'providerCount');
  const routes = nonNegative(routeCount, 'routeCount');
  const cap = Math.max(1, nonNegative(providerCap, 'providerCap'));
  const providerBreadth = clamp(Math.log1p(providers) / Math.log1p(cap));
  const routeCoverage = routes === 0 ? 0 : clamp(routes / Math.max(routes, cap));
  const total = providerWeight + routeWeight;
  if (total <= 0) throw new RangeError('availability weights must be positive');
  return {
    providerBreadth,
    routeCoverage,
    score: (providerBreadth * providerWeight + routeCoverage * routeWeight) / total
  };
}
