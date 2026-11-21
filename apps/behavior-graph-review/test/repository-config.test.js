import test from 'node:test';
import assert from 'node:assert/strict';
import { webcrypto } from 'node:crypto';
import { CLIENT_CONFIG_SCHEMA, createConfiguredRepositories, loadConfiguredRepositories } from '../src/repository-config.js';
import { startReviewApp } from '../src/main.js';

const configUrl = 'https://app.example/team/review/config.json';
const config = () => ({ schema_version: CLIENT_CONFIG_SCHEMA, repositories: { local: { type: 'http', base_url: '' } }, bindings: { graphs: 'local', reviews: 'local' } });
const response = (data, status = 200) => ({ ok: status >= 200 && status < 300, status, json: async () => structuredClone(data) });
const emptyMemory = () => ({ type: 'memory', catalog: { schema_version: 'behavior-graph-catalog/1', graphs: [] }, snapshots: [] });

test('configuration independently binds named graph and review HTTP services with complete prefixes', async () => {
  const calls = [];
  const value = config();
  value.repositories = {
    graphs: { type: 'http', base_url: '../graph-service/' },
    reviews: { type: 'http', base_url: 'https://reviews.example/root/v1/' },
  };
  value.bindings = { graphs: 'graphs', reviews: 'reviews' };
  const { graphRepository, reviewRepository } = createConfiguredRepositories(value, {
    configUrl,
    fetchImpl: async (url, options) => { calls.push({ url, options }); return response(url.endsWith('/api/bootstrap') ? { csrf_token: 'token' } : { graphs: [] }); },
  });
  await graphRepository.listGraphs();
  await graphRepository.getGraph('a/b', 'v1?');
  await reviewRepository.getReview('a/b');
  await reviewRepository.createAnnotation('a/b', { text: 'note' }, 3);
  await reviewRepository.deleteAnnotation('a/b', 'n', 4);
  assert.deepEqual(calls.map(call => call.url), [
    'https://app.example/team/graph-service/api/graphs',
    'https://app.example/team/graph-service/api/graphs/a%2Fb?revision=v1%3F',
    'https://reviews.example/root/v1/api/graphs/a%2Fb/review',
    'https://reviews.example/root/v1/api/bootstrap',
    'https://reviews.example/root/v1/api/graphs/a%2Fb/annotations',
    'https://reviews.example/root/v1/api/graphs/a%2Fb/annotations/n',
  ]);
  assert.equal(calls.filter(call => call.url.endsWith('/api/bootstrap')).length, 1);
  assert.equal(calls.at(-1).options.headers['X-Review-Token'], 'token');
});

test('empty HTTP base_url preserves the directory that contains config.json', async () => {
  const calls = [];
  const repositories = createConfiguredRepositories(config(), {
    configUrl, fetchImpl: async url => { calls.push(url); return response({ graphs: [] }); },
  });
  await repositories.graphRepository.listGraphs();
  await repositories.reviewRepository.getReview('a');
  assert.deepEqual(calls, ['https://app.example/team/review/api/graphs', 'https://app.example/team/review/api/graphs/a/review']);
});

test('JSON catalog locations resolve against config.json and memory binds without network reads', async () => {
  const calls = [];
  const value = config();
  value.repositories.staticGraphs = { type: 'json', url: '../published/catalog.json' };
  value.bindings.graphs = 'staticGraphs';
  const { graphRepository } = createConfiguredRepositories(value, { configUrl, cryptoImpl: webcrypto, fetchImpl: async url => {
    calls.push(url); return response({ schema_version: 'behavior-graph-catalog/1', graphs: [] });
  } });
  assert.deepEqual(await graphRepository.listGraphs(), []);
  assert.deepEqual(calls, ['https://app.example/team/published/catalog.json']);
  value.repositories.staticGraphs = emptyMemory();
  const memory = createConfiguredRepositories(value, { configUrl, fetchImpl: async () => { throw new Error('Unexpected network read'); } });
  assert.deepEqual(await memory.graphRepository.listGraphs(), []);
});

