import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, readFile, writeFile, rm } from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import { createHash } from 'node:crypto';
import { build, buildArtifacts } from '../build.mjs';

test('build is reproducible and manifest authenticates exactly the served files', async () => {
  const first = await buildArtifacts(), second = await buildArtifacts();
  assert.deepEqual(first, second);
  const manifest = JSON.parse(first.get('manifest.json'));
  assert.equal(manifest.schema_version, 'behavior-review-app-dist/1');
  assert.equal(manifest.entry, 'index.html');
  assert.equal(Object.keys(manifest.files).length, first.size - 1);
  for (const [name, hash] of Object.entries(manifest.files)) {
    assert.equal(createHash('sha256').update(first.get(name)).digest('hex'), hash);
  }
  const html = first.get('index.html');
  assert.match(html, /<script type="module" src="\.\/main\.[a-f0-9]+\.js">\s*<\/script>/);
  assert.doesNotMatch(html, /<script(?![^>]*\bsrc=)[^>]*>/);
});

test('check detects stale assets without rewriting them', async t => {
  const out = await mkdtemp(path.join(os.tmpdir(),'review-app-build-'));
  t.after(() => rm(out,{recursive:true,force:true}));
  await build({out}); await build({out,check:true});
  const filename = path.join(out,'index.html');
  await writeFile(filename,'stale');
  await assert.rejects(build({out,check:true}),/stale/);
  assert.equal(await readFile(filename,'utf8'),'stale');
});

test('build refuses to overwrite unrelated files', async t => {
  const out = await mkdtemp(path.join(os.tmpdir(),'review-app-build-'));
  t.after(() => rm(out,{recursive:true,force:true}));
  await writeFile(path.join(out,'personal.txt'),'keep');
  await assert.rejects(build({out}),/Refusing/);
  assert.equal(await readFile(path.join(out,'personal.txt'),'utf8'),'keep');
});
