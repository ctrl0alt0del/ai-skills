#!/usr/bin/env node
/** Reproducible build: version static modules/assets and emit a strict manifest. */
import { createHash } from 'node:crypto';
import { readFile, readdir, mkdir, writeFile, rm } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const appRoot = path.dirname(fileURLToPath(import.meta.url));
const srcRoot = path.join(appRoot, 'src');
const defaultOut = path.resolve(appRoot, '../../skills/behavior-graph-review-app/assets/review-app/dist');
const schema = 'behavior-review-app-dist/1';
const sha = content => createHash('sha256').update(content).digest('hex');

export async function buildArtifacts() {
  const artifacts = new Map();
  const built = new Map();
  const visiting = new Set();
  async function module(name) {
    if (built.has(name)) return built.get(name);
    if (!/^[a-z][a-z0-9-]*\.js$/.test(name)) throw new Error(`Unsupported module path: ${name}`);
    if (visiting.has(name)) throw new Error(`Module import cycle: ${name}`);
    visiting.add(name);
    let code = await readFile(path.join(srcRoot, name), 'utf8');
    // Source modules deliberately use relative, static ESM imports. This is asset
    // fingerprinting, not a JavaScript parser/minifier or source transpiler.
    const imports = [...code.matchAll(/^import\s+[^;\n]*?from\s+(['"])(\.\/[^'"]+)\1\s*;/gm)];
    for (const imported of imports) {
      const filename = await module(imported[2].slice(2));
      code = code.replace(imported[0], imported[0].replace(imported[2], './' + filename));
    }
    const filename = name.replace(/\.js$/, '.' + sha(code).slice(0, 16) + '.js');
    artifacts.set(filename, code);
    built.set(name, filename);
    visiting.delete(name);
    return filename;
  }
  const entry = await module('main.js');
  const css = await readFile(path.join(srcRoot, 'styles.css'), 'utf8');
  const cssName = 'styles.' + sha(css).slice(0, 16) + '.css';
  artifacts.set(cssName, css);
  let html = await readFile(path.join(srcRoot, 'index.html'), 'utf8');
  if (!html.includes('src="./main.js"') || !html.includes('href="./styles.css"')) {
    throw new Error('The HTML entry must reference main.js and styles.css.');
  }
  html = html.replace('src="./main.js"', `src="./${entry}"`)
    .replace('href="./styles.css"', `href="./${cssName}"`);
  artifacts.set('index.html', html);
  // Keep a stable configuration URL while authenticating the shipped default.
  // The local server can project its own public client configuration at this URL.
  artifacts.set('config.json', await readFile(path.join(srcRoot, 'config.json'), 'utf8'));
  const sourceFiles = {};
  for (const name of (await readdir(srcRoot)).sort()) {
    sourceFiles['src/' + name] = sha(await readFile(path.join(srcRoot, name)));
  }
  for (const name of ['build.mjs', 'package.json']) sourceFiles[name] = sha(await readFile(path.join(appRoot, name)));
  const files = Object.fromEntries([...artifacts].sort(([a], [b]) => a.localeCompare(b)).map(([name, content]) => [name, sha(content)]));
  const manifest = { schema_version: schema, entry: 'index.html', source_digest: sha(JSON.stringify(sourceFiles)), files };
  artifacts.set('manifest.json', JSON.stringify(manifest, null, 2) + '\n');
  return artifacts;
}

export async function build({ out = defaultOut, check = false } = {}) {
  out = path.resolve(out);
  const artifacts = await buildArtifacts();
  let existing = [];
  try { existing = await readdir(out); }
  catch (error) { if (error.code !== 'ENOENT') throw error; }
  if (check) {
    if (JSON.stringify([...existing].sort()) !== JSON.stringify([...artifacts.keys()].sort())) {
      throw new Error('Built asset set differs from source. Run the build command.');
    }
    for (const [name, content] of artifacts) {
      if (await readFile(path.join(out, name), 'utf8') !== content) {
        throw new Error(`Built asset is stale: ${name}. Run the build command.`);
      }
    }
    return { checked: true, files: artifacts.size, out };
  }
  if (existing.length) {
    let previous;
    try { previous = JSON.parse(await readFile(path.join(out, 'manifest.json'), 'utf8')); }
    catch { throw new Error('Refusing to overwrite a directory without this app’s build manifest.'); }
    if (previous.schema_version !== schema || existing.some(name => name !== 'manifest.json' && !Object.hasOwn(previous.files ?? {}, name))) {
      throw new Error('Refusing to replace unrecognized files in the build destination.');
    }
    for (const name of existing) {
      if (!artifacts.has(name)) await rm(path.join(out, name));
    }
  }
  await mkdir(out, { recursive: true });
  for (const [name, content] of artifacts) await writeFile(path.join(out, name), content);
  return { built: true, files: artifacts.size, out };
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const args = process.argv.slice(2);
  let out = defaultOut, check = false;
  try {
    for (let i = 0; i < args.length; i++) {
      if (args[i] === '--check') check = true;
      else if (args[i] === '--out' && args[i + 1]) out = args[++i];
      else throw new Error('Usage: node build.mjs [--check] [--out /absolute/dist]');
    }
    console.log(JSON.stringify(await build({ out, check })));
  } catch (error) {
    console.error(error.message); process.exitCode = 1;
  }
}
