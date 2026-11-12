import { useEffect, useRef, useMemo, useSyncExternalStore } from 'react';
import { useParams } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient, type QueryClient } from '@tanstack/react-query';
import { workflowActivations, workflowSubmissions, workflowReviews, workflowRevisions, workflowSequential } from '@/lib/api';
import { clearSelectionCommands, ReviewIntegrityError, validateDetail, type ReviewDetail } from '@/lib/workflowReviewSelection';
import type { WorkflowsApi, SequentialSettingsFence } from './DataContext';

/** No locale input, no persistent cache, no stale cached admission on remount. */
function useActivations() {
  const { slug = '' } = useParams();
  const query = useQuery({
    queryKey: ['workflow-activations', slug],
    queryFn: ({ signal }) => workflowActivations.listWorkflowActivations(slug, signal),
    enabled: !!slug, staleTime: 0, gcTime: 0, retry: false, refetchOnMount: 'always',
  });
  return { ...query, data: query.isFetchedAfterMount && !query.isError ? query.data : undefined };
}
function status(error: unknown): number | undefined { return (error as { status?: number } | null)?.status; }
function useReviewDetail(activationId: string, history: boolean): ReturnType<WorkflowsApi['useReviewDetail']> {
  const { slug = '' } = useParams();
  const qc = useQueryClient();
  const readEpoch = useRef(0);
  const key = ['workflow-detail', slug, activationId, history ? 'history' : 'current'] as const;
  const query = useQuery<ReviewDetail>({
    queryKey: key, enabled: !!slug && !!activationId,
    staleTime: 0, gcTime: 0, retry: false, refetchOnMount: 'always', refetchOnWindowFocus: 'always',
    refetchIntervalInBackground: false,
    refetchInterval: q => {
      if (q.state.error || !q.state.data) return false;
      const d = q.state.data;
      const col = d.revision?.current.collection ?? d.selection.original?.collection;
      return col?.state === 'approved' && col.validity === 'current' && col.approved_use ? false : 5000;
    },
    queryFn: async ({ signal }) => {
      ++readEpoch.current; // Synchronous read-start fence, independent of React notifications.
      // Child keys retain the full selected lane/object. Bundle abort applies to every actual request.
      async function read<T>(kind: string, object: string, lane: string, fn: () => Promise<T>): Promise<T> {
        return qc.fetchQuery({ queryKey: [kind, slug, activationId, object, lane], queryFn: fn, staleTime: 0, gcTime: 0, retry: false });
      }
      try {
        const activation = await read('workflow-activation', activationId, 'owner-human', () => workflowActivations.getWorkflowActivation(slug, activationId, signal));
        if (activation.activation_id !== activationId) throw new ReviewIntegrityError('workflow_review_integrity');
        const selection = await read('workflow-review-selection', activationId, 'owner-human', () => workflowActivations.getOwnerReviewSelection(slug, activationId, signal));
        await validateDetail({ slug, activation, selection, handoff: null, revision: null, request: null, history });
        const handoff = selection.original ? await read('workflow-handoff', selection.original.handoff_id, 'owner-human', () =>
          workflowSubmissions.getAuthorHandoff(slug, selection.original!.handoff_id, { proof_kind: 'owner-human' }, signal)) : null;
        const revision = selection.successor ? await read('workflow-revision', selection.successor.revision_operation_id, 'owner-human', () =>
          workflowRevisions.getDocumentRevision(slug, selection.successor!.revision_operation_id, { proof_kind: 'owner-human' }, signal)) : null;
        await validateDetail({ slug, activation, selection, handoff, revision, request: null, history });
        const requestId = history || !selection.successor ? selection.original?.human_request_id : selection.successor.human_request_id;
        const graph = history || !selection.successor ? handoff?.current.graph : revision?.current.graph;
        if (requestId && !graph?.request_ids.includes(requestId)) throw new ReviewIntegrityError('workflow_review_integrity');
        const request = requestId ? await read('workflow-human-request', requestId, 'human', () =>
          workflowReviews.getWorkflowReviewRequest(slug, requestId, { proof_kind: 'human' }, signal)) : null;
        const detail = await validateDetail({ slug, activation, selection, handoff, revision, request, history });
        if (signal.aborted) throw new DOMException('Aborted', 'AbortError');
        return detail;
      } catch (error) {
        if (status(error) === 403 || status(error) === 404) {
          qc.removeQueries({ predicate: q => q.queryKey[0] !== 'workflow-detail' && q.queryKey[1] === slug && q.queryKey[2] === activationId && String(q.queryKey[0]).startsWith('workflow-') });
          try { clearSelectionCommands(slug, activationId); } catch { /* Refusal still hides content if storage is unavailable. */ }
        }
        throw error;
      }
    },
  });
  useEffect(() => {
    const ownedEpoch = readEpoch;
    const ownedKey = ['workflow-detail', slug, activationId, history ? 'history' : 'current'];
    const fresh = () => { if (document.visibilityState === 'visible') void qc.invalidateQueries({ queryKey: ownedKey, exact: true }); };
    document.addEventListener('visibilitychange', fresh);
    return () => {
      ++ownedEpoch.current;
      document.removeEventListener('visibilitychange', fresh);
      void qc.cancelQueries({ queryKey: ownedKey, exact: true });
      qc.removeQueries({ queryKey: ownedKey, exact: true });
      qc.removeQueries({ predicate: q => q.queryKey[1] === slug && q.queryKey[2] === activationId && String(q.queryKey[0]).startsWith('workflow-') && q.queryKey[0] !== 'workflow-detail' });
    };
  }, [qc, slug, activationId, history]);
  const captureAdmission = (detail: ReviewDetail) => {
    const epoch = readEpoch.current, snapshot = JSON.stringify(detail);
    return () => {
      const state = qc.getQueryState<ReviewDetail>(key);
      return epoch === readEpoch.current && state?.fetchStatus === 'idle' && state.status === 'success' &&
        !state.error && !state.isInvalidated && JSON.stringify(state.data) === snapshot;
    };
  };
  return { ...query, captureAdmission, data: query.isFetchedAfterMount && !query.isError ? query.data : undefined };
}
function useMutationRefusal() {
  const qc = useQueryClient();
  return (error: unknown, { slug, activationId }: { slug: string; activationId: string }) => {
    if (status(error) !== 403 && status(error) !== 404) return;
    qc.removeQueries({ predicate: q => q.queryKey[1] === slug && q.queryKey[2] === activationId && String(q.queryKey[0]).startsWith('workflow-') });
    try { clearSelectionCommands(slug, activationId); } catch { /* UI also fails closed on refusal. */ }
  };
}
function useHumanResponse() {
  const onError = useMutationRefusal();
  return useMutation({ retry: false, onError, mutationFn: ({ slug, body }: Parameters<ReturnType<WorkflowsApi['useHumanResponse']>['mutateAsync']>[0]) => workflowReviews.signHumanReview(slug, body) });
}
function useOwnerReturn() {
  const onError = useMutationRefusal();
  return useMutation({ retry: false, onError, mutationFn: ({ slug, body }: Parameters<ReturnType<WorkflowsApi['useOwnerReturn']>['mutateAsync']>[0]) => workflowRevisions.startDocumentRevision(slug, body.instance_id, body) });
}
/** Selection changes/refusals hide old data; cache never supplies fresh access. */
function useSequentialRead<T>(kind: string, id: string, load: (slug: string, signal: AbortSignal) => Promise<T>) {
  const { slug = '' } = useParams();
  const qc = useQueryClient();
  const key = ['workflow-sequential', slug, kind, id];
  const query = useQuery({ queryKey: key, queryFn: async ({ signal }) => {
    try { return await load(slug, signal); } catch (error) {
      // U8a: a refused read keeps no earlier run/evidence bytes in this cache entry.
      if (status(error) === 403 || status(error) === 404) {
        const owned = qc.getQueryCache().find<T>({ queryKey: key, exact: true });
        owned?.setState({ ...owned.state, data: undefined, dataUpdatedAt: 0 });
      }
      throw error;
    }
  },
    enabled: !!slug && !!id, staleTime: 0, gcTime: 0, retry: false,
    refetchOnMount: 'always', refetchOnWindowFocus: 'always', placeholderData: undefined });
  useEffect(() => () => {
    const ownedKey = ['workflow-sequential', slug, kind, id];
    void qc.cancelQueries({ queryKey: ownedKey, exact: true });
    qc.removeQueries({ queryKey: ownedKey, exact: true });
  }, [qc, slug, kind, id]);
  return { ...query, data: query.isFetchedAfterMount && !query.isError && !query.isFetching ? query.data : undefined };
}
const sequentialReads = {
  useSequentialDefinitions: () => useSequentialRead('definitions', 'catalog', workflowSequential.listSequentialDefinitions),
  useSequentialDefinition: (id: string) => useSequentialRead('definition', id, (slug, signal) => workflowSequential.getSequentialDefinition(slug, id, signal)),
  useSequentialSettings: (id: string) => useSequentialRead('settings', id, (slug, signal) => workflowSequential.getSequentialSettings(slug, id, signal)),
  useSequentialRuns: () => useSequentialRead('runs', 'all', workflowSequential.listSequentialRuns),
  useSequentialRun: (id: string) => useSequentialRead('run', id, (slug, signal) => workflowSequential.getSequentialRun(slug, id, signal)),
  useTaskSequentialWorkflow: (id: string) => useSequentialRead('task', id, (slug, signal) => workflowSequential.getTaskSequentialWorkflow(slug, id, signal)),
};
export const realWorkflowsApi: WorkflowsApi = { useSequentialSettingsActions, useSequentialSettingsFence, ...sequentialReads, useActivations, useReviewDetail, useHumanResponse, useOwnerReturn };


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
        const value = await workflowSequential.getSequentialSettingsOperation(slug, key);
        const fence = store.fences.get(slug);
        // 404/error/foreign/mismatched receipts keep original custody. Reading
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
