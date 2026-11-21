/** Optional browser test using injected in-memory repositories, no data HTTP API. */
import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { readFile } from 'node:fs/promises';
import { fileURLToPath, pathToFileURL } from 'node:url';
import path from 'node:path';
import { createRequire } from 'node:module';
import { buildArtifacts } from '../build.mjs';

const require = createRequire(import.meta.url);
let playwrightModule;
if (process.env.PLAYWRIGHT_MODULE) playwrightModule = process.env.PLAYWRIGHT_MODULE;
else {
  try { playwrightModule = require.resolve('playwright'); }
  catch {
    if (!process.env.CODEX_PRIMARY_RUNTIME_NODE_MODULES) throw new Error('Install Playwright or set PLAYWRIGHT_MODULE to its module path.');
    playwrightModule = path.join(process.env.CODEX_PRIMARY_RUNTIME_NODE_MODULES, 'playwright/index.mjs');
  }
}
const loadedPlaywright = await import(pathToFileURL(playwrightModule));
const { chromium } = loadedPlaywright.chromium ? loadedPlaywright : loadedPlaywright.default;
const artifacts = await buildArtifacts();
const appModule = [...artifacts.keys()].find(name => /^app\./.test(name));
const mainModule = [...artifacts.keys()].find(name => /^main\./.test(name));
const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../..');
const graph = JSON.parse(await readFile(path.join(repoRoot, 'skills/behavior-graph-review-app/assets/example-graph.json'), 'utf8'));
const fixtureModule = `
import { mountReviewApp } from './${appModule}';
const graph = ${JSON.stringify(graph)};
const clone = v => structuredClone(v);
const reviews = new Map(['alpha','beta'].map(id => [id, {
  graph_id:id, graph_revision:'version-2', graph_digest:id+'-2', title:id, state_version:2,
  round:2, rounds:[1,2].map(round=>({round, graph_revision:'version-'+round, digest:id+'-'+round, summary:'Fixture round '+round, created_at:'2026-11-21', node_count:graph.nodes.length, dependency_count:graph.dependencies.length})),
  annotations:[], approval:null, finalized:null, review_requested:false, agent_active:false,
}]));
let noteNumber=0;
function write(id, version, operation) {
  const s=clone(reviews.get(id));
  if(s.state_version!==version)throw Object.assign(new Error('Fixture review changed'),{status:409});
  operation(s);s.state_version++;s.approval=null;reviews.set(id,s);return clone(s);
}
const graphRepository = {
  async listGraphs(){return ['alpha','beta'].map(id=>({id,title:'Fixture '+id,revision:'version-2',digest:id+'-2'}));},
  async getGraph(id,revision='version-2'){return {id,title:'Fixture '+id,revision,digest:id+'-'+revision.replace('version-',''),graph:clone(graph)};},
};
const reviewRepository = {
  async getReview(id){return clone(reviews.get(id));},
  async createAnnotation(id,input,v){return write(id,v,s=>s.annotations.push({...input,id:'note-'+(++noteNumber),round:s.round,status:'open',source:'ui',source_ref:null,created_at:new Date().toISOString(),resolution:null}));},
  async updateAnnotation(id,noteId,patch,v){return write(id,v,s=>Object.assign(s.annotations.find(n=>n.id===noteId),patch,{status:'open',resolution:null}));},
  async deleteAnnotation(id,noteId,v){return write(id,v,s=>{s.annotations.find(n=>n.id===noteId).status='deleted';});},
  async resolveAnnotation(id,noteId,input,v){return write(id,v,s=>Object.assign(s.annotations.find(n=>n.id===noteId),{status:'resolved',resolution:{...input,source:'ui'}}));},
  async reopenAnnotation(id,noteId,v){return write(id,v,s=>Object.assign(s.annotations.find(n=>n.id===noteId),{status:'open',resolution:null}));},
  async requestReview(id,v){return write(id,v,s=>{s.review_requested=true;});},
  async approveReview(id,input,v){return write(id,v,s=>{s.approval=input;});},
};
window.reviewFixture={reviews,advance(id){reviews.get(id).state_version++;}};
window.reviewApp=mountReviewApp({graphRepository,reviewRepository},{pollInterval:0});
`;
const server = createServer((req,res) => {
  const name = req.url === '/' ? 'index.html' : req.url.slice(1);
  const content = name === mainModule ? fixtureModule : artifacts.get(name);
  if (!content) { res.writeHead(404); res.end(); return; }
  const type = name.endsWith('.html') ? 'text/html' : name.endsWith('.css') ? 'text/css' : 'text/javascript';
  res.writeHead(200,{'Content-Type':type+'; charset=utf-8'});res.end(content);
});
await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
let browser;
try {
  browser = await chromium.launch({headless:true,...(process.env.CHROMIUM_EXECUTABLE ? {executablePath:process.env.CHROMIUM_EXECUTABLE} : {})});
  const page = await browser.newPage({viewport:{width:1440,height:1000}});
  const errors=[],apiRequests=[];
  page.on('pageerror',error=>errors.push(error.message));
  page.on('request',request=>{if(new URL(request.url()).pathname.startsWith('/api/'))apiRequests.push(request.url());});
  await page.goto('http://127.0.0.1:'+server.address().port);
  await page.waitForFunction(()=>window.reviewApp?.controller.state?.graph_id==='alpha');
  assert.equal(await page.locator('#graph-select option').count(),2);
  assert((await page.locator('.graph-node').count())>0);
  await page.locator('#tab-notes').click();
  await page.locator('#annotation-text').fill('A literal <script>window.injected=true</script> question');
  await page.locator('#save-note').click();
  await page.waitForFunction(()=>window.reviewFixture.reviews.get('alpha').annotations.length===1);
  assert.equal(await page.evaluate(()=>window.injected),undefined);
  await page.getByRole('button',{name:'Edit',exact:true}).click();
  await page.locator('#edit-text').fill('Edited fixture question');
  await page.evaluate(()=>window.reviewFixture.advance('alpha'));
  await page.locator('#save-edit').click();
  await page.waitForFunction(()=>document.getElementById('save-edit').textContent==='Retry changes');
  assert.equal(await page.locator('#edit-text').inputValue(),'Edited fixture question');
  await page.locator('#save-edit').click();
  await page.waitForFunction(()=>window.reviewFixture.reviews.get('alpha').annotations[0].text==='Edited fixture question');
  await page.getByRole('button',{name:'Delete',exact:true}).click();
  await page.locator('#confirm-delete').click();
  await page.waitForFunction(()=>window.reviewFixture.reviews.get('alpha').annotations[0].status==='deleted');
  assert.equal(await page.locator('#notes-list .note').count(),0);
  await page.locator('#note-filter').selectOption('deleted');
  assert.equal(await page.locator('#notes-list .note.deleted').count(),1);
  await page.locator('#note-filter').selectOption('all');
  await page.locator('#annotation-text').fill('Unsent Alpha draft');
  await page.locator('#graph-select').selectOption('beta');
  await page.waitForFunction(()=>window.reviewApp.controller.state?.graph_id==='beta');
  assert.equal(await page.locator('#annotation-text').inputValue(),'');
  await page.locator('#annotation-text').fill('Unsent Beta draft');
  await page.locator('#graph-select').selectOption('alpha');
  await page.waitForFunction(()=>window.reviewApp.controller.state?.graph_id==='alpha');
  assert.equal(await page.locator('#annotation-text').inputValue(),'Unsent Alpha draft');
  await page.locator('#round-select').selectOption('1');
  await page.waitForFunction(()=>document.getElementById('status-pill').textContent==='Past proposal');
  assert.equal(await page.locator('#annotation-text').isDisabled(),true);
  await page.locator('#round-select').selectOption('current');
  assert.equal(await page.locator('#annotation-text').isDisabled(),false);
  await page.setViewportSize({width:390,height:844});
  await page.locator('#graph-select').selectOption('beta');
  await page.waitForFunction(()=>window.reviewApp.controller.state?.graph_id==='beta');
  assert.equal(await page.locator('#annotation-text').inputValue(),'Unsent Beta draft');
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth <= window.innerWidth),true);
  assert.deepEqual(apiRequests,[]);
  assert.deepEqual(errors,[]);
  console.log(JSON.stringify({passed:true,checks:['injected repositories without data HTTP','catalog/render','create/edit/delete','stale edit recovery','literal text safety','per-graph drafts','historical read-only','mobile width'],pageErrors:errors.length}));
} finally {
  await browser?.close();
  await new Promise(resolve=>server.close(resolve));
}
