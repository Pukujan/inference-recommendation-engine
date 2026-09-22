import { availabilityScore, summarizeObservations } from './metrics.mjs';
import { clamp, higherIsBetter, lowerIsBetter, weightedGeometricMean } from './numeric.mjs';

export const ENGINE_VERSION = '0.1.0';

const DEFAULT_POLICY = {
  policyVersion: '0.1.0',
  price: {
    inputWeight: 0.4,
    outputWeight: 0.6,
    rawWeight: 0.9,
    discountWeight: 0.1,
    anchorUnits: 0.01
  },
  availability: {
    providerCap: 5,
    providerWeight: 0.7,
    routeWeight: 0.3,
    minimumProviders: 2,
    acceptable: 0.55
  },
  performance: {
    reliabilityWeight: 0.3,
    ttftWeight: 0.2,
    durationWeight: 0.15,
    throughputWeight: 0.1,
    priceWeight: 0.15,
    availabilityWeight: 0.1,
    reliabilityLow: 0.8,
    reliabilityHigh: 0.99,
    ttftLowMs: 2000,
    ttftHighMs: 30000,
    durationLowMs: 10000,
    durationHighMs: 120000,
    throughputLow: 5,
    throughputHigh: 50,
    minimumObservations: 30,
    minimumReliabilityLcb: 0.95,
    maximumTimeoutRate: 0.05,
    maximumTtftP95Ms: 60000,
    maximumDurationP95Ms: 120000,
    minimumThroughput: 5,
    allowPrivateWithoutPublic: true
  }
};

function deepMerge(base, override) {
  const result = structuredClone(base);
  for (const [key, value] of Object.entries(override ?? {})) {
    if (value && typeof value === 'object' && !Array.isArray(value) && result[key] && typeof result[key] === 'object') {
      result[key] = deepMerge(result[key], value);
    } else {
      result[key] = value;
    }
  }
  return result;
}

export function defaultPolicy() {
  return structuredClone(DEFAULT_POLICY);
}

export function validatePolicy(policy) {
  const candidate = deepMerge(DEFAULT_POLICY, policy);
  const sections = [candidate.price, candidate.availability, candidate.performance];
  for (const section of sections) {
    for (const [key, value] of Object.entries(section)) {
      if (typeof value === 'number' && !Number.isFinite(value)) throw new TypeError(`policy.${key} must be finite`);
    }
  }
  const priceWeights = candidate.price.rawWeight + candidate.price.discountWeight;
  const performanceWeights = candidate.performance.reliabilityWeight + candidate.performance.ttftWeight + candidate.performance.durationWeight + candidate.performance.throughputWeight + candidate.performance.priceWeight + candidate.performance.availabilityWeight;
  if (Math.abs(priceWeights - 1) > 1e-9) throw new RangeError('price weights must sum to 1');
  if (Math.abs(performanceWeights - 1) > 1e-9) throw new RangeError('performance weights must sum to 1');
  if (candidate.price.inputWeight + candidate.price.outputWeight <= 0) throw new RangeError('price token weights must be positive');
  if (candidate.availability.minimumProviders < 1) throw new RangeError('minimumProviders must be at least 1');
  return candidate;
}

function priceComponent(candidate, policy) {
  const input = Number(candidate?.price?.inputPerMillion);
  const output = Number(candidate?.price?.outputPerMillion);
  if (!Number.isFinite(input) || !Number.isFinite(output) || input < 0 || output < 0) return { available: false, score: 0, rawUnits: null, discountPct: 0 };
  const rawUnits = weightedGeometricMean([input, output], [policy.price.inputWeight, policy.price.outputWeight]);
  const rawScore = 1 / (1 + Math.log1p(rawUnits / Math.max(policy.price.anchorUnits, 1e-12)));
  const discountPct = clamp(Number(candidate.discountPct) || 0, 0, 100);
  const discountScore = discountPct / 100;
  return {
    available: true,
    rawUnits,
    discountPct,
    rawScore,
    discountScore,
    score: policy.price.rawWeight * rawScore + policy.price.discountWeight * discountScore
  };
}

