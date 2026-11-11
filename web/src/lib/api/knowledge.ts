/** Mirror of runtime/daemon/routes/knowledge.py. Distill and immutable archives. */
import { request } from './client';

export interface SourceCoverage {
  category: string;
  state: 'included' | 'excluded' | 'unavailable';
  reason: string;
  /** null means unknown. */
  count: number | null;
  cutoff: string;
  errors: string[];
}

export interface KnowledgeSourceRecord {
  reference_id: string;
  category: 'runtime_memory' | 'claude_memory';
  relative_path: string;
  sha256: string;
  body: string;
  metadata: Record<string, unknown>;
}

export interface RetiredKnowledgeSource {
  source_id: string;
  name: string;
  team: string;
  provider: string;
  retirement: 'retained_definition_and_workspace';
  retired_at: string | null;
  cutoff: string;
  consistent: boolean;
  capture_available: boolean;
  coverage: SourceCoverage[];
  records: KnowledgeSourceRecord[];
  errors: string[];
}

export interface RetiredKnowledgeReference {
  source_id: string;
  reference_id: string;
  available: boolean;
  reason: string | null;
  record: KnowledgeSourceRecord | null;
  coverage: SourceCoverage[];
}

export const getRetiredKnowledgeSource = (
  slug: string,
  sourceId: string,
): Promise<RetiredKnowledgeSource> =>
  request(`/orgs/${encodeURIComponent(slug)}/knowledge/retired-agents/${encodeURIComponent(sourceId)}`);

export const getRetiredKnowledgeReference = (
  slug: string,
  sourceId: string,
  referenceId: string,
): Promise<RetiredKnowledgeReference> =>
  request(`/orgs/${encodeURIComponent(slug)}/knowledge/retired-agents/${encodeURIComponent(sourceId)}/sources/${encodeURIComponent(referenceId)}`);

export interface DistillSubmission {
  submission_id: string;
  source_id: string;
  confirmed: true;
  processor_id?: string | null;
}

export interface DistillOperation {
  operation_id: string;
  submission_id: string;
  source_id: string;
  state: 'Queued' | 'Processing' | 'Ready' | 'Failed';
  phase: string;
  cutoff: string;
  requested_processor_id: string | null;
  processor_id: string | null;
  task_id: string | null;
  session_id: string | null;
  result_id: number | null;
  created_at: string;
  updated_at: string;
  failure: Record<string, string | number> | null;
  archive_id: string | null;
  archive_version: number | null;
}

export interface KnowledgeDisposition {
  reference_id: string;
  disposition: 'selected' | 'reference_only' | 'excluded';
  reason: string;
  exclusion_category: 'credentials' | 'instructions_permissions' | 'skills_identity_sessions_caches'
    | 'unrelated_private' | 'task_conversation_history' | 'incidental' | 'unsupported'
    | 'unusable' | 'redundant' | null;
  count: number | null;
  retained_reference_id?: string | null;
}

export interface CuratedKnowledgeItem {
  item_id: string;
  category: 'lesson_fact' | 'procedure' | 'founder_decision_preference' | 'open_work_reference';
  title: string;
  content: string | null;
  references: string[];
  instill_eligible: boolean;
  attribution: {
    reference_id: string;
    original_author: string | null;
    authored_at: string | null;
    observed_at: string | null;
    founder_ruling: string | null;
    founder_ruling_reference: string | null;
  }[];
  flags: ('historical' | 'uncertain' | 'conflict' | 'superseded' | 'evicted' | 'stale' | 'promoted' | 'attribution_unverified')[];
  warnings: string[];
  prerequisites: string | null;
  limits: string | null;
  open_work: {
    owner: string | null;
    status: string | null;
    as_of: string | null;
    next_action: string | null;
    holds: string | null;
  } | null;
}

export interface KnowledgeArchiveManifest {
  format: 'knowledge-archive@1';
  org_slug: string;
  archive_id: string;
  version: number;
  operation_id: string;
  source_id: string;
  source_name: string;
  source_team: string;
  source_provider: string;
  cutoff: string;
  processed_at: string;
  processor_id: string;
  task_id: string;
  session_id: string;
  result_id: number;
  capture_digest: string;
  explicitly_empty: boolean;
  coverage: SourceCoverage[];
  errors: string[];
  dispositions: KnowledgeDisposition[];
  category_counts: Record<string, number>;
  disposition_counts: Record<string, number>;
  eligible_count: number;
  inventory: {
    reference_id: string;
    category: string;
    sha256: string;
    relative_path?: string | null;
    metadata?: Record<string, unknown> | null;
  }[];
  item_inventory: string[];
}

export interface KnowledgeArchive {
  archive_id: string;
  org_slug: string;
  source_id: string;
  version: number;
  operation_id: string;
  created_at: string;
  manifest: KnowledgeArchiveManifest;
  items: CuratedKnowledgeItem[];
}

export interface KnowledgeArchiveSelection {
  archive_id: string;
  version: number;
  operation_id: string;
  source_id: string;
  source_name: string;
  source_team: string;
  source_provider: string;
  created_at: string;
  cutoff: string;
  processor_id: string;
  item_count: number;
  eligible_count: number;
  coverage: SourceCoverage[];
}

export const distillKnowledge = (slug: string, submission: DistillSubmission): Promise<DistillOperation> =>
  request(`/orgs/${encodeURIComponent(slug)}/knowledge/distill`, { method: 'POST', body: submission });

export const getKnowledgeDistill = (slug: string, operationId: string): Promise<DistillOperation> =>
  request(`/orgs/${encodeURIComponent(slug)}/knowledge/distill/${encodeURIComponent(operationId)}`);

export const listKnowledgeArchives = (slug: string): Promise<{ archives: KnowledgeArchiveSelection[] }> =>
  request(`/orgs/${encodeURIComponent(slug)}/knowledge/archives`);

export const getKnowledgeArchive = (slug: string, archiveId: string): Promise<KnowledgeArchive> =>
  request(`/orgs/${encodeURIComponent(slug)}/knowledge/archives/${encodeURIComponent(archiveId)}`);
