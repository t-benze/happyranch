/** Mirror of runtime/daemon/routes/knowledge.py. Read-only, no curation/import. */
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
