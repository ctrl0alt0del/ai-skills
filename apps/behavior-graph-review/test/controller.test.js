import test from 'node:test';
import assert from 'node:assert/strict';
import { ReviewController } from '../src/controller.js';

const clone = value => structuredClone(value);
function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}
function fixture() {
  // Independent graph and human-review stores. Neither knows filesystem/HTTP.
  const graphStore = new Map(['a', 'b'].map(id => [id, new Map([
    ['1', {id, title: 'Graph ' + id, revision: '1', digest: 'digest-' + id, graph: {nodes: [{id: id + '-node'}]}}],
  ])]));
  const reviewStore = new Map(['a', 'b'].map(id => [id, {
    graph_id: id, graph_revision: '1', graph_digest: 'digest-' + id,
    state_version: 1, round: 1, annotations: [], approval: null,
  }]));
  const calls = [];
  const graphRepository = {
    async listGraphs() { return [...graphStore.values()].map(versions => clone(versions.get('1'))); },
    async getGraph(id, revision) { calls.push(['getGraph', id, revision]); return clone(graphStore.get(id).get(revision)); },
  };
  function write(id, version, operation) {
    const next = clone(reviewStore.get(id));
    if (version !== next.state_version) throw Object.assign(new Error('stale'), {status: 409});
    operation(next); next.state_version++; next.approval = null;
    reviewStore.set(id, next); return clone(next);
  }
  const reviewRepository = {
    async getReview(id) { return clone(reviewStore.get(id)); },
    async createAnnotation(id, input, version) { return write(id, version, s => s.annotations.push({...input, id: 'n1', status: 'open'})); },
    async updateAnnotation(id, noteId, patch, version) { return write(id, version, s => Object.assign(s.annotations.find(n => n.id === noteId), patch, {status: 'open'})); },
    async deleteAnnotation(id, noteId, version) { return write(id, version, s => {s.annotations.find(n => n.id === noteId).status = 'deleted';}); },
    async resolveAnnotation(id, noteId, input, version) { return write(id, version, s => Object.assign(s.annotations.find(n => n.id === noteId), {status: 'resolved', resolution: input})); },
    async reopenAnnotation(id, noteId, version) { return write(id, version, s => {s.annotations.find(n => n.id === noteId).status = 'open';}); },
    async requestReview(id, version) { return write(id, version, s => {s.review_requested = true;}); },
    async approveReview(id, input, version) { return write(id, version, s => {s.approval = input;}); },
  };
  const repositories = { graphRepository, reviewRepository };
  return {controller: new ReviewController(repositories), repositories, graphStore, reviewStore, calls};
}

test('independent repositories support graph catalog and complete annotation CRUD', async () => {
  const {controller, reviewStore, calls} = fixture();
  assert.deepEqual((await controller.listGraphs()).map(g => g.id), ['a', 'b']);
  await controller.selectGraph('a');
  assert.deepEqual(calls, [['getGraph', 'a', '1']]);
  const input = {target_type: 'graph', target_id: null, kind: 'question', text: 'What is approved?'};
  await controller.mutate('createAnnotation', [input], 1);
  await controller.mutate('updateAnnotation', ['n1', {text: 'Updated question'}], 2);
  assert.equal(controller.state.annotations[0].text, 'Updated question');
  await controller.mutate('deleteAnnotation', ['n1'], 3);
  assert.equal(controller.state.annotations[0].status, 'deleted');
  assert.equal(reviewStore.get('b').state_version, 1);
  assert.deepEqual(reviewStore.get('b').annotations, []);
});

test('review selects its immutable graph revision even when repository has a newer graph', async () => {
  const {controller, graphStore} = fixture();
  graphStore.get('a').set('2', {id:'a', revision:'2', digest:'new', graph:{nodes:[]}});
  await controller.selectGraph('a');
  assert.equal(controller.state.graph_revision, '1');
  assert.deepEqual(controller.state.graph.nodes, [{id:'a-node'}]);
});

test('mismatched immutable snapshot is rejected before rendering or mutation', async () => {
  const {controller, graphStore} = fixture();
  graphStore.get('a').get('1').digest = 'wrong-content';
  await assert.rejects(controller.selectGraph('a'), /does not match/);
  assert.equal(controller.state, null);
});

test('late graph A read cannot overwrite selected graph B', async () => {
  const {controller, repositories} = fixture();
  const late = deferred();
  const original = repositories.reviewRepository.getReview;
  repositories.reviewRepository.getReview = id => id === 'a' ? late.promise : original(id);
  const a = controller.selectGraph('a');
  await controller.selectGraph('b');
  late.resolve(await original('a'));
  assert.equal(await a, null);
  assert.equal(controller.graphId, 'b');
  assert.equal(controller.state.graph_id, 'b');
});

