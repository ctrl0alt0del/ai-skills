import { cloneDocument, GraphRepositoryError, requireIdentity, validateCatalog, validateSnapshot } from './graph-documents.41133a2b415dffee.js';
import { resolveRepositoryUrl } from './repository-urls.1b1e92b4e9240ca8.js';

/** Match the document-store key layout without interpreting opaque ids as paths. */
export async function graphSnapshotKey(id, revision, cryptoImpl = globalThis.crypto) {
  requireIdentity(id, 'Graph id');
  requireIdentity(revision, 'Graph revision');
  if (typeof cryptoImpl?.subtle?.digest !== 'function') throw new TypeError('WebCrypto SHA-256 is required for the JSON graph repository.');
  const hash = async value => Array.from(new Uint8Array(await cryptoImpl.subtle.digest('SHA-256', new TextEncoder().encode(value))), byte => byte.toString(16).padStart(2, '0')).join('');
  const [graphHash, revisionHash] = await Promise.all([hash(id), hash(revision)]);
  return `graphs/${graphHash}/${revisionHash}.json`;
}

/** Static JSON documents on an HTTP(S) origin, including a public object-storage prefix. */
export function createJsonGraphRepository({ url, fetchImpl = globalThis.fetch, cryptoImpl = globalThis.crypto } = {}) {
  if (typeof fetchImpl !== 'function') throw new TypeError('A JSON graph fetch implementation is required.');
  if (typeof cryptoImpl?.subtle?.digest !== 'function') throw new TypeError('WebCrypto SHA-256 is required for the JSON graph repository.');
  const catalogUrl = new URL(resolveRepositoryUrl(url, undefined, 'The JSON graph catalog URL'));
  const snapshotPromises = new Map();
  async function readJson(location) {
    const response = await fetchImpl(location, { method: 'GET', cache: 'no-store', credentials: 'same-origin' });
    if (!response.ok) throw new GraphRepositoryError(`The graph repository request failed (${response.status}).`, response.status);
    try { return await response.json(); }
    catch { throw new GraphRepositoryError('The graph repository returned an unreadable JSON document.', response.status); }
  }
  async function readCatalog() {
    return validateCatalog(await readJson(catalogUrl.href));
  }
  return {
    async listGraphs() { return cloneDocument((await readCatalog()).graphs); },
    async getGraph(id, revision) {
      requireIdentity(id, 'Graph id');
      let latest;
      if (revision === undefined) {
        latest = (await readCatalog()).graphs.find(summary => summary.id === id);
        if (!latest) throw new GraphRepositoryError('The requested graph was not found in the catalog.', 404);
        revision = latest.revision;
      }
      requireIdentity(revision, 'Graph revision');
      // A pinned historical read does not depend on the current catalog's contents.
      const key = await graphSnapshotKey(id, revision, cryptoImpl);
      if (!snapshotPromises.has(key)) {
        const pending = readJson(new URL(key, catalogUrl).href).then(value => {
          const snapshot = validateSnapshot(value);
          if (snapshot.id !== id || snapshot.revision !== revision) {
            throw new GraphRepositoryError('The graph document does not match the requested identity and revision.', 409);
          }
          return cloneDocument(snapshot);
        }).catch(error => {
          snapshotPromises.delete(key);
          throw error;
        });
        snapshotPromises.set(key, pending);
      }
      const snapshot = await snapshotPromises.get(key);
      if (latest && snapshot.digest !== latest.digest) {
        throw new GraphRepositoryError('The graph document digest does not match its catalog entry.', 409);
      }
      // Cached documents remain immutable even when a caller edits its returned copy.
      return cloneDocument(snapshot);
    },
  };
}