function performanceComponent(runtime, policy) {
  const reliability = Number(runtime?.reliabilityLcb);
  const ttft = Number(runtime?.ttftP95Ms);
  const duration = Number(runtime?.durationP95Ms);
  const throughput = Number(runtime?.throughputTokensPerSecond);
  const hasMetrics = [reliability, ttft, duration, throughput].every(Number.isFinite);
  if (!hasMetrics) return { available: false, score: null, reliability: null, ttft: null, duration: null, throughput: null };
  const components = {
    reliability: higherIsBetter(reliability, policy.performance.reliabilityLow, policy.performance.reliabilityHigh),
    ttft: lowerIsBetter(ttft, policy.performance.ttftLowMs, policy.performance.ttftHighMs),
    duration: lowerIsBetter(duration, policy.performance.durationLowMs, policy.performance.durationHighMs),
    throughput: higherIsBetter(throughput, policy.performance.throughputLow, policy.performance.throughputHigh)
  };
  return {
    available: true,
    ...components,
    reliabilityValue: reliability,
    ttftValue: ttft,
    durationValue: duration,
    throughputValue: throughput,
    score: policy.performance.reliabilityWeight * components.reliability + policy.performance.ttftWeight * components.ttft + policy.performance.durationWeight * components.duration + policy.performance.throughputWeight * components.throughput
  };
}

function gate(runtime, candidate, policy, channel, availability) {
  const reasons = [];
  if (!candidate?.price) reasons.push('missing_price');
  if (channel === 'public' && availability.score < policy.availability.acceptable) reasons.push('low_availability');
  if (channel === 'public' && Number(candidate?.providerCount ?? 0) < policy.availability.minimumProviders) reasons.push('insufficient_provider_breadth');
  if (!runtime) {
    reasons.push('missing_runtime_evidence');
    return reasons;
  }
  if (runtime.evidenceState !== 'measured') reasons.push('insufficient_runtime_evidence');
  if (Number.isFinite(runtime.reliabilityLcb) && runtime.reliabilityLcb < policy.performance.minimumReliabilityLcb) reasons.push('reliability_below_gate');
  if (Number.isFinite(runtime.timeoutRate) && runtime.timeoutRate > policy.performance.maximumTimeoutRate) reasons.push('timeout_rate_above_gate');
  if (Number.isFinite(runtime.ttftP95Ms) && runtime.ttftP95Ms > policy.performance.maximumTtftP95Ms) reasons.push('ttft_above_gate');
  if (Number.isFinite(runtime.durationP95Ms) && runtime.durationP95Ms > policy.performance.maximumDurationP95Ms) reasons.push('duration_above_gate');
  if (Number.isFinite(runtime.throughputTokensPerSecond) && runtime.throughputTokensPerSecond < policy.performance.minimumThroughput) reasons.push('throughput_below_gate');
  return reasons;
}

export function evaluateCandidate(input, requestedPolicy = {}, channel = 'public') {
  if (!['public', 'private'].includes(channel)) throw new RangeError('channel must be public or private');
  const policy = validatePolicy(requestedPolicy);
  const availability = availabilityScore({ ...input, ...policy.availability });
  const price = priceComponent(input, policy);
  const runtime = input?.runtime?.[channel] ?? input?.runtime;
  const performance = performanceComponent(runtime, policy);
  const base = performance.available ? performance.score : 0;
  const combined = performance.available
    ? base + policy.performance.priceWeight * price.score + policy.performance.availabilityWeight * availability.score
    : policy.performance.priceWeight * price.score + policy.performance.availabilityWeight * availability.score;
  const reasons = gate(runtime, input, policy, channel, availability);
  const status = reasons.length === 0 ? 'qualified' : (price.available && (runtime || channel === 'public') ? 'provisional' : 'unknown');
  const effectiveScore = clamp(combined);
  return {
    id: String(input?.id ?? ''),
    channel,
    policyVersion: policy.policyVersion,
    status,
    reasons,
    score: effectiveScore,
    price,
    availability,
    performance,
    evidence: runtime ? { state: runtime.evidenceState ?? 'observed', attempts: runtime.eligibleAttempts ?? null } : { state: 'missing', attempts: 0 }
  };
}

export function rankCandidates(candidates, requestedPolicy = {}, channel = 'public') {
  const evaluated = candidates.map((candidate) => evaluateCandidate(candidate, requestedPolicy, channel));
  return evaluated.sort((left, right) => right.score - left.score || (left.price.rawUnits ?? Infinity) - (right.price.rawUnits ?? Infinity) || left.id.localeCompare(right.id)).map((item, index) => ({ rank: index + 1, ...item }));
}

export function prepareCandidate(input, options = {}) {
  const privateSummary = summarizeObservations(input.privateObservations ?? [], options);
  const publicSummary = summarizeObservations(input.publicObservations ?? [], options);
  return {
    ...input,
    runtime: { private: privateSummary, public: publicSummary }
  };
}
