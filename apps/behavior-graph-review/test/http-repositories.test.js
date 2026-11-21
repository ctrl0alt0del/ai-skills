import test from 'node:test';
import assert from 'node:assert/strict';
import { createHttpRepositories } from '../src/http-repositories.js';

function fixture(handler = () => ({})) {
  const calls = [];
  const repositories = createHttpRepositories({
    baseUrl: '/review/',
    async fetchImpl(url, options) {
      calls.push({url, ...options, body: options.body ? JSON.parse(options.body) : undefined});
      const data = url.endsWith('/api/bootstrap') ? {csrf_token:'write-token'} : handler(url, options);
      return {ok:true, status:200, async json() {return data;}};
    },
  });
  return {...repositories, calls};
}

test('graph adapter lists and gets encoded, explicitly pinned graph revisions', async () => {
  const {graphRepository, calls} = fixture(url => url.endsWith('/api/graphs') ? {graphs:[{id:'a/b'}]} : {id:'a/b'});
  assert.deepEqual(await graphRepository.listGraphs(), [{id:'a/b'}]);
  await graphRepository.getGraph('a/b', 'round:1?');
  assert.equal(calls[1].url, '/review/api/graphs/a%2Fb?revision=round%3A1%3F');
  assert.equal(calls[1].method, 'GET');
  assert.equal(calls[1].cache, 'no-store');
});

test('CRUD methods use separate routes and preserve CAS version and content', async () => {
  const {reviewRepository:r, calls} = fixture();
  const input={target_type:'graph', target_id:null, kind:'note', text:'literal $() <script>'};
  await r.createAnnotation('graph', input, 3);
  await r.updateAnnotation('graph', 'note/a', {text:'edited'}, 4);
  await r.deleteAnnotation('graph', 'note/a', 5);
  assert.equal(calls.filter(c => c.url.endsWith('/api/bootstrap')).length, 1);
  const mutations = calls.filter(c => c.method !== 'GET');
  assert.deepEqual(mutations.map(c => c.method), ['POST','PATCH','DELETE']);
  assert.equal(mutations[1].url, '/review/api/graphs/graph/annotations/note%2Fa');
  assert.deepEqual(mutations.map(c => c.body.expected_version), [3,4,5]);
  assert.equal(mutations[0].body.text, input.text);
  assert(mutations.every(c => c.headers['X-Review-Token'] === 'write-token'));
});

test('human review reads support active/deleted lists and direct lookup', async () => {
  const {reviewRepository:r, calls} = fixture(() => ({annotations:[{id:'n'}]}));
  assert.deepEqual(await r.listAnnotations('a'), [{id:'n'}]);
  await r.listAnnotations('a', {includeDeleted:true});
  await r.getAnnotation('a','n');
  await r.getReview('a');
  assert.deepEqual(calls.map(c => c.url), [
    '/review/api/graphs/a/annotations', '/review/api/graphs/a/annotations?include_deleted=true',
    '/review/api/graphs/a/annotations/n', '/review/api/graphs/a/review',
  ]);
});

test('review workflow operations retain explicit action meaning', async () => {
  const {reviewRepository:r, calls} = fixture();
  await r.resolveAnnotation('a','n',{disposition:'addressed',text:'done'},4);
  await r.reopenAnnotation('a','n',5);
  await r.requestReview('a',6);
  await r.approveReview('a',{acknowledge_limits:true,text:'actual reviewer approval'},7);
  assert.deepEqual(calls.filter(c => c.method==='POST').map(c => c.url.split('/').at(-1)), ['resolve','reopen','request-review','approve']);
  assert.equal(calls.at(-1).body.expected_version,7);
});

test('HTTP conflicts surface without a retry and preserve status for draft recovery', async () => {
  let writes = 0;
  const {reviewRepository} = createHttpRepositories({async fetchImpl(url) {
    if (url.endsWith('/api/bootstrap')) return {ok:true, status:200, json:async()=>({csrf_token:'t'})};
    writes++; return {ok:false,status:409,json:async()=>({error:'review changed'})};
  }});
  await assert.rejects(reviewRepository.deleteAnnotation('a','n',1), e => e.status === 409 && /review changed/.test(e.message));
  assert.equal(writes,1);
});

test('malformed responses and missing tokens fail without a write', async () => {
  let calls = 0;
  const {reviewRepository} = createHttpRepositories({async fetchImpl() {
    calls++; return {ok:true,status:200,json:async()=>({})};
  }});
  await assert.rejects(reviewRepository.createAnnotation('a',{},1), /write token/);
  assert.equal(calls,1);
  const {graphRepository} = createHttpRepositories({async fetchImpl() {
    return {ok:true,status:200,json:async()=>{throw new Error('invalid JSON');}};
  }});
  await assert.rejects(graphRepository.listGraphs(), /unreadable response/);
});
