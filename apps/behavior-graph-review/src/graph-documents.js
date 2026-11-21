/** Common DTO checks for graph read adapters; business-rule validation stays upstream. */
export function isRecord(value) {
  return value !== null && typeof value === 'object' && !Array.isArray(value) &&
    (Object.getPrototypeOf(value) === Object.prototype || Object.getPrototypeOf(value) === null);
}

export function requireIdentity(value, label) {
  if (typeof value !== 'string' || value.length === 0) throw new TypeError(`${label} must be a nonempty string.`);
  for (const character of value) {
    const point = character.codePointAt(0);
    if (point >= 0xd800 && point <= 0xdfff) throw new TypeError(`${label} must contain valid Unicode text.`);
  }
  // Identities are opaque. In particular, never trim, normalize, or interpret a slash.
  return value;
}

export function validateGraphSummary(value, label = 'Graph summary') {
  if (!isRecord(value)) throw new TypeError(`${label} must be an object.`);
  for (const key of ['id', 'title', 'revision', 'digest']) {
    if (!Object.hasOwn(value, key)) throw new TypeError(`${label}.${key} is required.`);
  }
  requireIdentity(value.id, `${label}.id`);
  requireIdentity(value.revision, `${label}.revision`);
  if (typeof value.title !== 'string') throw new TypeError(`${label}.title must be a string.`);
  if (value.digest !== null && (typeof value.digest !== 'string' || !value.digest)) {
    throw new TypeError(`${label}.digest must be a nonempty string or null.`);
  }
  return value;
}

export function validateCatalog(value) {
  if (!isRecord(value) || value.schema_version !== 'behavior-graph-catalog/1' || !Array.isArray(value.graphs)) {
    throw new TypeError('The graph catalog must use schema_version "behavior-graph-catalog/1" and contain a graphs array.');
  }
  const ids = new Set();
  for (const summary of value.graphs) {
    validateGraphSummary(summary);
    if (ids.has(summary.id)) throw new TypeError('The graph catalog contains a duplicate graph identity.');
    ids.add(summary.id);
  }
  return value;
}

export function validateSnapshot(value) {
  validateGraphSummary(value, 'Graph snapshot');
  if (!Object.hasOwn(value, 'graph') || (value.graph !== null && !isRecord(value.graph))) {
    throw new TypeError('Graph snapshot.graph must be a graph object or null.');
  }
  if ((value.graph === null) !== (value.digest === null)) {
    throw new TypeError('A graph snapshot must have both graph content and a digest, or neither.');
  }
  return value;
}

export function cloneDocument(value) {
  return structuredClone(value);
}

export class GraphRepositoryError extends Error {
  constructor(message, status) {
    super(message);
    this.name = 'GraphRepositoryError';
    this.status = status;
  }
}
