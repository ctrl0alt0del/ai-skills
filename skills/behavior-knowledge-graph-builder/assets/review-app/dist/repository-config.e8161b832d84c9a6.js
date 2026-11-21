import { createHttpRepositories } from './http-repositories.e5d784d0458d4dfd.js';
import { createJsonGraphRepository } from './json-graph-repository.2500d765b1f5e745.js';
import { createMemoryGraphRepository } from './memory-graph-repository.fe950b476722fea9.js';
import { isRecord } from './graph-documents.41133a2b415dffee.js';
import { resolveRepositoryUrl } from './repository-urls.1b1e92b4e9240ca8.js';

export const CLIENT_CONFIG_SCHEMA = 'behavior-review-client-config/1';

function keys(value, required, optional, label) {
  if (!isRecord(value)) throw new TypeError(`${label} must be an object.`);
  for (const key of required) {
    if (!Object.hasOwn(value, key)) throw new TypeError(`${label}.${key} is required.`);
  }
  const allowed = new Set([...required, ...optional]);
  if (Reflect.ownKeys(value).some(key => !allowed.has(key))) throw new TypeError(`${label} contains an unknown property.`);
}

function validName(name) {
  return typeof name === 'string' && /^[a-zA-Z][a-zA-Z0-9_-]{0,63}$/.test(name) &&
    name !== 'prototype' && !Object.hasOwn(Object.prototype, name);
}

/** Named instances allow graph reads and review writes to use independent services. */
export function createConfiguredRepositories(config, { configUrl = globalThis.location?.href, fetchImpl = globalThis.fetch, cryptoImpl = globalThis.crypto } = {}) {
  keys(config, ['schema_version', 'repositories', 'bindings'], [], 'Client configuration');
  if (config.schema_version !== CLIENT_CONFIG_SCHEMA) throw new TypeError(`Client configuration schema_version must be "${CLIENT_CONFIG_SCHEMA}".`);
  if (!isRecord(config.repositories)) throw new TypeError('Client configuration.repositories must be an object of named repositories.');
  keys(config.bindings, ['graphs', 'reviews'], [], 'Client configuration.bindings');
  let absoluteConfigUrl;
  try { absoluteConfigUrl = resolveRepositoryUrl(configUrl instanceof URL ? configUrl.href : configUrl, undefined, 'The config.json URL'); }
  catch { throw new TypeError('An absolute HTTP(S) configUrl is required to resolve repository locations.'); }

  const definitions = new Map();
  for (const name of Reflect.ownKeys(config.repositories)) {
    if (!validName(name)) throw new TypeError('Repository names must start with a letter, contain only letters, digits, underscores or hyphens, and avoid reserved object names.');
    const definition = config.repositories[name];
    const label = `Repository "${name}"`;
    if (!isRecord(definition) || !Object.hasOwn(definition, 'type')) throw new TypeError(`${label}.type is required.`);
    if (definition.type === 'http') {
      keys(definition, ['type'], ['base_url'], label);
      definitions.set(name, { type: 'http', baseUrl: resolveRepositoryUrl(Object.hasOwn(definition, 'base_url') ? definition.base_url : '', absoluteConfigUrl, `${label}.base_url`) });
    } else if (definition.type === 'json') {
      keys(definition, ['type', 'url'], [], label);
      if (!definition.url) throw new TypeError(`${label}.url must identify a graph catalog.`);
      definitions.set(name, { type: 'json', url: resolveRepositoryUrl(definition.url, absoluteConfigUrl, `${label}.url`) });
    } else if (definition.type === 'memory') {
      keys(definition, ['type', 'catalog', 'snapshots'], [], label);
      definitions.set(name, { type: 'memory', catalog: definition.catalog, snapshots: definition.snapshots });
    } else {
      throw new TypeError(`${label}.type must be "http", "json", or "memory".`);
    }
  }
  for (const role of ['graphs', 'reviews']) {
    const name = config.bindings[role];
    if (!validName(name) || !definitions.has(name)) throw new TypeError(`The ${role} binding must name a configured repository.`);
    if (role === 'reviews' && definitions.get(name).type !== 'http') {
      throw new TypeError(`Repository "${name}" cannot store reviews; JSON and memory repositories support graph reads only.`);
    }
  }
  const instances = new Map();
  for (const [name, definition] of definitions) {
    if (definition.type === 'http') instances.set(name, createHttpRepositories({ baseUrl: definition.baseUrl, fetchImpl }));
    else if (definition.type === 'json') instances.set(name, { graphRepository: createJsonGraphRepository({ url: definition.url, fetchImpl, cryptoImpl }) });
    else instances.set(name, { graphRepository: createMemoryGraphRepository(definition) });
  }
  return {
    graphRepository: instances.get(config.bindings.graphs).graphRepository,
    reviewRepository: instances.get(config.bindings.reviews).reviewRepository,
  };
}

/** Configuration failure is fatal; never silently switch the user's chosen storage. */
export async function loadConfiguredRepositories({ configUrl, fetchImpl = globalThis.fetch, cryptoImpl = globalThis.crypto } = {}) {
  if (typeof fetchImpl !== 'function') throw new TypeError('A fetch implementation is required to load config.json.');
  const location = resolveRepositoryUrl(configUrl instanceof URL ? configUrl.href : configUrl, undefined, 'The config.json URL');
  const response = await fetchImpl(location, { method: 'GET', cache: 'no-store', credentials: 'same-origin' });
  if (!response.ok) throw new Error(`Cannot load config.json (${response.status}). Check the app configuration and reload.`);
  let config;
  try { config = await response.json(); }
  catch { throw new Error('config.json is not valid JSON. Correct the file and reload.'); }
  return createConfiguredRepositories(config, { configUrl: location, fetchImpl, cryptoImpl });
}
