/** U7a mirror of workflow_sequential.py. Existing bearer, read-only; no start. */
import { request } from './client';

export interface DefinitionPin {
  identity: string; version: number; definition_digest: string; source_digest: string;
  compiler: 'workflow-sequential-compiler@1'; validator: 'workflow-sequential-validator@1';
  interpreter: 'workflow-sequential-interpreter@1'; capabilities: 'workflow-sequential-capabilities@1';
}
export interface SequentialLimits {
  content_returns: number; max_content_epochs: number; max_visits: number; max_events: number;
}
export interface SequentialBinding { role: string; principal: string }
export interface SequentialDefinition { id: string; pin: DefinitionPin; definition_json: string }
export interface SequentialDefinitions {
  format: 'workflow-sequential-definitions-view@1';
  storage: 'new-engine unavailable' | 'storage readable'; definitions: SequentialDefinition[];
}
export interface SequentialSettings {
  format: 'workflow-sequential-settings-view@1'; id: string; generation: number; snapshot_digest: string;
  snapshot: { format: 'workflow-sequential-settings-storage@1'; definition: DefinitionPin;
    bindings: SequentialBinding[]; limits: SequentialLimits; admission: 'Disabled' | 'Allowed' };
}
export interface SequentialRun {
  id: string; definition_id: string; definition: DefinitionPin; settings_id: string;
  settings: { generation: number; snapshot_digest: string };
  state: 'active' | 'held' | 'completed' | 'failed' | 'cancelled'; active_visit_id: string | null;
  counters: { epoch: number; content_returns: number; visits: number; events: number };
  tasks: { task_id: string; relationship: 'source' | 'carrier' | 'visit'; visit_id: string | null }[];
}
export interface SequentialRuns {
  format: 'workflow-sequential-runs-view@1';
  storage: 'new-engine unavailable' | 'storage readable'; runs: SequentialRun[];
}
export interface SequentialRunDetail {
  format: 'workflow-sequential-run-view@1'; run: SequentialRun; request: string;
  bindings: SequentialBinding[]; limits: SequentialLimits;
  inputs: { name: string; contract: 'bounded-text@1'; value: string }[];
  holds: { visit_id: string | null; cause: string; next_owner: string; action: string }[];
  visits: { id: string; node_id: string; ordinal: number; epoch: number; task_id: string; principal: string;
    state: string; selected: boolean; outcome: string | null; findings: string[] }[];
  outputs: { id: string; visit_id: string; slot: string; contract: string; epoch: number;
    content_digest: string; content_utf8: string }[];
}
export interface SequentialTaskWorkflow {
  format: 'workflow-sequential-task-link@1'; task_id: string; run: SequentialRun | null;
}
export const listSequentialDefinitions = (slug: string, signal?: AbortSignal): Promise<SequentialDefinitions> =>
  request(`/orgs/${slug}/workflows/sequential/definitions`, { signal });
export const getSequentialDefinition = (slug: string, id: string, signal?: AbortSignal): Promise<SequentialDefinition> =>
  request(`/orgs/${slug}/workflows/sequential/definitions/${encodeURIComponent(id)}`, { signal });
export const getSequentialSettings = (slug: string, identity: string, signal?: AbortSignal): Promise<SequentialSettings> =>
  request(`/orgs/${slug}/workflows/sequential/settings`, { params: { definition_identity: identity }, signal });
export const listSequentialRuns = (slug: string, signal?: AbortSignal): Promise<SequentialRuns> =>
  request(`/orgs/${slug}/workflows/sequential/runs`, { signal });
export const getSequentialRun = (slug: string, id: string, signal?: AbortSignal): Promise<SequentialRunDetail> =>
  request(`/orgs/${slug}/workflows/sequential/runs/${encodeURIComponent(id)}`, { signal });
export const getTaskSequentialWorkflow = (slug: string, id: string, signal?: AbortSignal): Promise<SequentialTaskWorkflow> =>
  request(`/orgs/${slug}/tasks/${encodeURIComponent(id)}/sequential-workflow`, { signal });
