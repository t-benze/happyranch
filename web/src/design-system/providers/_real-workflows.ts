import { useEffect, useMemo, useSyncExternalStore } from 'react';
import { useParams } from 'react-router-dom';
import { useQuery, useQueryClient, type QueryClient } from '@tanstack/react-query';
import { workflowSequential } from '@/lib/api';
import type { WorkflowsApi, SequentialSettingsFence } from './DataContext';

function status(error: unknown): number | undefined { return (error as { status?: number } | null)?.status; }
/** Fixed refusal published on every sibling projection fenced by an org's access refusal (AC11). */
class SequentialAccessFenced extends Error {
  readonly status = 403;
  readonly code = 'sequential_access_fenced';
  constructor() { super('sequential_access_fenced'); }
}
/** Per-QueryClient, per-org admission generation plus the org's in-flight sequential reads. */
interface SequentialAdmission { generation: Map<string, number>; inflight: Map<string, Set<AbortController>> }
const sequentialAdmissions = new WeakMap<QueryClient, SequentialAdmission>();
function admissionStore(qc: QueryClient): SequentialAdmission {
  let value = sequentialAdmissions.get(qc);
  if (!value) { value = { generation: new Map(), inflight: new Map() }; sequentialAdmissions.set(qc, value); }
  return value;
}
/**
 * A 403/404 on ANY sequential read refuses the whole org's sequential projections:
 * bump the generation (late sibling successes then throw instead of caching), abort
 * every in-flight read, and blank every cached entry. Siblings carry the fixed fence
 * refusal until a read started after this point succeeds (fresh admission).
 */
function refuseSequentialOrg(qc: QueryClient, slug: string, ownHash: string) {
  const store = admissionStore(qc);
  store.generation.set(slug, (store.generation.get(slug) ?? 0) + 1);
  for (const controller of store.inflight.get(slug) ?? []) controller.abort();
  for (const q of qc.getQueryCache().findAll({ queryKey: ['workflow-sequential', slug] })) {
    q.setState(q.queryHash === ownHash ? { ...q.state, data: undefined, dataUpdatedAt: 0 }
      : { ...q.state, data: undefined, dataUpdatedAt: 0, error: new SequentialAccessFenced(), errorUpdatedAt: Date.now(), status: 'error' });
  }
}
/** The settings GET's 404 when no generation was ever saved for this definition identity (U8b first setup). */
function isSettingsNotSaved(kind: string, error: unknown): boolean {
  return kind === 'settings' && status(error) === 404
    && (error as { code?: string | null } | null)?.code === 'sequential_settings_not_found';
}
/** The original-save lookup's 404 when that exact operation key was never committed (absent, not a refusal). */
function isOperationAbsent(error: unknown): boolean {
  return status(error) === 404 && (error as { code?: string | null } | null)?.code === 'sequential_settings_operation_not_found';
}
/**
 * Runs one non-cached sequential read under the org's shared admission policy:
 * it joins the org generation and abort set; a late result after a sibling
 * refusal or generation change throws the fixed fence refusal; its own 403/404
 * (other than the caller's exact positive-absence code) refuses the whole org.
 */
async function admittedSequentialRead<T>(qc: QueryClient, slug: string, absent: (error: unknown) => boolean,
  load: (signal: AbortSignal) => Promise<T>): Promise<T> {
  const store = admissionStore(qc);
  const generation = store.generation.get(slug) ?? 0;
  const controller = new AbortController();
  const inflight = store.inflight.get(slug) ?? new Set<AbortController>();
  store.inflight.set(slug, inflight.add(controller));
  const fenced = () => (store.generation.get(slug) ?? 0) !== generation;
  try {
    const value = await load(controller.signal);
    if (fenced()) throw new SequentialAccessFenced();
    return value;
  } catch (error) {
    if (fenced()) throw new SequentialAccessFenced();
    if (absent(error)) throw error;
    if (status(error) === 403 || status(error) === 404) refuseSequentialOrg(qc, slug, '');
    throw error;
  } finally {
    inflight.delete(controller);
  }
}
/** PRD F6: only a transport failure (no HTTP status) is a network refresh failure. */
function isNetworkFailure(error: unknown): boolean {
  return !!error && status(error) === undefined && (error as { name?: string }).name !== 'AbortError';
}
/**
 * Selection changes/refusals hide old data; cache never supplies fresh access.
 * A network refresh failure keeps this mount's last-loaded value with `refreshFailed`.
 */
