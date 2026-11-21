/** HTTP is one adapter. UI/controller modules never know these routes or tokens. */
export class RepositoryError extends Error {
  constructor(message, status) {
    super(message);
    this.name = 'RepositoryError';
    this.status = status;
  }
}

export function createHttpRepositories({ baseUrl = '', fetchImpl = globalThis.fetch } = {}) {
  if (typeof fetchImpl !== 'function') throw new TypeError('An HTTP fetch implementation is required.');
  const root = baseUrl.replace(/\/$/, '');
  let tokenPromise;
  async function request(path, { method = 'GET', body } = {}) {
    const options = { method, cache: 'no-store', credentials: 'same-origin' };
    if (method !== 'GET') {
      if (!tokenPromise) {
        tokenPromise = request('/api/bootstrap').then(data => {
          if (typeof data.csrf_token !== 'string' || !data.csrf_token) {
            throw new Error('The review service did not provide a write token. Reload the app.');
          }
          return data.csrf_token;
        }).catch(error => { tokenPromise = null; throw error; });
      }
      options.headers = { 'Content-Type': 'application/json', 'X-Review-Token': await tokenPromise };
      options.body = JSON.stringify(body);
    }
    const response = await fetchImpl(root + path, options);
    let data;
    try { data = await response.json(); }
    catch { throw new RepositoryError('The review service returned an unreadable response. Your unsaved text is preserved.', response.status); }
    if (!response.ok) {
      if (response.status === 403) tokenPromise = null;
      throw new RepositoryError(data.error || `Review request failed (${response.status}).`, response.status);
    }
    return data;
  }
  const graphPath = id => '/api/graphs/' + encodeURIComponent(id);
  const notePath = (id, noteId) => graphPath(id) + '/annotations/' + encodeURIComponent(noteId);
  const write = (path, method, input, expectedVersion) => request(path, {
    method,
    body: { ...input, expected_version: expectedVersion },
  });
  return {
    graphRepository: {
      async listGraphs() {
        const data = await request('/api/graphs');
        return data.graphs;
      },
      getGraph(id, revision) {
        const suffix = revision === undefined ? '' : '?revision=' + encodeURIComponent(revision);
        return request(graphPath(id) + suffix);
      },
    },
    reviewRepository: {
      getReview: id => request(graphPath(id) + '/review'),
      async listAnnotations(id, { includeDeleted = false } = {}) {
        const data = await request(graphPath(id) + '/annotations' + (includeDeleted ? '?include_deleted=true' : ''));
        return data.annotations;
      },
      getAnnotation: (id, noteId) => request(notePath(id, noteId)),
      createAnnotation: (id, input, version) => write(graphPath(id) + '/annotations', 'POST', input, version),
      updateAnnotation: (id, noteId, patch, version) => write(notePath(id, noteId), 'PATCH', patch, version),
      deleteAnnotation: (id, noteId, version) => write(notePath(id, noteId), 'DELETE', {}, version),
      resolveAnnotation: (id, noteId, input, version) => write(graphPath(id) + '/resolve', 'POST', { ...input, annotation_id: noteId }, version),
      reopenAnnotation: (id, noteId, version) => write(graphPath(id) + '/reopen', 'POST', { annotation_id: noteId }, version),
      requestReview: (id, version) => write(graphPath(id) + '/request-review', 'POST', {}, version),
      approveReview: (id, input, version) => write(graphPath(id) + '/approve', 'POST', input, version),
    },
  };
}
