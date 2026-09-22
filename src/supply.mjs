import { clamp, weightedGeometricMean } from './numeric.mjs';

export const DEFAULT_SUPPLY_POLICY = {
  availabilityExponent: 1,
  distributionWeight: 0.5,
  derivativeWeight: 0.25,
  derivativeCap: 3,
  lowerPricePreferenceWeight: 0.5,
  inputWeight: 0.4,
  outputWeight: 0.6,
  nearFreeCostCapPerMillion: 0.1,
  nearFreePriceUtilityFloor: 0.95,
  nonNearFreeLogCostDecay: 1
};

function supplyPolicy(requestedPolicy = {}) {
  return { ...DEFAULT_SUPPLY_POLICY, ...(requestedPolicy?.price?.supply ?? requestedPolicy) };
}

function number(value) {
  const result = Number(value);
  return Number.isFinite(result) ? result : null;
}

function quantile(values, probability) {
  const clean = values.filter(Number.isFinite).slice().sort((left, right) => left - right);
  if (!clean.length) return null;
  const position = (clean.length - 1) * clamp(probability);
  const lower = Math.floor(position), upper = Math.ceil(position);
  return lower === upper ? clean[lower] : clean[lower] + (clean[upper] - clean[lower]) * (position - lower);
}

function percentileRank(sortedValues, value) {
  if (!sortedValues.length || !Number.isFinite(value)) return null;
  let low = 0;
  let high = sortedValues.length;
  while (low < high) {
    const middle = Math.floor((low + high) / 2);
    if (sortedValues[middle] <= value) low = middle + 1;
    else high = middle;
  }
  return low / sortedValues.length;
}

function logAvailability(value) {
  return Math.log1p(Math.max(0, Number(value) || 0));
}

export function normalizePricePoints(points = []) {
  return points
    .map((point) => {
      if (Array.isArray(point)) return { price: number(point[0]), available: number(point[1]) };
      if (Array.isArray(point?.value)) return { price: number(point.value[0]), available: number(point.value[1]) };
      return { price: number(point?.price), available: number(point?.available ?? point?.count) };
    })
    .filter((point) => point.price !== null && point.price > 0 && point.available !== null && point.available >= 0)
    .sort((left, right) => left.price - right.price || right.available - left.available);
}

function ladderDerivatives(points) {
  return points.map((point, index) => {
    if (index === 0) return 0;
    const previous = points[index - 1];
    const deltaPrice = Math.max(Math.log(point.price) - Math.log(previous.price), 1e-9);
    return Math.max(0, (logAvailability(point.available) - logAvailability(previous.available)) / deltaPrice);
  });
}

export function buildAvailabilityDistribution(ladders = []) {
  const logValues = [];
  const derivativeValues = [];
  for (const ladder of ladders) {
    const points = normalizePricePoints(ladder);
    logValues.push(...points.map((point) => logAvailability(point.available)));
    derivativeValues.push(...ladderDerivatives(points).filter((value) => value > 0));
  }
  return {
    pointCount: logValues.length,
    sortedLogs: logValues.slice().sort((left, right) => left - right),
    medianLog: quantile(logValues, 0.5) ?? 0,
    derivativeP90: quantile(derivativeValues, 0.9) ?? 0
  };
}

