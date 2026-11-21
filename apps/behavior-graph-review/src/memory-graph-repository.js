import { cloneDocument, GraphRepositoryError, requireIdentity, validateCatalog, validateSnapshot } from './graph-documents.js';

/** Read-only, detached snapshots. The catalog explicitly names each graph's latest revision. */
export function createMemoryGraphRepository({ catalog, snapshots } = {}) {
  const ownedCatalog = validateCatalog(cloneDocument(catalog));
  if (!Array.isArray(snapshots)) throw new TypeError('Memory graph repository snapshots must be an array.');
  const versions = new Map();
  const latest = new Map(ownedCatalog.graphs.map(summary => [summary.id, summary]));
  for (const value of snapshots) {
    const snapshot = validateSnapshot(cloneDocument(value));
    if (!latest.has(snapshot.id)) throw new TypeError('Every memory snapshot must belong to a graph in its catalog.');
    if (!versions.has(snapshot.id)) versions.set(snapshot.id, new Map());
    const graphVersions = versions.get(snapshot.id);
    if (graphVersions.has(snapshot.revision)) throw new TypeError('Memory graph snapshots contain a duplicate identity and revision.');
    graphVersions.set(snapshot.revision, snapshot);
  }
  for (const summary of ownedCatalog.graphs) {
    const snapshot = versions.get(summary.id)?.get(summary.revision);
    if (!snapshot || snapshot.digest !== summary.digest) {
      throw new TypeError('Every memory catalog entry must reference an available snapshot with the same digest.');
    }
  }
  return {
    async listGraphs() { return cloneDocument(ownedCatalog.graphs); },
    async getGraph(id, revision) {
      requireIdentity(id, 'Graph id');
      if (revision !== undefined) requireIdentity(revision, 'Graph revision');
      const snapshot = versions.get(id)?.get(revision ?? latest.get(id)?.revision);
      if (!snapshot) throw new GraphRepositoryError('The requested graph snapshot was not found.', 404);
      return cloneDocument(snapshot);
    },
  };
}