function useSequentialRead<T>(kind: string, id: string, load: (slug: string, signal: AbortSignal) => Promise<T>) {
  const { slug = '' } = useParams();
  const qc = useQueryClient();
  const key = ['workflow-sequential', slug, kind, id];
  const query = useQuery({ queryKey: key, queryFn: async ({ signal }) => {
    const store = admissionStore(qc);
    const generation = store.generation.get(slug) ?? 0;
    const controller = new AbortController();
    const relay = () => controller.abort();
    signal.addEventListener('abort', relay);
    const inflight = store.inflight.get(slug) ?? new Set<AbortController>();
    store.inflight.set(slug, inflight.add(controller));
    const fenced = () => (store.generation.get(slug) ?? 0) !== generation;
    try {
      const value = await load(slug, controller.signal);
      if (fenced()) throw new SequentialAccessFenced();
      return value;
    } catch (error) {
      // U8b: "nothing saved yet" for this definition identity is a positive setup
      // fact, not an access refusal; it discloses nothing and must not fence the org.
      if (isSettingsNotSaved(kind, error)) {
        if (fenced()) throw new SequentialAccessFenced();
        throw error;
      }
      // U8a: a refused read keeps no run/evidence bytes in ANY sequential entry of this org.
      if (status(error) === 403 || status(error) === 404) {
        const owned = qc.getQueryCache().find<T>({ queryKey: key, exact: true });
        refuseSequentialOrg(qc, slug, owned?.queryHash ?? '');
        throw error;
      }
      if (fenced()) throw new SequentialAccessFenced();
      throw error;
    } finally {
      inflight.delete(controller);
      signal.removeEventListener('abort', relay);
    }
  },
    enabled: !!slug && !!id, staleTime: 0, gcTime: 0, retry: false,
    refetchOnMount: 'always', refetchOnWindowFocus: 'always', placeholderData: undefined });
  useEffect(() => () => {
    const ownedKey = ['workflow-sequential', slug, kind, id];
    void qc.cancelQueries({ queryKey: ownedKey, exact: true });
    qc.removeQueries({ queryKey: ownedKey, exact: true });
  }, [qc, slug, kind, id]);
  const loaded = query.isFetchedAfterMount && query.data !== undefined;
  const retained = loaded && query.isError && isNetworkFailure(query.error);
  return {
    ...query,
    isError: query.isError && !retained,
    data: loaded && (!query.isError || retained) ? query.data : undefined,
    refreshFailed: retained,
    refreshError: retained ? query.error : null,
  };
}
const sequentialReads = {
  useSequentialDefinitions: () => useSequentialRead('definitions', 'catalog', workflowSequential.listSequentialDefinitions),
  useSequentialDefinition: (id: string) => useSequentialRead('definition', id, (slug, signal) => workflowSequential.getSequentialDefinition(slug, id, signal)),
  useSequentialSettings: (id: string) => useSequentialRead('settings', id, (slug, signal) => workflowSequential.getSequentialSettings(slug, id, signal)),
  useSequentialRuns: () => useSequentialRead('runs', 'all', workflowSequential.listSequentialRuns),
  useSequentialRun: (id: string) => useSequentialRead('run', id, (slug, signal) => workflowSequential.getSequentialRun(slug, id, signal)),
  useTaskSequentialWorkflow: (id: string) => useSequentialRead('task', id, (slug, signal) => workflowSequential.getTaskSequentialWorkflow(slug, id, signal)),
};
export const realWorkflowsApi: WorkflowsApi = { useSequentialSettingsActions, useSequentialSettingsFence, ...sequentialReads };


interface SequentialSendStore {
  fences: Map<string, SequentialSettingsFence>;
  listeners: Set<() => void>;
}
const sequentialSends = new WeakMap<QueryClient, SequentialSendStore>();
function sendStore(qc: QueryClient): SequentialSendStore {
  let value = sequentialSends.get(qc);
  if (!value) { value = { fences: new Map(), listeners: new Set() }; sequentialSends.set(qc, value); }
  return value;
}
function setFence(store: SequentialSendStore, slug: string, value: SequentialSettingsFence | null) {
  if (value) store.fences.set(slug, value); else store.fences.delete(slug);
  store.listeners.forEach(listener => listener());
}
function useSequentialSettingsFence(): SequentialSettingsFence | null {
  const { slug = '' } = useParams();
  const store = sendStore(useQueryClient());
  return useSyncExternalStore(listener => { store.listeners.add(listener); return () => { store.listeners.delete(listener); }; },
    () => store.fences.get(slug) ?? null);
}
function useSequentialSettingsActions(): ReturnType<WorkflowsApi['useSequentialSettingsActions']> {
  const { slug = '' } = useParams();
  const qc = useQueryClient();
  return useMemo(() => {
    const store = sendStore(qc);
    return {
      save: async (draft: workflowSequential.SequentialSettingsCommand) => {
        if (!slug || store.fences.has(slug)) throw new Error('sequential_settings_send_in_progress');
        const command = workflowSequential.freezeSequentialSettingsCommand(draft);
        setFence(store, slug, { command, phase: 'sending' });
        try {
          const saved = await workflowSequential.putSequentialSettings(slug, command);
          // A receipt is original history, never current settings cache data.
          setFence(store, slug, null);
          void qc.invalidateQueries({ queryKey: ['workflow-sequential', slug, 'settings'] });
          return saved;
        } catch (error) {
          const code = (error as { code?: string } | null)?.code;
          const definite = ['sequential_settings_request_invalid', 'sequential_settings_generation_stale',
            'sequential_settings_operation_conflict', 'sequential_definition_not_found',
            'sequential_definition_conflict', 'sequential_settings_defaults_invalid', 'sequential_authority_denied'];
          setFence(store, slug, code && definite.includes(code) ? null : { command, phase: 'uncertain' });
          throw error;
        }
      },
      readOperation: async (key: string) => {
        // Same admission policy as the shared sequential reads (AC11): a 403/404
        // refusal fences the org; a late receipt after a sibling refusal or
        // generation change throws the fence refusal and never releases custody.
        const value = await admittedSequentialRead(qc, slug, isOperationAbsent,
          signal => workflowSequential.getSequentialSettingsOperation(slug, key, signal));
        const fence = store.fences.get(slug);
        // 404/error/refusal/foreign/mismatched receipts keep original custody. Reading
        // while PUT is pending cannot release its synchronous send ownership.
        if (fence?.phase === 'uncertain' && fence.command.operation_key === key
          && JSON.stringify(workflowSequential.freezeSequentialSettingsCommand(value.command)) === JSON.stringify(fence.command)) {
          setFence(store, slug, null);
          void qc.invalidateQueries({ queryKey: ['workflow-sequential', slug, 'settings'] });
        }
        return value;
      },
    };
  }, [qc, slug]);
}
