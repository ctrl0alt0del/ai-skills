import test from 'node:test';
import assert from 'node:assert/strict';
import { createHash, webcrypto } from 'node:crypto';
import { createMemoryGraphRepository } from '../src/memory-graph-repository.js';
import { createJsonGraphRepository, graphSnapshotKey } from '../src/json-graph-repository.js';

const snapshot = (id = 'a', revision = 'r1') => ({ id, title: 'Graph ' + id, revision, digest: 'digest-' + revision, graph: { nodes: [{ id: 'node', label: 'original' }] } });
const summary = ({ graph, ...value }) => value;
const catalog = (...graphs) => ({ schema_version: 'behavior-graph-catalog/1', graphs: graphs.map(summary) });
const hash = text => createHash('sha256').update(text, 'utf8').digest('hex');
const key = (id, revision) => `graphs/${hash(id)}/${hash(revision)}.json`;
const baseUrl = 'https://static.example/bucket/project/catalog.json';
const response = (data, status = 200) => ({ ok: status >= 200 && status < 300, status, json: async () => structuredClone(data) });

test('memory catalog explicitly chooses latest and retains immutable detached history', async () => {
  const old = snapshot('a', 'release-10'), current = snapshot('a', 'release-2');
  const inputs = { catalog: catalog(current), snapshots: [current, old] };
  const repository = createMemoryGraphRepository(inputs);
  inputs.catalog.graphs[0].revision = 'wrong';
  inputs.snapshots[0].graph.nodes[0].label = 'modified input';
  const listed = await repository.listGraphs();
  assert.equal(listed[0].revision, 'release-2');
  listed[0].revision = 'modified output';
  const first = await repository.getGraph('a');
  assert.equal(first.revision, 'release-2');
  assert.equal(first.graph.nodes[0].label, 'original');
  first.graph.nodes[0].label = 'modified output';
  assert.equal((await repository.getGraph('a')).graph.nodes[0].label, 'original');
  assert.equal((await repository.getGraph('a', 'release-10')).revision, 'release-10');
  assert.equal((await repository.listGraphs())[0].revision, 'release-2');
});

test('memory graph identity maps safely handle opaque ids and unknown revisions', async () => {
  const one = snapshot('__proto__', 'r/1'), two = snapshot('a\u0000b', 'c'), three = snapshot('a', 'b\u0000c');
  const repository = createMemoryGraphRepository({ catalog: catalog(one, two, three), snapshots: [one, two, three] });
  assert.equal((await repository.getGraph('__proto__', 'r/1')).id, '__proto__');
  assert.equal((await repository.getGraph('a\u0000b', 'c')).id, 'a\u0000b');
  assert.equal((await repository.getGraph('a', 'b\u0000c')).revision, 'b\u0000c');
  await assert.rejects(repository.getGraph('missing'), error => error.status === 404);
  await assert.rejects(repository.getGraph('a', 'missing'), error => error.status === 404);
});

test('memory rejects inconsistent catalogs, duplicate snapshots and unavailable latest revisions', () => {
  const current = snapshot();
  for (const [input, message] of [
    [{ catalog: catalog(current, current), snapshots: [current] }, /duplicate graph identity/],
    [{ catalog: catalog(current), snapshots: [current, current] }, /duplicate identity and revision/],
    [{ catalog: catalog(current), snapshots: [] }, /available snapshot/],
    [{ catalog: catalog({ ...current, digest: 'different' }), snapshots: [current] }, /same digest/],
    [{ catalog: catalog(), snapshots: [current] }, /belong to a graph/],
    [{ catalog: catalog(current), snapshots: null }, /must be an array/],
    [{ catalog: { schema_version: 'wrong', graphs: [] }, snapshots: [] }, /schema_version/],
    [{ catalog: catalog(current), snapshots: [{ ...current, graph: null }] }, /both graph content and a digest/],
  ]) assert.throws(() => createMemoryGraphRepository(input), message);
  const initial = { id: 'empty', title: 'Empty', revision: '0', digest: null, graph: null };
  assert.doesNotThrow(() => createMemoryGraphRepository({ catalog: catalog(initial), snapshots: [initial] }));
});