test('invalid configuration is rejected without a request or silent fallback', () => {
  const cases = [
    [null, /must be an object/],
    [{ ...config(), schema_version: 'behavior-review-config/1' }, /schema_version/],
    [{ ...config(), unknown: true }, /unknown property/],
    [{ ...config(), bindings: { graphs: 'local', reviews: 'missing' } }, /reviews binding/],
    [{ ...config(), bindings: { graphs: 'local' } }, /reviews is required/],
    [{ ...config(), bindings: { graphs: 'local', reviews: 'local', extra: 'local' } }, /unknown property/],
    [{ ...config(), repositories: { local: { type: 'filesystem', path: './private' } } }, /type must be/],
    [{ ...config(), repositories: { local: { type: 'http', password: 'not-public' } } }, /unknown property/],
    [{ ...config(), repositories: { local: { type: 'http', base_url: null } } }, /URL string/],
    [{ ...config(), repositories: { local: { type: 'http', base_url: 'file:///private' } } }, /HTTP\(S\)/],
    [{ ...config(), repositories: { local: { type: 'http', base_url: 'https://user:secret@example.test' } } }, /credentials/],
    [{ ...config(), repositories: { local: { type: 'http', base_url: '/service?token=secret' } } }, /query/],
    [{ ...config(), repositories: { local: { type: 'http', base_url: '/service?' } } }, /query/],
    [{ ...config(), repositories: { local: { type: 'http', base_url: '/service#' } } }, /fragment/],
    [{ ...config(), repositories: { local: { type: 'http', base_url: 'http://api.example.test' } } }, /HTTPS/],
    [{ ...config(), repositories: { local: { type: 'http', base_url: ' https://api.example.test' } } }, /unsupported URL character/],
    [{ ...config(), repositories: { local: { type: 'http', base_url: 'https://api.example.test/\\path' } } }, /unsupported URL character/],
    [{ ...config(), repositories: { local: { type: 'http', base_url: 'https://api.example.test/<path>' } } }, /unsupported URL character/],
    [{ ...config(), repositories: { local: { type: 'json', url: './catalog.json' } } }, /cannot store reviews/],
    [{ ...config(), repositories: { local: emptyMemory() } }, /cannot store reviews/],
    [{ ...config(), repositories: { local: { type: 'http' }, unused: { type: 'misspelled' } } }, /type must be/],
    [{ ...config(), repositories: { local: { type: 'http' }, unused: { type: 'json', url: '' } } }, /must identify a graph catalog/],
    [{ ...config(), repositories: { local: { type: 'http' }, unused: { type: 'memory', catalog: {}, snapshots: [] } } }, /graph catalog/],
    [{ ...config(), repositories: { local: { type: 'http' }, constructor: { type: 'http' } } }, /Repository names/],
    [{ ...config(), repositories: JSON.parse('{"local":{"type":"http"},"__proto__":{"type":"http"}}') }, /Repository names/],
    [{ ...config(), bindings: { graphs: 'toString', reviews: 'local' } }, /graphs binding/],
    [{ ...config(), repositories: Object.create({ local: { type: 'http' } }) }, /must be an object/],
  ];
  let calls = 0;
  for (const [value, message] of cases) {
    assert.throws(() => createConfiguredRepositories(value, { configUrl, cryptoImpl: webcrypto, fetchImpl: async () => { calls++; } }), message);
  }
  assert.equal(calls, 0);
  assert.equal(Object.prototype.type, undefined);
  assert.throws(() => createConfiguredRepositories(config(), { configUrl: 'file:///config.json' }), /absolute HTTP\(S\) configUrl/);
});

test('local HTTP endpoints are supported and JSON catalog URLs cannot use query credentials', () => {
  for (const host of ['localhost', '127.0.0.1', '[::1]']) {
    assert.doesNotThrow(() => createConfiguredRepositories(config(), { configUrl: `http://${host}:8765/config.json` }));
  }
  const value = config();
  value.repositories.graphs = { type: 'json', url: './catalog.json?signature=secret' };
  value.bindings.graphs = 'graphs';
  assert.throws(() => createConfiguredRepositories(value, { configUrl, cryptoImpl: webcrypto }), /query/);
});

test('startup loader reads configuration before making repository requests', async () => {
  const calls = [];
  const repositories = await loadConfiguredRepositories({ configUrl, fetchImpl: async (url, options) => {
    calls.push({ url, options });
    return response(url === configUrl ? config() : { graphs: [] });
  } });
  assert.deepEqual(calls.map(call => call.url), [configUrl]);
  assert.equal(calls[0].options.cache, 'no-store');
  await repositories.graphRepository.listGraphs();
  assert.equal(calls[1].url, 'https://app.example/team/review/api/graphs');
});

test('missing, malformed or unsupported config stops startup without contacting a default service', async () => {
  for (const [result, message] of [
    [response({ error: 'missing' }, 404), /Cannot load config.json \(404\)/],
    [{ ok: true, status: 200, json: async () => { throw new Error('parse failure'); } }, /not valid JSON/],
    [response({ ...config(), schema_version: 'future/9' }), /schema_version/],
  ]) {
    let calls = 0;
    await assert.rejects(loadConfiguredRepositories({ configUrl, fetchImpl: async () => { calls++; return result; } }), message);
    assert.equal(calls, 1);
  }
});

test('a configuration failure is visible and prevents mounting or review writes', async () => {
  const elements = new Map(['connection-text', 'connection-dot', 'status-pill', 'empty-title', 'empty-description', 'graph-empty', 'toast-container'].map(id => [id, {
    textContent: '', attributes: {}, classes: new Set(id === 'graph-empty' ? ['hidden'] : []),
    setAttribute(name, value) { this.attributes[name] = value; },
    get classList() { return { add: name => this.classes.add(name), remove: name => this.classes.delete(name) }; },
  }]));
  const controls = [{ disabled: false }, { disabled: false }];
  let mounted = false, requests = 0;
  const app = await startReviewApp({
    configUrl,
    documentImpl: { getElementById: id => elements.get(id), querySelectorAll: () => controls },
    mount: () => { mounted = true; },
    fetchImpl: async () => { requests++; return response({ ...config(), bindings: { graphs: 'unknown', reviews: 'local' } }); },
  });
  assert.equal(app, null);
  assert.equal(mounted, false);
  assert.equal(requests, 1);
  assert.equal(elements.get('connection-text').textContent, 'Configuration error');
  assert.match(elements.get('empty-description').textContent, /graphs binding/);
  assert.equal(elements.get('graph-empty').classes.has('hidden'), false);
  assert.equal(elements.get('toast-container').attributes.role, 'alert');
  assert(controls.every(control => control.disabled));
});