test('late mutation or error for old graph is not applied to newly selected graph', async () => {
  const {controller, repositories} = fixture();
  await controller.selectGraph('a');
  const late = deferred();
  repositories.reviewRepository.createAnnotation = () => late.promise;
  const pending = controller.mutate('createAnnotation', [{text:'old graph'}], 1);
  await controller.selectGraph('b');
  late.reject(new Error('old graph failed'));
  assert.equal(await pending, null);
  assert.equal(controller.state.graph_id, 'b');
});

test('an older state version cannot roll back a more recent mutation', async () => {
  const {controller, repositories, reviewStore} = fixture();
  await controller.selectGraph('a');
  const late = deferred();
  const old = clone(reviewStore.get('a'));
  repositories.reviewRepository.getReview = () => late.promise;
  const pendingRead = controller.refresh();
  await controller.mutate('createAnnotation', [{text:'new data'}], 1);
  late.resolve(old);
  assert.equal(await pendingRead, null);
  assert.equal(controller.state.state_version, 2);
  assert.equal(controller.state.annotations.length, 1);
});

test('conflicts preserve the server version and are never blindly retried', async () => {
  const {controller, reviewStore} = fixture();
  await controller.selectGraph('a');
  reviewStore.get('a').state_version = 2;
  await assert.rejects(controller.mutate('createAnnotation', [{text:'draft'}], 1), e => e.status === 409);
  assert.equal(controller.state.state_version, 1);
  assert.equal(reviewStore.get('a').annotations.length, 0);
});

test('history reads stay bound to graph identity and cannot leak across selection', async () => {
  const {controller, repositories} = fixture();
  await controller.selectGraph('a');
  const late = deferred();
  const original = repositories.graphRepository.getGraph;
  repositories.graphRepository.getGraph = (id, revision) => id === 'a' ? late.promise : original(id, revision);
  const history = controller.historicalGraph('1');
  await controller.selectGraph('b');
  late.resolve(await original('a', '1'));
  assert.equal(await history, null);
  assert.equal(controller.state.graph_id, 'b');
});

test('invalid references, missing methods and duplicate graph IDs fail clearly', async () => {
  const f = fixture();
  assert.throws(() => new ReviewController({graphRepository:{}, reviewRepository:{}}), /listGraphs/);
  f.repositories.graphRepository.listGraphs = async () => [{id:'a'}, {id:'a'}];
  await assert.rejects(f.controller.listGraphs(), /duplicate/);
  f.reviewStore.get('a').graph_id = 'b';
  await assert.rejects(f.controller.selectGraph('a'), /invalid graph reference/);
});

test('historical rounds use recorded opaque revisions, with digest validation', async () => {
  const {controller, reviewStore, graphStore, calls} = fixture();
  reviewStore.get('a').rounds = [{round:7,graph_revision:'s3-version:abc/123',digest:'old-digest'}];
  graphStore.get('a').set('s3-version:abc/123',{id:'a',revision:'s3-version:abc/123',digest:'old-digest',graph:{nodes:[{id:'old'}]}});
  await controller.selectGraph('a');
  const round = await controller.getRound(7);
  assert.deepEqual(calls.at(-1), ['getGraph','a','s3-version:abc/123']);
  assert.equal(round.round,7);
  assert.deepEqual(round.graph.nodes,[{id:'old'}]);
  graphStore.get('a').get('s3-version:abc/123').digest='changed';
  await assert.rejects(controller.getRound(7),/does not match/);
});

test('missing historical revision is not guessed from the display round', async () => {
  const {controller, reviewStore} = fixture();
  reviewStore.get('a').rounds=[{round:1,digest:'digest-a'}];
  await controller.selectGraph('a');
  await assert.rejects(controller.getRound(1),/no immutable graph revision/);
});

test('late historical replies cannot replace a newer history selection or current view', async () => {
  const {controller,repositories,reviewStore,graphStore} = fixture();
  reviewStore.get('a').rounds=[
    {round:1,graph_revision:'old:1',digest:'d1'},
    {round:2,graph_revision:'old:2',digest:'d2'},
  ];
  graphStore.get('a').set('old:2',{id:'a',revision:'old:2',digest:'d2',graph:{nodes:[]}});
  await controller.selectGraph('a');
  const old=deferred(), original=repositories.graphRepository.getGraph;
  repositories.graphRepository.getGraph=(id,revision)=>revision==='old:1'?old.promise:original(id,revision);
  const pending=controller.getRound(1);
  assert.equal((await controller.getRound(2)).round,2);
  old.resolve({id:'a',revision:'old:1',digest:'d1',graph:{nodes:[]}});
  assert.equal(await pending,null);
  const next=deferred();repositories.graphRepository.getGraph=()=>next.promise;
  const canceled=controller.getRound(1);controller.cancelHistory();
  next.resolve({id:'a',revision:'old:1',digest:'d1',graph:{nodes:[]}});
  assert.equal(await canceled,null);
});