test('JSON adapter hashes exact UTF-8 identities and preserves the complete catalog prefix', async () => {
  const id = '../orders/客户?draft#v1', revision = 'release/β:1';
  const current = snapshot(id, revision), calls = [];
  const repository = createJsonGraphRepository({ url: baseUrl, cryptoImpl: webcrypto, fetchImpl: async (url, options) => {
    calls.push({ url, options });
    return response(url === baseUrl ? catalog(current) : current);
  } });
  assert.deepEqual(await repository.listGraphs(), [summary(current)]);
  assert.equal((await repository.getGraph(id)).revision, revision);
  assert.equal(calls[2].url, 'https://static.example/bucket/project/' + key(id, revision));
  assert(calls.every(call => call.options.method === 'GET' && call.options.cache === 'no-store'));
  assert.equal(await graphSnapshotKey(id, revision, webcrypto), key(id, revision));
  await assert.rejects(graphSnapshotKey('\ud800', revision, webcrypto), /valid Unicode/);
});

test('JSON pinned historical reads bypass latest catalog and return detached cached snapshots', async () => {
  const old = snapshot('a', 'old'), calls = [];
  const repository = createJsonGraphRepository({ url: baseUrl, cryptoImpl: webcrypto, fetchImpl: async url => {
    calls.push(url); assert.notEqual(url, baseUrl); return response(old);
  } });
  const first = await repository.getGraph('a', 'old');
  first.graph.nodes[0].label = 'mutated';
  const again = await repository.getGraph('a', 'old');
  assert.equal(again.graph.nodes[0].label, 'original');
  assert.deepEqual(calls, ['https://static.example/bucket/project/' + key('a', 'old')]);
});

test('JSON latest reads refresh the catalog while preserving previous immutable revisions', async () => {
  let current = snapshot('a', 'r1');
  const documents = new Map([['r1', current], ['r2', snapshot('a', 'r2')]]);
  const repository = createJsonGraphRepository({ url: baseUrl, cryptoImpl: webcrypto, fetchImpl: async url => {
    if (url === baseUrl) return response(catalog(current));
    return response([...documents.values()].find(value => url.endsWith(key(value.id, value.revision))));
  } });
  assert.equal((await repository.getGraph('a')).revision, 'r1');
  current = documents.get('r2');
  assert.equal((await repository.listGraphs())[0].revision, 'r2');
  assert.equal((await repository.getGraph('a')).revision, 'r2');
  assert.equal((await repository.getGraph('a', 'r1')).revision, 'r1');
  await assert.rejects(repository.getGraph('unknown'), error => error.status === 404);
});

test('JSON rejects mismatched documents and retries a later explicit read after a failed fetch', async () => {
  const current = snapshot(), attempts = [];
  let document = { ...current, revision: 'wrong' };
  const repository = createJsonGraphRepository({ url: baseUrl, cryptoImpl: webcrypto, fetchImpl: async url => {
    attempts.push(url); return response(url === baseUrl ? catalog(current) : document);
  } });
  await assert.rejects(repository.getGraph('a', 'r1'), error => error.status === 409 && /identity and revision/.test(error.message));
  document = current;
  assert.equal((await repository.getGraph('a', 'r1')).revision, 'r1');
  assert.equal(attempts.length, 2);
  const mismatch = createJsonGraphRepository({ url: baseUrl, cryptoImpl: webcrypto, fetchImpl: async url => response(url === baseUrl ? catalog(current) : { ...current, digest: 'wrong' }) });
  await assert.rejects(mismatch.getGraph('a'), error => error.status === 409 && /digest/.test(error.message));
});

test('JSON reports invalid catalogs, unreadable documents and service errors', async () => {
  for (const [result, message] of [
    [response(catalog(snapshot(), snapshot())), /duplicate graph identity/],
    [response({ graphs: [] }), /schema_version/],
    [{ ok: true, status: 200, json: async () => { throw new Error('broken JSON'); } }, /unreadable JSON/],
    [response({ error: 'not found' }, 404), /failed \(404\)/],
  ]) {
    const repository = createJsonGraphRepository({ url: baseUrl, cryptoImpl: webcrypto, fetchImpl: async () => result });
    await assert.rejects(repository.listGraphs(), message);
  }
  assert.throws(() => createJsonGraphRepository({ url: 'file:///catalog.json', cryptoImpl: webcrypto }), /HTTP\(S\)/);
  assert.throws(() => createJsonGraphRepository({ url: baseUrl, cryptoImpl: {} }), /WebCrypto/);
});
