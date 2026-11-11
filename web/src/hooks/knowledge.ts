/**
 * Retired-source knowledge hooks (THR-296 S4). Thin react-query forwarders over
 * the existing S1/S2 mirrors in `@/lib/api/knowledge`; no FE-only API.
 *
 * Reads only, except `useStartDistill` (the confirmed Distill POST). Operation
 * reads reconcile on the server; the client polls only while Queued/Processing.
 *
 * Leave-and-reopen: the S2 API has no per-source operation list, so this
 * browser keeps a small per-org/per-source ledger of its own submission IDs
 * and returned operation IDs. A submission ID is written BEFORE the POST so a
 * lost response can be explicitly re-sent with the SAME key (server replay
 * returns the original operation; it never starts a second one).
 */
import { useCallback, useEffect, useSyncExternalStore } from 'react';
import { useMutation, useQueries, useQuery, useQueryClient } from '@tanstack/react-query';
import { useParams } from 'react-router-dom';
import { useOrgSlugOptional } from '@/lib/orgSlug';
import * as api from '@/lib/api/knowledge';

export type {
  SourceCoverage,
  KnowledgeSourceRecord,
  RetiredKnowledgeSource,
  RetiredKnowledgeReference,
  DistillOperation,
  KnowledgeDisposition,
  CuratedKnowledgeItem,
  KnowledgeArchive,
  KnowledgeArchiveSelection,
} from '@/lib/api/knowledge';

function useSlug(): string {
  const { slug: routeSlug } = useParams<{ slug: string }>();
  const ctxSlug = useOrgSlugOptional();
  return routeSlug ?? ctxSlug ?? '';
}

const IN_FLIGHT = new Set<api.DistillOperation['state']>(['Queued', 'Processing']);

export function useRetiredKnowledgeSource(sourceId: string | null) {
  const slug = useSlug();
  return useQuery({
    queryKey: ['knowledge', slug, 'retired', sourceId],
    queryFn: () => api.getRetiredKnowledgeSource(slug, sourceId!),
    enabled: !!slug && !!sourceId,
    retry: false,
  });
}

export function useRetiredKnowledgeReference(sourceId: string | null, referenceId: string | null) {
  const slug = useSlug();
  return useQuery({
    queryKey: ['knowledge', slug, 'retired', sourceId, 'reference', referenceId],
    queryFn: () => api.getRetiredKnowledgeReference(slug, sourceId!, referenceId!),
    enabled: !!slug && !!sourceId && !!referenceId,
    retry: false,
  });
}

export function useKnowledgeArchives() {
  const slug = useSlug();
  return useQuery({
    queryKey: ['knowledge', slug, 'archives'],
    queryFn: () => api.listKnowledgeArchives(slug),
    enabled: !!slug,
    retry: false,
  });
}

export function useKnowledgeArchive(archiveId: string | null) {
  const slug = useSlug();
  return useQuery({
    queryKey: ['knowledge', slug, 'archive', archiveId],
    queryFn: () => api.getKnowledgeArchive(slug, archiveId!),
    enabled: !!slug && !!archiveId,
    // Ready archives are immutable; keep the payload once fetched.
    staleTime: Infinity,
    retry: false,
  });
}

const operationQuery = (slug: string, operationId: string) => ({
  queryKey: ['knowledge', slug, 'distill', operationId],
  queryFn: () => api.getKnowledgeDistill(slug, operationId),
  enabled: !!slug && !!operationId,
  retry: false,
  refetchInterval: (query: { state: { data?: api.DistillOperation } }) =>
    query.state.data && IN_FLIGHT.has(query.state.data.state) ? 4000 : false,
});

/** A terminal transition publishes (or fails) an archive: refresh the Ready list once. */
function useArchivesRefreshOn(slug: string, terminalKey: string) {
  const queryClient = useQueryClient();
  useEffect(() => {
    if (terminalKey) void queryClient.invalidateQueries({ queryKey: ['knowledge', slug, 'archives'] });
  }, [queryClient, slug, terminalKey]);
}

const terminal = (op: api.DistillOperation | undefined) =>
  op && !IN_FLIGHT.has(op.state) ? `${op.operation_id}:${op.state}` : '';

export function useDistillOperation(operationId: string | null) {
  const slug = useSlug();
  const query = useQuery({ ...operationQuery(slug, operationId ?? ''), enabled: !!slug && !!operationId });
  useArchivesRefreshOn(slug, terminal(query.data));
  return query;
}

