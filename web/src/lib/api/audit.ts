/** Mirror of runtime/daemon/routes/audit.py */
import { request } from './client';
import type { AuditEntry } from './types';

export type { AuditEntry } from './types';

export type MemoryCollectionPhase = 'intent' | 'identity' | 'expectation' | 'binding' | 'launched' | 'terminal';
export interface MemoryCollectionFileIdentity { path: string; sha256: string }
export interface MemoryCollectionIdentity {
  source_root: string;
  runtime_root: string;
  org_root: string;
  package_version: string;
  python: { executable: string; version: string; implementation: string; cache_tag: string | null };
  loaded_code: { module: string; qualname: string; origin: string; loaded_sha256: string; source_code_sha256: string }[];
  files: MemoryCollectionFileIdentity[];
  teams_sha256: string;
  cohort: { agent: string; team: string; role: 'manager' | 'worker'; executor: string; model: string | null }[];
  profiles: {
    name: string;
    kind: 'builtin' | 'custom';
    workspace_adapter_id: string;
    command_adapter_id: string | null;
    readiness_marker_fragment: string;
    model_arg_sha256: string;
    provider: MemoryCollectionFileIdentity | null;
    adapter: {
      id: string;
      version: string;
      contract_version: number;
      dependency_manifest_version: number | null;
      dependencies: MemoryCollectionFileIdentity[];
    } | null;
  }[];
  backend: {
    mode: 'legacy' | 'supervised';
    name: string | null;
    version: string | null;
    capabilities: Record<string, 'guaranteed' | 'best_effort' | 'unavailable'> | null;
  };
}
/** Serving evidence only. Unknown components are null; there is no health override. */
interface MemoryCollectionObservationBase {
  contract_version: 1;
  org: string;
  observation_error: string | null;
  latest_seal_audit_id: number | null;
  epoch_id: null;
  epoch_audit_id: null;
  sampled_at: string;
  data_through: string | null;
}
export type MemoryCollectionObservation = MemoryCollectionObservationBase & ({
  boot_id: null;
  installed_identity: null;
  generation: null;
  assigned_intents: null;
  intent_digest: null;
  phase_counts: null;
  phase_digests: null;
  active_preparations: null;
} | {
  boot_id: string;
  installed_identity: MemoryCollectionIdentity | null;
  generation: number;
  assigned_intents: number;
  intent_digest: string;
  phase_counts: Record<MemoryCollectionPhase, { attempted: number; persisted: number }>;
  phase_digests: Record<MemoryCollectionPhase, { attempted: string; persisted: string }>;
  active_preparations: { ordinal: number; task_id: string; agent: string; session_id: string | null; expectation_known: boolean }[];
});
export interface AuditResponse {
  entries: AuditEntry[];
  next_cursor?: string | null;
  /** Optional on seal-action GET only; absence supports older daemons. */
  memory_collection_observation?: MemoryCollectionObservation | null;
}

export const listAudit = (
  slug: string,
  params?: {
    task_id?: string;
    agent?: string;
    action?: string;
    since?: string;
    limit?: number;
    /** Opaque keyset cursor from a prior response's `next_cursor`. Omit for
     *  the first page; the daemon AND-composes it with all other filters. */
    cursor?: string;
    /** Enrich Thread-scope entries with _thread_dream_id (A4 marker). */
    include_thread_origin?: boolean;
  },
): Promise<AuditResponse> =>
  request(`/orgs/${slug}/audit`, {
    params: { ...params, include_thread_origin: params?.include_thread_origin ?? true },
  });
