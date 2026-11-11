/** Sequential Founder reads/settings mirror; existing bearer, no start. */
import { request } from './client';
import { getToken } from '../auth';

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

export interface SequentialSettingsCommand {
  format: 'workflow-sequential-settings-put@1'; operation_key: string; expected_generation: number;
  definition: DefinitionPin; bindings: SequentialBinding[]; limits: SequentialLimits;
}
export interface SequentialSettingsReceipt {
  format: 'workflow-sequential-settings-receipt@1'; org_slug: string; operation_key: string;
  command: SequentialSettingsCommand;
  receipt: { format: 'workflow-sequential-settings-receipt-storage@1'; operation_id: string;
    settings_id: string; generation: number; snapshot_digest: string };
  original: SequentialSettings;
}

/** Closed commands have ASCII keys and integer numbers. Freeze exact original
 * bytes before awaiting token/transport; never normalize an uncertain resend. */
function canonical(value: unknown): unknown {
  if (typeof value === 'number') {
    if (!Number.isSafeInteger(value) || Object.is(value, -0)) throw new TypeError('sequential_settings_integer');
    return value;
  }
  if (typeof value === 'string') {
    if (/[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]/u.test(value)) throw new TypeError('sequential_settings_unicode');
    return value;
  }
  if (value === null || typeof value === 'boolean') return value;
  if (Array.isArray(value)) return Object.freeze(value.map(canonical));
  if (typeof value !== 'object' || Object.getPrototypeOf(value) !== Object.prototype) throw new TypeError('sequential_settings_json');
  return Object.freeze(Object.fromEntries(Object.keys(value).sort().map(key => [key, canonical((value as Record<string, unknown>)[key])])));
}
export function freezeSequentialSettingsCommand(command: SequentialSettingsCommand): SequentialSettingsCommand {
  const copy = canonical(command) as SequentialSettingsCommand;
  if (new TextEncoder().encode(JSON.stringify(copy)).length > 16384) throw new TypeError('sequential_settings_size');
  return copy;
}
function receipt(value: SequentialSettingsReceipt, slug: string, key: string): SequentialSettingsReceipt {
  canonical(value);
  if (value.format !== 'workflow-sequential-settings-receipt@1' || value.org_slug !== slug
    || value.operation_key !== key || value.command.operation_key !== key
    || value.receipt.settings_id !== value.original.id || value.receipt.generation !== value.original.generation
    || value.receipt.snapshot_digest !== value.original.snapshot_digest) throw new TypeError('sequential_settings_receipt');
  return value;
}
/** One PUT, including on 401. Uses the existing Founder token custody. */
export const putSequentialSettings = async (slug: string, command: SequentialSettingsCommand): Promise<SequentialSettingsReceipt> => {
  const body = freezeSequentialSettingsCommand(command);
  const saved = await request<SequentialSettingsReceipt>(`/orgs/${slug}/workflows/sequential/settings`, {
    method: 'PUT', body, auth: { token: await getToken() },
  });
  const value = receipt(saved, slug, body.operation_key);
  if (JSON.stringify(canonical(value.command)) !== JSON.stringify(body)) throw new TypeError('sequential_settings_original_command');
  return value;
};
/** Original receipt only. Never substitutes the latest current-generation GET. */
export const getSequentialSettingsOperation = async (slug: string, key: string): Promise<SequentialSettingsReceipt> =>
  receipt(await request(`/orgs/${slug}/workflows/sequential/settings/operations`, { params: { operation_key: key } }), slug, key);
