/** Storage-neutral contracts used by the review app. IDs and revisions are opaque. */
export interface GraphSummary {
  id: string;
  title: string;
  revision: string;
  digest: string | null;
}
export interface BehaviorGraph {
  schema_version: string;
  project: { name: string; scope: string[]; sources: unknown[] };
  nodes: unknown[];
  dependencies: unknown[];
  evidence: unknown[];
  coverage: { checked: string[]; blind_spots: string[]; unexpanded: string[]; conflicts: string[] };
}
export interface GraphSnapshot extends GraphSummary { graph: BehaviorGraph | null }
export interface GraphRepository {
  listGraphs(): Promise<GraphSummary[]>;
  /** A specified revision must identify an immutable snapshot, never latest. */
  getGraph(graphId: string, revision?: string): Promise<GraphSnapshot>;
}
export type AnnotationKind = 'correction' | 'question' | 'missing' | 'confirmation' | 'note';
export interface NewAnnotation {
  target_type: 'graph' | 'node' | 'dependency' | 'claim';
  target_id: string | null;
  kind: AnnotationKind;
  text: string;
}
export interface Annotation extends NewAnnotation {
  id: string;
  round: number;
  status: 'open' | 'resolved' | 'deleted';
  source: 'ui' | 'chat' | 'agent';
  source_ref: string | null;
  created_at: string;
  updated_at?: string;
  updated_by?: 'ui' | 'chat' | 'agent';
  deleted_at?: string;
  deleted_by?: 'ui' | 'chat' | 'agent';
  resolution: null | { disposition: string; text: string; source: string; round: number; at: string };
  /** Zero means an agent-authored note has not received human feedback. */
  feedback_revision?: number;
  processed_revision?: number;
  /** Derived from processed_revision < feedback_revision; independent of status. */
  processing_status?: 'unprocessed' | 'processed';
  /** Retained across edits; may describe a previous feedback revision. */
  processing_receipt?: FeedbackProcessingReceipt | null;
}
export type FeedbackOutcome = 'addressed' | 'needs_clarification' | 'no_change' | 'withdrawn';
export interface FeedbackProcessingReceipt {
  feedback_revision: number;
  outcome: FeedbackOutcome;
  summary: string;
  processed_at: string;
  processor: string;
  graph_revision: string;
  graph_digest: string | null;
}
export interface PendingFeedback {
  schema_version: 'behavior-review-feedback/1';
  graph_id: string;
  graph_revision: string;
  graph_digest: string | null;
  state_version: number;
  review_requested: boolean;
  /** Includes unprocessed deletions, even though normal annotation lists hide them. */
  annotations: Annotation[];
}
export interface ReviewSnapshot {
  graph_id: string;
  graph_revision: string;
  graph_digest: string | null;
  state_version: number;
  session_id?: string;
  title?: string;
  round: number;
  rounds: Array<{round: number; graph_revision: string; digest: string; summary: string; created_at: string; node_count: number; dependency_count: number}>;
  annotations: Annotation[];
  approval: null | Record<string, unknown>;
  finalized: null | Record<string, unknown>;
  review_requested: boolean;
  agent_active: boolean;
}
export interface ResolutionInput {
  disposition: 'addressed' | 'accepted_gap' | 'rejected';
  text: string;
}
export interface ApprovalInput { acknowledge_limits: true; text: string }
export interface HumanReviewRepository {
  getReview(graphId: string): Promise<ReviewSnapshot>;
  listAnnotations(graphId: string, options?: {includeDeleted?: boolean}): Promise<Annotation[]>;
  getAnnotation(graphId: string, annotationId: string): Promise<Annotation>;
  createAnnotation(graphId: string, input: NewAnnotation, expectedVersion: number): Promise<ReviewSnapshot>;
  updateAnnotation(graphId: string, annotationId: string, patch: Partial<Pick<NewAnnotation, 'text' | 'kind'>>, expectedVersion: number): Promise<ReviewSnapshot>;
  /** Delete from active review; retain the previous value in audit history. */
  deleteAnnotation(graphId: string, annotationId: string, expectedVersion: number): Promise<ReviewSnapshot>;
  resolveAnnotation(graphId: string, annotationId: string, input: ResolutionInput, expectedVersion: number): Promise<ReviewSnapshot>;
  reopenAnnotation(graphId: string, annotationId: string, expectedVersion: number): Promise<ReviewSnapshot>;
  requestReview(graphId: string, expectedVersion: number): Promise<ReviewSnapshot>;
  approveReview(graphId: string, input: ApprovalInput, expectedVersion: number): Promise<ReviewSnapshot>;
}
/** Optional agent capability. The human review UI does not invoke these writes. */
export interface FeedbackProcessingRepository {
  listPendingFeedback(graphId: string): Promise<PendingFeedback>;
  /** Acknowledge exactly the revision inspected, after saving the resulting work. */
  markFeedbackProcessed(graphId: string, annotationId: string, feedbackRevision: number,
    outcome: FeedbackOutcome, summary: string, processor: string, expectedVersion: number): Promise<ReviewSnapshot>;
}
export interface Repositories { graphRepository: GraphRepository; reviewRepository: HumanReviewRepository }
