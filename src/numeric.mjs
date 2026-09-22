export function finiteNumber(value, name = 'value') {
  const number = Number(value);
  if (!Number.isFinite(number)) throw new TypeError(`${name} must be finite`);
  return number;
}

export function nonNegative(value, name = 'value') {
  const number = finiteNumber(value, name);
  if (number < 0) throw new RangeError(`${name} must be non-negative`);
  return number;
}

export function clamp(value, low = 0, high = 1) {
  return Math.min(high, Math.max(low, finiteNumber(value)));
}

export function sum(values) {
  return values.reduce((total, value) => total + finiteNumber(value), 0);
}

export function weightedMean(values, weights) {
  if (!Array.isArray(values) || !Array.isArray(weights) || values.length !== weights.length || values.length === 0) {
    throw new RangeError('weightedMean requires equally-sized, non-empty arrays');
  }
  const totalWeight = sum(weights);
  if (totalWeight <= 0) throw new RangeError('weightedMean requires positive weight');
  return values.reduce((total, value, index) => total + finiteNumber(value) * nonNegative(weights[index], 'weight'), 0) / totalWeight;
}

export function weightedGeometricMean(values, weights, epsilon = 1e-12) {
  if (!Array.isArray(values) || !Array.isArray(weights) || values.length !== weights.length || values.length === 0) {
    throw new RangeError('weightedGeometricMean requires equally-sized, non-empty arrays');
  }
  const totalWeight = sum(weights);
  if (totalWeight <= 0) throw new RangeError('weightedGeometricMean requires positive weight');
  const logMean = values.reduce((total, value, index) => {
    const amount = nonNegative(value, 'price');
    return total + Math.log(Math.max(amount, epsilon)) * nonNegative(weights[index], 'weight');
  }, 0) / totalWeight;
  return Math.exp(logMean);
}

export function lowerIsBetter(value, low, high) {
  const amount = nonNegative(value);
  const lower = nonNegative(low);
  const upper = Math.max(lower + Number.EPSILON, nonNegative(high));
  if (amount <= lower) return 1;
  if (amount >= upper) return 0;
  return 1 - ((Math.log1p(amount) - Math.log1p(lower)) / (Math.log1p(upper) - Math.log1p(lower)));
}

export function higherIsBetter(value, low, high) {
  const amount = finiteNumber(value);
  const lower = finiteNumber(low);
  const upper = Math.max(lower + Number.EPSILON, finiteNumber(high));
  if (amount <= lower) return 0;
  if (amount >= upper) return 1;
  return (amount - lower) / (upper - lower);
}

export function wilsonLowerBound(successes, trials, z = 1.96) {
  const wins = nonNegative(successes, 'successes');
  const count = nonNegative(trials, 'trials');
  const confidence = finiteNumber(z, 'z');
  if (wins > count) throw new RangeError('successes cannot exceed trials');
  if (count === 0) return 0;
  const p = wins / count;
  const z2 = confidence * confidence;
  const denominator = 1 + z2 / count;
  const center = p + z2 / (2 * count);
  const spread = confidence * Math.sqrt((p * (1 - p) + z2 / (4 * count)) / count);
  return clamp((center - spread) / denominator);
}

export function quantile(values, probability) {
  if (!Array.isArray(values) || values.length === 0) return null;
  const p = clamp(probability);
  const sorted = values.map((value) => nonNegative(value)).sort((a, b) => a - b);
  const index = (sorted.length - 1) * p;
  const lower = Math.floor(index);
  const upper = Math.ceil(index);
  if (lower === upper) return sorted[lower];
  return sorted[lower] + (sorted[upper] - sorted[lower]) * (index - lower);
}
