/**
 * Application coordination over injected repositories. This module has no DOM,
 * network, filesystem, database, or credential knowledge.
 */
export class ReviewController {
  constructor({ graphRepository, reviewRepository }) {
    for (const method of ['listGraphs', 'getGraph']) {
      if (typeof graphRepository?.[method] !== 'function') {
        throw new TypeError(`GraphRepository.${method} is required`);
      }
    }
    for (const method of ['getReview', 'createAnnotation', 'updateAnnotation',
      'deleteAnnotation', 'resolveAnnotation', 'reopenAnnotation', 'requestReview', 'approveReview']) {
      if (typeof reviewRepository?.[method] !== 'function') {
        throw new TypeError(`HumanReviewRepository.${method} is required`);
      }
    }
    this.graphs = graphRepository;
    this.reviews = reviewRepository;
    this.graphId = null;
    this.state = null;
    this.generation = 0;
    this.requestSequence = 0;
    this.appliedSequence = 0;
    this.roundSequence = 0;
  }

  async listGraphs() {
    const graphs = await this.graphs.listGraphs();
    if (!Array.isArray(graphs) || graphs.some(g => typeof g?.id !== 'string' || !g.id)) {
      throw new Error('The graph repository returned an invalid catalog.');
    }
    if (new Set(graphs.map(g => g.id)).size !== graphs.length) {
      throw new Error('The graph repository returned duplicate graph identities.');
    }
    return graphs;
  }

  async selectGraph(graphId) {
    this.generation += 1;
    this.cancelHistory();
    this.graphId = graphId;
    this.state = null;
    this.appliedSequence = 0;
    return graphId === null ? null : this.refresh();
  }

  async compose(review, graphId) {
    if (review?.graph_id !== graphId || typeof review.graph_revision !== 'string' ||
      !Number.isInteger(review.state_version) || review.state_version < 1) {
      throw new Error('The review repository returned an invalid graph reference.');
    }
    const snapshot = await this.graphs.getGraph(graphId, review.graph_revision);
    if (snapshot?.id !== graphId || snapshot.revision !== review.graph_revision ||
      snapshot.digest !== review.graph_digest) {
      throw new Error('The graph snapshot does not match this review. Reload before editing.');
    }
    return {
      ...review,
      title: snapshot.title ?? review.title,
      session_id: review.session_id ?? graphId,
      graph: snapshot.graph,
    };
  }

  accept(state, graphId, generation, sequence) {
    if (this.graphId !== graphId || this.generation !== generation) return null;
    if (this.state && state.state_version < this.state.state_version) return null;
    if (this.state && state.state_version === this.state.state_version && sequence < this.appliedSequence) return null;
    this.appliedSequence = sequence;
    this.state = state;
    return state;
  }

  async refresh() {
    const graphId = this.graphId;
    if (graphId === null) return null;
    const generation = this.generation;
    const sequence = ++this.requestSequence;
    try {
      const review = await this.reviews.getReview(graphId);
      if (this.graphId !== graphId || this.generation !== generation) return null;
      const state = await this.compose(review, graphId);
      return this.accept(state, graphId, generation, sequence);
    } catch (error) {
      if (this.graphId !== graphId || this.generation !== generation) return null;
      throw error;
    }
  }

  async mutate(method, args, expectedVersion) {
    const graphId = this.graphId;
    if (graphId === null) throw new Error('Choose a graph first.');
    const generation = this.generation;
    const sequence = ++this.requestSequence;
    const allowed = ['createAnnotation', 'updateAnnotation', 'deleteAnnotation',
      'resolveAnnotation', 'reopenAnnotation', 'requestReview', 'approveReview'];
    if (!allowed.includes(method)) throw new TypeError('Unknown review operation.');
    try {
      const review = await this.reviews[method](graphId, ...args, expectedVersion);
      if (this.graphId !== graphId || this.generation !== generation) return null;
      const state = await this.compose(review, graphId);
      return this.accept(state, graphId, generation, sequence);
    } catch (error) {
      if (this.graphId !== graphId || this.generation !== generation) return null;
      throw error;
    }
  }

  cancelHistory() {
    this.roundSequence += 1;
  }

  async getRound(round) {
    const sequence = ++this.roundSequence;
    const metadata = this.state?.rounds?.find(item => item.round === round);
    if (!metadata || typeof metadata.graph_revision !== 'string' || !metadata.graph_revision) {
      throw new Error('This review round has no immutable graph revision reference.');
    }
    const snapshot = await this.historicalGraph(metadata.graph_revision);
    if (!snapshot || sequence !== this.roundSequence) return null;
    if (snapshot.digest !== metadata.digest) {
      throw new Error('This graph revision does not match the recorded review round.');
    }
    return { ...metadata, graph: snapshot.graph };
  }

  async historicalGraph(revision) {
    const graphId = this.graphId;
    const generation = this.generation;
    if (graphId === null) return null;
    try {
      const snapshot = await this.graphs.getGraph(graphId, revision);
      if (this.graphId !== graphId || this.generation !== generation) return null;
      if (snapshot?.id !== graphId || snapshot.revision !== revision) {
        throw new Error('The graph repository returned a different revision.');
      }
      return snapshot;
    } catch (error) {
      if (this.graphId !== graphId || this.generation !== generation) return null;
      throw error;
    }
  }
}
