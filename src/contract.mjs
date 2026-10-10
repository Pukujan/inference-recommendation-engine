// Versioned adapter input contracts. An adapter maps a provider's own response
// into these records; provider clients and credentials stay outside this core.
export const ROUTE_CONTRACT_VERSION = 'ire-adapter-route/v1';
export const OBSERVATION_CONTRACT_VERSION = 'ire-adapter-observation/v1';
export const RESULT_CLASSES = Object.freeze(['success', 'failure', 'timeout', 'cancelled']);

export const ROUTE_REQUIRED_FIELDS = Object.freeze([
  'schemaVersion',
  'id',
  'currency',
  'providerCount',
  'routeCount',
  'price'
]);

export const OBSERVATION_REQUIRED_FIELDS = Object.freeze([
  'schemaVersion',
  'eventId',
  'result',
  'clientExcluded',
  'validAt',
  'knownAt'
]);

export const OBSERVATION_FIELDS = Object.freeze([
  ...OBSERVATION_REQUIRED_FIELDS,
  'ttftMs',
  'durationMs',
  'routingMs',
  'completionTokens',
  'costUnits'
]);

const OBSERVATION_MEASUREMENTS = Object.freeze([
  'ttftMs',
  'durationMs',
  'routingMs',
  'completionTokens',
  'costUnits'
]);

const TIMESTAMP_PATTERN = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$/;

function isPlainObject(value) {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function checkTimestamp(value, path, errors) {
  if (typeof value !== 'string' || !TIMESTAMP_PATTERN.test(value) || !Number.isFinite(Date.parse(value))) {
    errors.push(`${path} must be an RFC 3339 timestamp`);
  }
}

// A record at a different major is a forward-compatibility case, so it reports the
// version drift rather than a generic mismatch.
function checkContractVersion(value, expected, errors) {
  if (value === expected) return;
  const family = expected.slice(0, expected.lastIndexOf('/') + 1);
  if (typeof value === 'string' && value.startsWith(family)) {
    errors.push(`unknown contract major: ${value} (expected ${expected})`);
    return;
  }
  errors.push(`schemaVersion must be ${expected}`);
}

function checkNonNegativeNumber(value, path, errors) {
  const number = Number(value);
  if (!Number.isFinite(number) || number < 0) errors.push(`${path} must be a non-negative finite number`);
}

function checkNonNegativeInteger(value, path, errors) {
  if (!Number.isInteger(value) || value < 0) errors.push(`${path} must be a non-negative integer`);
}

// Both bounds are required together: a record that knows when it was true must also
// know when the ledger learned it, per the bitemporal invariant.
function checkBitemporal(record, errors) {
  const hasValid = 'validAt' in record;
  const hasKnown = 'knownAt' in record;
  if (hasValid !== hasKnown) {
    errors.push('validAt and knownAt must be provided together');
    return;
  }
  if (hasValid) {
    checkTimestamp(record.validAt, 'validAt', errors);
    checkTimestamp(record.knownAt, 'knownAt', errors);
  }
}

function checkPrice(price, errors) {
  if (!isPlainObject(price)) {
    errors.push('price must be an object');
    return;
  }
  for (const field of ['inputPerMillion', 'outputPerMillion']) {
    if (!(field in price)) errors.push(`price.${field} is required`);
    else checkNonNegativeNumber(price[field], `price.${field}`, errors);
  }
}

function checkSourceRevision(revision, errors) {
  if (!isPlainObject(revision)) {
    errors.push('sourceRevision must be an object');
    return;
  }
  for (const field of ['sourceId', 'revision']) {
    if (typeof revision[field] !== 'string' || revision[field].length === 0) {
      errors.push(`sourceRevision.${field} must be a non-empty string`);
    }
  }
  checkTimestamp(revision.retrievedAt, 'sourceRevision.retrievedAt', errors);
}

export function validateRouteRecord(record) {
  if (!isPlainObject(record)) return { valid: false, errors: ['route must be an object'] };
  const errors = [];
  for (const field of ROUTE_REQUIRED_FIELDS) {
    if (!(field in record)) errors.push(`missing required field: ${field}`);
  }
  if ('schemaVersion' in record) checkContractVersion(record.schemaVersion, ROUTE_CONTRACT_VERSION, errors);
  if ('id' in record && (typeof record.id !== 'string' || record.id.length === 0)) errors.push('id must be a non-empty string');
  if ('currency' in record && record.currency !== 'USD') errors.push('currency must be USD');
  if ('providerCount' in record) checkNonNegativeInteger(record.providerCount, 'providerCount', errors);
  if ('routeCount' in record) checkNonNegativeInteger(record.routeCount, 'routeCount', errors);
  if ('price' in record) checkPrice(record.price, errors);
  checkBitemporal(record, errors);
  if ('sourceRevision' in record) checkSourceRevision(record.sourceRevision, errors);
  return { valid: errors.length === 0, errors };
}

export function validateObservationRecord(record) {
  if (!isPlainObject(record)) return { valid: false, errors: ['observation must be an object'] };
  const errors = [];
  for (const field of OBSERVATION_REQUIRED_FIELDS) {
    if (!(field in record)) errors.push(`missing required field: ${field}`);
  }
  for (const field of Object.keys(record)) {
    if (!OBSERVATION_FIELDS.includes(field)) errors.push(`unknown observation field: ${field}`);
  }
  if ('schemaVersion' in record) checkContractVersion(record.schemaVersion, OBSERVATION_CONTRACT_VERSION, errors);
  if ('eventId' in record && (typeof record.eventId !== 'string' || record.eventId.length === 0)) errors.push('eventId must be a non-empty string');
  if ('result' in record && !RESULT_CLASSES.includes(record.result)) errors.push(`result must be one of ${RESULT_CLASSES.join(', ')}`);
  if ('clientExcluded' in record && typeof record.clientExcluded !== 'boolean') errors.push('clientExcluded must be a boolean');
  checkBitemporal(record, errors);
  for (const field of OBSERVATION_MEASUREMENTS) {
    if (field in record) checkNonNegativeNumber(record[field], field, errors);
  }
  return { valid: errors.length === 0, errors };
}

export function validateRouteRecords(records) {
  if (!Array.isArray(records)) return { valid: false, errors: ['routes must be an array'] };
  const errors = [];
  records.forEach((record, index) => {
    for (const error of validateRouteRecord(record).errors) errors.push(`routes[${index}]: ${error}`);
  });
  return { valid: errors.length === 0, errors };
}