export function useDistillOperations(operationIds: string[]) {
  const slug = useSlug();
  const queries = useQueries({ queries: operationIds.map((id) => operationQuery(slug, id)) });
  useArchivesRefreshOn(slug, queries.map((q) => terminal(q.data)).filter(Boolean).join(','));
  return queries;
}

// ---------------------------------------------------------------------------
// Per-browser attempt ledger (submission IDs and returned operation IDs).

export interface DistillAttempt {
  submission_id: string;
  operation_id: string | null;
  submitted_at: string;
}

const LEDGER_LIMIT = 20;
const listeners = new Set<() => void>();
const ledgerKey = (slug: string, sourceId: string) => `happyranch.knowledge.distill.${slug}.${sourceId}`;
const snapshots = new Map<string, { raw: string | null; value: DistillAttempt[] }>();

function readLedger(key: string): DistillAttempt[] {
  let raw: string | null = null;
  try { raw = window.localStorage.getItem(key); } catch { raw = null; }
  const cached = snapshots.get(key);
  if (cached && cached.raw === raw) return cached.value;
  let value: DistillAttempt[] = [];
  try {
    const parsed: unknown = raw ? JSON.parse(raw) : [];
    if (Array.isArray(parsed)) {
      value = parsed.filter((entry): entry is DistillAttempt =>
        !!entry && typeof entry.submission_id === 'string'
        && (entry.operation_id === null || typeof entry.operation_id === 'string')
        && typeof entry.submitted_at === 'string');
    }
  } catch { value = []; }
  snapshots.set(key, { raw, value });
  return value;
}

function writeLedger(key: string, next: DistillAttempt[]): void {
  try { window.localStorage.setItem(key, JSON.stringify(next.slice(-LEDGER_LIMIT))); } catch { /* storage unavailable */ }
  listeners.forEach((listener) => listener());
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  const onStorage = () => listener();
  window.addEventListener('storage', onStorage);
  return () => { listeners.delete(listener); window.removeEventListener('storage', onStorage); };
}

export function useDistillAttempts(sourceId: string | null) {
  const slug = useSlug();
  const key = sourceId && slug ? ledgerKey(slug, sourceId) : null;
  const attempts = useSyncExternalStore(subscribe, () => (key ? readLedger(key) : EMPTY));
  const forget = useCallback((submissionId: string) => {
    if (!key) return;
    writeLedger(key, readLedger(key).filter((entry) => entry.submission_id !== submissionId));
  }, [key]);
  return { attempts, forget };
}
const EMPTY: DistillAttempt[] = [];

function newSubmissionId(): string {
  const random = typeof crypto !== 'undefined' && 'randomUUID' in crypto
    ? crypto.randomUUID().replace(/-/g, '')
    : `${Date.now().toString(16)}${Math.random().toString(16).slice(2)}`;
  return `web-${random}`;
}

/**
 * Confirmed Distill submission. `submissionId` re-sends an earlier unknown
 * outcome with its original key; omitted, a new key is recorded before POST.
 */
export function useStartDistill(sourceId: string) {
  const slug = useSlug();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ submissionId }: { submissionId?: string }) => {
      const key = ledgerKey(slug, sourceId);
      const submission_id = submissionId ?? newSubmissionId();
      if (!submissionId) {
        writeLedger(key, [...readLedger(key), { submission_id, operation_id: null, submitted_at: new Date().toISOString() }]);
      }
      try {
        const operation = await api.distillKnowledge(slug, { submission_id, source_id: sourceId, confirmed: true });
        writeLedger(key, readLedger(key).map((entry) => (
          entry.submission_id === submission_id ? { ...entry, operation_id: operation.operation_id } : entry
        )));
        queryClient.setQueryData(['knowledge', slug, 'distill', operation.operation_id], operation);
        return operation;
      } catch (error) {
        // A definitive refusal created no operation; forget the key. Anything
        // else (transport loss, 5xx) keeps the key for an explicit same-key check.
        const status = (error as { status?: number }).status;
        if (typeof status === 'number' && status >= 400 && status < 500) {
          writeLedger(key, readLedger(key).filter((entry) => entry.submission_id !== submission_id));
        }
        throw error;
      }
    },
    onSettled: () => {
      void queryClient.invalidateQueries({ queryKey: ['knowledge', slug, 'archives'] });
    },
  });
}
