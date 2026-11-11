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
 * returns the original operation; it never starts a second one). If the ledger
 * cannot be read or the key cannot be durably written, nothing is sent; while
 * any recorded attempt is unknown or unconfirmed, no new key is minted.
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

/** Ledger read: `ok=false` when storage is unreadable or any stored value is malformed (an empty string included). */
export interface LedgerSnapshot { ok: boolean; attempts: DistillAttempt[] }
const UNREADABLE = Symbol('unreadable');
const snapshots = new Map<string, { raw: string | null | typeof UNREADABLE; value: LedgerSnapshot }>();

function readLedger(key: string): LedgerSnapshot {
  let raw: string | null | typeof UNREADABLE;
  try { raw = window.localStorage.getItem(key); } catch { raw = UNREADABLE; }
  const cached = snapshots.get(key);
  if (cached && cached.raw === raw) return cached.value;
  let value: LedgerSnapshot = { ok: false, attempts: [] };
  if (raw !== UNREADABLE) {
    try {
      // Only a genuinely absent key is an empty ledger; every present string
      // (including '') must parse and validate, else it is unreadable.
      const parsed: unknown = raw === null ? [] : JSON.parse(raw);
      if (Array.isArray(parsed)) {
        const attempts = parsed.filter((entry): entry is DistillAttempt =>
          !!entry && typeof entry.submission_id === 'string'
          && (entry.operation_id === null || typeof entry.operation_id === 'string')
          && typeof entry.submitted_at === 'string');
        // A dropped entry could be an unresolved submission: treat as unreadable.
        value = { ok: attempts.length === parsed.length, attempts };
      }
    } catch { /* malformed: unreadable */ }
  }
  snapshots.set(key, { raw, value });
  return value;
}

/**
 * Persist the ledger and prove it by reading the exact bytes back. Returns false
 * when storage refuses or does not hold the write (quota, disabled storage).
 * Trimming never drops an unresolved submission (operation_id null).
 */
function writeLedger(key: string, next: DistillAttempt[]): boolean {
  let kept = next;
  if (kept.length > LEDGER_LIMIT) {
    const excess = kept.length - LEDGER_LIMIT;
    let dropped = 0;
    kept = kept.filter((entry) => {
      if (dropped < excess && entry.operation_id !== null) { dropped += 1; return false; }
      return true;
    });
  }
  const serialized = JSON.stringify(kept);
  let ok = false;
  try {
    window.localStorage.setItem(key, serialized);
    ok = window.localStorage.getItem(key) === serialized;
  } catch { ok = false; }
  listeners.forEach((listener) => listener());
  return ok;
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
  const snapshot = useSyncExternalStore(subscribe, () => (key ? readLedger(key) : EMPTY));
  const forget = useCallback((submissionId: string) => {
    if (!key) return;
    writeLedger(key, readLedger(key).attempts.filter((entry) => entry.submission_id !== submissionId));
  }, [key]);
  return { attempts: snapshot.attempts, ledgerReadable: snapshot.ok, forget };
}
const EMPTY: LedgerSnapshot = { ok: true, attempts: [] };

function newSubmissionId(): string {
  const random = typeof crypto !== 'undefined' && 'randomUUID' in crypto
    ? crypto.randomUUID().replace(/-/g, '')
    : `${Date.now().toString(16)}${Math.random().toString(16).slice(2)}`;
  return `web-${random}`;
}

/**
 * Local refusal before or after the Distill POST. `ledger_unavailable` and
 * `ledger_write_failed` refuse BEFORE any POST; `unresolved_attempt` refuses a
 * fresh key while an earlier attempt is unknown or unconfirmed;
 * `ledger_mapping_failed` means the POST answered but its operation ID could not
 * be saved — the submission ID stays recorded for a same-key "Check again".
 * `recovery_refused` means the daemon refused a same-key recovery request
 * (`refusal` carries its code); that does not settle the ORIGINAL submission,
 * so its key stays recorded and new starts stay blocked.
 */