export function weightPriceLadder(points, distribution = buildAvailabilityDistribution([points]), requestedPolicy = {}) {
  const policy = supplyPolicy(requestedPolicy);
  const normalized = normalizePricePoints(points);
  if (!normalized.length) return { effectivePrice: null, weightedAvailability: null, totalWeight: 0, profiles: [] };
  const medianLog = Math.max(distribution.medianLog ?? 0, 1e-9);
  const derivativeAnchor = Math.max(distribution.derivativeP90 ?? 0, 1e-9);
  const ladderMedianPrice = Math.max(quantile(normalized.map((point) => point.price), 0.5) ?? normalized[0].price, 1e-12);
  const derivatives = ladderDerivatives(normalized);
  const profiles = normalized.map((point, index) => {
    const logCount = logAvailability(point.available);
    const percentile = percentileRank(distribution.sortedLogs ?? [], logCount) ?? 0;
    const relativeSupply = Math.exp(logCount - medianLog);
    const levelWeight = Math.pow(Math.max(relativeSupply, 1e-9), Math.max(0.000001, Number(policy.availabilityExponent)));
    const distributionMultiplier = Math.max(0.05, 1 + Number(policy.distributionWeight) * ((percentile * 2) - 1));
    const derivative = derivatives[index] ?? 0;
    const derivativeScore = Math.min(Number(policy.derivativeCap), derivative / derivativeAnchor);
    const derivativeMultiplier = 1 + Math.max(0, Number(policy.derivativeWeight)) * derivativeScore;
    const lowerPriceMultiplier = Math.pow(Math.max(ladderMedianPrice / point.price, 1e-9), Math.max(0, Number(policy.lowerPricePreferenceWeight)));
    const weight = levelWeight * distributionMultiplier * derivativeMultiplier * lowerPriceMultiplier;
    return { price: point.price, available: point.available, percentile, derivative, derivativeScore, weight };
  });
  const totalWeight = profiles.reduce((sum, profile) => sum + profile.weight, 0);
  return {
    effectivePrice: totalWeight > 0 ? profiles.reduce((sum, profile) => sum + profile.price * profile.weight, 0) / totalWeight : null,
    weightedAvailability: totalWeight > 0 ? profiles.reduce((sum, profile) => sum + profile.available * profile.weight, 0) / totalWeight : null,
    totalWeight,
    profiles
  };
}

export function asymmetricPriceUtility(cost, requestedPolicy = {}) {
  const policy = supplyPolicy(requestedPolicy);
  if (!(cost > 0)) return { score: 0.01, regime: 'unpriced' };
  const cap = Math.max(1e-12, Number(policy.nearFreeCostCapPerMillion));
  const floor = clamp(Number(policy.nearFreePriceUtilityFloor), 0.01, 1);
  if (cost <= cap) return { score: floor + (1 - floor) * (1 - cost / cap), regime: 'near_free' };
  const score = floor * Math.exp(-Math.max(0, Number(policy.nonNearFreeLogCostDecay)) * Math.max(0, Math.log(cost / cap)));
  return { score: clamp(score, 0.01, 1), regime: 'priced' };
}

function ladderFor(candidate, side) {
  return candidate?.price?.[`${side}Ladder`] ?? candidate?.price?.[`${side}PricePoints`] ?? [];
}

export function evaluatePriceSupply(candidate, poolDistribution = null, requestedPolicy = {}) {
  const policy = supplyPolicy(requestedPolicy);
  const inputLadder = normalizePricePoints(ladderFor(candidate, 'input'));
  const outputLadder = normalizePricePoints(ladderFor(candidate, 'output'));
  if (!inputLadder.length || !outputLadder.length) return { available: false, reason: 'missing_price_ladder' };
  const distribution = poolDistribution ?? buildAvailabilityDistribution([inputLadder, outputLadder]);
  const input = weightPriceLadder(inputLadder, distribution.input ?? distribution, policy);
  const output = weightPriceLadder(outputLadder, distribution.output ?? distribution, policy);
  const cost = weightedGeometricMean([input.effectivePrice, output.effectivePrice], [policy.inputWeight, policy.outputWeight]);
  return {
    available: Number.isFinite(cost),
    input,
    output,
    effectiveCost: cost,
    utility: asymmetricPriceUtility(cost, policy)
  };
}

export function buildPriceSupplyPool(candidates = []) {
  return {
    input: buildAvailabilityDistribution(candidates.map((candidate) => ladderFor(candidate, 'input'))),
    output: buildAvailabilityDistribution(candidates.map((candidate) => ladderFor(candidate, 'output')))
  };
}

export function rankPriceSupplyCandidates(candidates = [], requestedPolicy = {}) {
  const pool = buildPriceSupplyPool(candidates);
  return candidates
    .map((candidate) => ({ id: String(candidate.id ?? ''), ...evaluatePriceSupply(candidate, pool, requestedPolicy) }))
    .sort((left, right) => (right.utility?.score ?? 0) - (left.utility?.score ?? 0) || (left.effectiveCost ?? Infinity) - (right.effectiveCost ?? Infinity) || left.id.localeCompare(right.id))
    .map((item, index) => ({ rank: index + 1, ...item }));
}