export type DistillLedgerCode = 'ledger_unavailable' | 'ledger_write_failed' | 'unresolved_attempt' | 'ledger_mapping_failed' | 'recovery_refused';
export class DistillLedgerError extends Error {
  constructor(readonly code: DistillLedgerCode, readonly operationId: string | null = null, readonly refusal: string | null = null) {
    super(code);
    this.name = 'DistillLedgerError';
  }
}

/** Final responses to a first send that prove no operation was created for its key. */
const NO_OPERATION_STATUSES = new Set([400, 401, 403, 404, 409, 422]);

/**
 * Confirmed Distill submission. `submissionId` re-sends an earlier unknown
 * outcome with its original key. Omitted, a new key is minted only when every
 * recorded attempt has a confirmed terminal state, and only after that key is
 * durably recorded; otherwise nothing is sent.
 */
export function useStartDistill(sourceId: string) {
  const slug = useSlug();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ submissionId }: { submissionId?: string }) => {
      const key = ledgerKey(slug, sourceId);
      const ledger = readLedger(key);
      if (!ledger.ok) throw new DistillLedgerError('ledger_unavailable');
      const submission_id = submissionId ?? newSubmissionId();
      if (submissionId) {
        // Recovery re-sends only a key this browser actually recorded.
        if (!ledger.attempts.some((entry) => entry.submission_id === submissionId)) {
          throw new DistillLedgerError('ledger_unavailable');
        }
      } else {
        const unresolved = ledger.attempts.some((entry) => {
          if (entry.operation_id === null) return true;
          const op = queryClient.getQueryData<api.DistillOperation>(['knowledge', slug, 'distill', entry.operation_id]);
          return !op || IN_FLIGHT.has(op.state);
        });
        if (unresolved) throw new DistillLedgerError('unresolved_attempt');
        const recorded = writeLedger(key, [...ledger.attempts, { submission_id, operation_id: null, submitted_at: new Date().toISOString() }]);
        if (!recorded || !readLedger(key).attempts.some((entry) => entry.submission_id === submission_id)) {
          throw new DistillLedgerError('ledger_write_failed');
        }
      }
      let operation: api.DistillOperation;
      try {
        operation = await api.distillKnowledge(slug, { submission_id, source_id: sourceId, confirmed: true });
      } catch (error) {
        const status = (error as { status?: number }).status;
        const refused = typeof status === 'number' && status >= 400 && status < 500;
        if (submissionId) {
          // Recovery of an earlier unknown submission: only a returned operation
          // resolves it. A refusal of THIS request proves nothing about the
          // original, so its key always stays recorded (new starts stay blocked).
          if (refused) {
            const code = (error as { code?: string | null }).code;
            throw new DistillLedgerError('recovery_refused', null, code ?? `http_${status}`);
          }
          throw error;
        }
        // First send of a freshly minted key: forget it only for refusals the
        // daemon returns before or instead of creating an operation (bearer
        // auth, request validation, source/archive refusal). Anything else
        // (other 4xx, 5xx, transport loss) keeps the key for a same-key check.
        if (typeof status === 'number' && NO_OPERATION_STATUSES.has(status)) {
          writeLedger(key, readLedger(key).attempts.filter((entry) => entry.submission_id !== submission_id));
        }
        throw error;
      }
      queryClient.setQueryData(['knowledge', slug, 'distill', operation.operation_id], operation);
      const current = readLedger(key);
      const mapped = current.ok && writeLedger(key, current.attempts.map((entry) => (
        entry.submission_id === submission_id ? { ...entry, operation_id: operation.operation_id } : entry
      )));
      if (!mapped || !readLedger(key).attempts.some((entry) => entry.submission_id === submission_id && entry.operation_id === operation.operation_id)) {
        // The pending entry (same submission ID) remains for "Check again".
        throw new DistillLedgerError('ledger_mapping_failed', operation.operation_id);
      }
      return operation;
    },
    onSettled: () => {
      void queryClient.invalidateQueries({ queryKey: ['knowledge', slug, 'archives'] });
    },
  });
}
