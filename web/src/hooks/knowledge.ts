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
import { useCallback, useEffect, useRef, useSyncExternalStore } from 'react';
import { useMutation, useQueries, useQuery, useQueryClient } from '@tanstack/react-query';
import { useParams } from 'react-router-dom';
import { useOrgSlugOptional } from '@/lib/orgSlug';
import * as api from '@/lib/api/knowledge';
import { getMemory } from '@/lib/api/agents';

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
  ImportOperation,
  ImportItemResult,
  ImportEligibility,
} from '@/lib/api/knowledge';
export type { MemoryEntry } from '@/lib/api/types';

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
  /**
   * The curator confirmed with this submission. A same-key re-send repeats it
   * exactly; absent on entries recorded before a curator could be chosen,
   * which re-send without one.
   */
  processor_id?: string | null;
}

const LEDGER_LIMIT = 20;
const listeners = new Set<() => void>();
const ledgerKey = (slug: string, sourceId: string) => `happyranch.knowledge.distill.${slug}.${sourceId}`;

/** Ledger read: `ok=false` when storage is unreadable or any stored value is malformed (an empty string included). */
export interface LedgerSnapshot { ok: boolean; attempts: DistillAttempt[] }
const UNREADABLE = Symbol('unreadable');
const snapshots = new Map<string, { raw: string | null | typeof UNREADABLE; value: LedgerSnapshot }>();

function isDistillAttempt(entry: unknown): entry is DistillAttempt {
  const value = entry as DistillAttempt | null;
  return !!value && typeof value.submission_id === 'string'
    && (value.operation_id === null || typeof value.operation_id === 'string')
    && typeof value.submitted_at === 'string'
    && (value.processor_id === undefined || value.processor_id === null || typeof value.processor_id === 'string');
}

/** `valid` is the per-ledger entry predicate (Distill attempts by default). */
function readLedger<T extends DistillAttempt = DistillAttempt>(
  key: string, valid: (entry: unknown) => boolean = isDistillAttempt,
): { ok: boolean; attempts: T[] } {
  let raw: string | null | typeof UNREADABLE;
  try { raw = window.localStorage.getItem(key); } catch { raw = UNREADABLE; }
  const cached = snapshots.get(key);
  if (cached && cached.raw === raw) return cached.value as { ok: boolean; attempts: T[] };
  let value: LedgerSnapshot = { ok: false, attempts: [] };
  if (raw !== UNREADABLE) {
    try {
      // Only a genuinely absent key is an empty ledger; every present string
      // (including '') must parse and validate, else it is unreadable.
      const parsed: unknown = raw === null ? [] : JSON.parse(raw);
      if (Array.isArray(parsed)) {
        const attempts = parsed.filter((entry): entry is DistillAttempt => valid(entry));
        // A dropped entry could be an unresolved submission: treat as unreadable.
        value = { ok: attempts.length === parsed.length, attempts };
      }
    } catch { /* malformed: unreadable */ }
  }
  snapshots.set(key, { raw, value });
  return value as { ok: boolean; attempts: T[] };
}

/**
 * Persist the ledger and prove it by reading the exact bytes back. Returns false
 * when storage refuses or does not hold the write (quota, disabled storage).
 * Trimming never drops an unresolved submission (operation_id null).
 */
function writeLedger<T extends DistillAttempt>(key: string, next: T[]): boolean {
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
 * outcome with its original key and the curator recorded with it. Omitted, a
 * new key is minted only when every recorded attempt has a confirmed terminal
 * state, and only after that key and the chosen curator (`processorId`) are
 * durably recorded; otherwise nothing is sent.
 */
export function useStartDistill(sourceId: string) {
  const slug = useSlug();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ submissionId, processorId }: { submissionId?: string; processorId?: string }) => {
      const key = ledgerKey(slug, sourceId);
      const ledger = readLedger(key);
      if (!ledger.ok) throw new DistillLedgerError('ledger_unavailable');
      const submission_id = submissionId ?? newSubmissionId();
      // The curator is part of the confirmation: a re-send uses the recorded one.
      let processor_id: string | null = processorId ?? null;
      if (submissionId) {
        // Recovery re-sends only a key this browser actually recorded.
        const recordedEntry = ledger.attempts.find((entry) => entry.submission_id === submissionId);
        if (!recordedEntry) throw new DistillLedgerError('ledger_unavailable');
        processor_id = recordedEntry.processor_id ?? null;
      } else {
        const unresolved = ledger.attempts.some((entry) => {
          if (entry.operation_id === null) return true;
          const op = queryClient.getQueryData<api.DistillOperation>(['knowledge', slug, 'distill', entry.operation_id]);
          return !op || IN_FLIGHT.has(op.state);
        });
        if (unresolved) throw new DistillLedgerError('unresolved_attempt');
        const recorded = writeLedger(key, [...ledger.attempts, {
          submission_id, operation_id: null, submitted_at: new Date().toISOString(), ...(processor_id ? { processor_id } : {}),
        }]);
        if (!recorded || !readLedger(key).attempts.some((entry) => entry.submission_id === submission_id)) {
          throw new DistillLedgerError('ledger_write_failed');
        }
      }
      let operation: api.DistillOperation;
      try {
        operation = await api.distillKnowledge(slug, {
          submission_id, source_id: sourceId, confirmed: true, ...(processor_id ? { processor_id } : {}),
        });
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

// ---------------------------------------------------------------------------
// Instill into an ACTIVE recipient (THR-296 S5). Thin forwarders over the S3b
// mirrors; the server owns eligibility, admission, item identity and receipts.

const IMPORT_IN_FLIGHT = new Set<api.ImportOperation['state']>(['Queued', 'Processing']);

/** Recipient import history (learned sources and receipt links). */
export function useKnowledgeImports(recipientId: string | null) {
  const slug = useSlug();
  return useQuery({
    queryKey: ['knowledge', slug, 'imports', recipientId],
    queryFn: () => api.listKnowledgeImports(slug, recipientId!),
    enabled: !!slug && !!recipientId,
    retry: false,
    refetchInterval: (query: { state: { data?: { operations: api.ImportOperation[] } } }) =>
      query.state.data?.operations.some((op) => IMPORT_IN_FLIGHT.has(op.state)) ? 4000 : false,
  });
}

export function useKnowledgeImport(operationId: string | null) {
  const slug = useSlug();
  return useQuery({
    queryKey: ['knowledge', slug, 'import', operationId],
    queryFn: () => api.getKnowledgeImport(slug, operationId!),
    enabled: !!slug && !!operationId,
    retry: false,
    refetchInterval: (query: { state: { data?: api.ImportOperation } }) =>
      query.state.data && IMPORT_IN_FLIGHT.has(query.state.data.state) ? 4000 : false,
  });
}

/** Advisory S3b projection: no reservation; confirm/retry revalidate on the server. */
export function useImportEligibility(recipientId: string | null, archiveId: string | null) {
  const slug = useSlug();
  return useQuery({
    queryKey: ['knowledge', slug, 'eligibility', recipientId, archiveId],
    queryFn: () => api.getKnowledgeImportEligibility(slug, recipientId!, archiveId!),
    enabled: !!slug && !!recipientId && !!archiveId,
    retry: false,
  });
}

/** One recipient memory note (existing memory read; returns inherited provenance). */
export function useAgentMemoryEntry(agentName: string | null, memoryId: string | null) {
  const slug = useSlug();
  return useQuery({
    queryKey: ['knowledge', slug, 'memory', agentName, memoryId],
    queryFn: () => getMemory(slug, agentName!, memoryId!),
    enabled: !!slug && !!agentName && !!memoryId,
    retry: false,
  });
}

function useImportSettled(slug: string, recipientId: string) {
  const queryClient = useQueryClient();
  return useCallback(() => {
    void queryClient.invalidateQueries({ queryKey: ['knowledge', slug, 'imports', recipientId] });
    void queryClient.invalidateQueries({ queryKey: ['knowledge', slug, 'eligibility', recipientId] });
    void queryClient.invalidateQueries({ queryKey: ['agent-learnings', slug, recipientId] });
  }, [queryClient, slug, recipientId]);
}

/**
 * Same-operation retry. The server rewrites only items it proves missing or
 * unwritten; held and successful items are never rewritten. Any failure
 * re-reads the operation so the next retry waits for authoritative state.
 */
export function useRetryImport(recipientId: string) {
  const slug = useSlug();
  const queryClient = useQueryClient();
  const settled = useImportSettled(slug, recipientId);
  return useMutation({
    mutationFn: (operationId: string) => api.retryKnowledgeImport(slug, operationId),
    onSuccess: (operation) => {
      queryClient.setQueryData(['knowledge', slug, 'import', operation.operation_id], operation);
    },
    onError: (_error, operationId) => {
      void queryClient.invalidateQueries({ queryKey: ['knowledge', slug, 'import', operationId] });
    },
    onSettled: settled,
  });
}

/**
 * Unknown-outcome confirmations for one recipient. Each entry carries the exact
 * confirmation so a lost response is re-sent byte-for-byte with its ORIGINAL
 * submission ID (the server returns the original operation and never imports
 * twice). An entry is removed only when an operation for that submission ID is
 * returned (POST response or the recipient's import history). Same storage
 * rules as Distill: only an absent value is empty; malformed, '' or unwritable
 * storage refuses with zero requests.
 */
export interface InstillAttempt extends DistillAttempt {
  recipient_id: string;
  archive_id: string;
  archive_version: number;
  item_ids: string[];
}

const instillKey = (slug: string, recipientId: string) => `happyranch.knowledge.instill.${slug}.${recipientId}`;

function isInstillAttempt(entry: unknown): boolean {
  const value = entry as InstillAttempt | null;
  return isDistillAttempt(entry) && value!.operation_id === null && typeof value!.recipient_id === 'string'
    && typeof value!.archive_id === 'string' && Number.isInteger(value!.archive_version)
    && Array.isArray(value!.item_ids) && value!.item_ids.length > 0
    && value!.item_ids.every((id) => typeof id === 'string');
}

const EMPTY_INSTILL: { ok: boolean; attempts: InstillAttempt[] } = { ok: true, attempts: [] };

export function useInstillAttempts(recipientId: string | null) {
  const slug = useSlug();
  const key = recipientId && slug ? instillKey(slug, recipientId) : null;
  const snapshot = useSyncExternalStore(subscribe, () => (key ? readLedger<InstillAttempt>(key, isInstillAttempt) : EMPTY_INSTILL));
  const history = useKnowledgeImports(recipientId);
  // Settle from authoritative history only: a returned operation carrying the
  // recorded submission ID. A read never settles anything by absence.
  const operations = history.data?.operations;
  useEffect(() => {
    if (!key || !operations || !snapshot.ok || snapshot.attempts.length === 0) return;
    const found = new Set(operations.map((op) => op.submission_id));
    if (snapshot.attempts.some((entry) => found.has(entry.submission_id))) {
      writeLedger(key, snapshot.attempts.filter((entry) => !found.has(entry.submission_id)));
    }
  }, [key, operations, snapshot]);
  return { attempts: snapshot.attempts, ledgerReadable: snapshot.ok };
}

/**
 * Action-boundary admission for a FRESH confirmation or a retry, read from the
 * live query cache and ledger at click time (the buttons apply the same gate).
 * Unread, loading, errored or re-fetching state is unknown and refuses.
 * Same-key recovery of a recorded unknown confirmation is not gated here.
 */
export function useInstillAdmission(recipientId: string) {
  const slug = useSlug();
  const queryClient = useQueryClient();
  const current = useCallback(<T,>(queryKey: unknown[]): T | null => {
    const state = queryClient.getQueryState<T>(queryKey);
    return state && state.status === 'success' && state.fetchStatus === 'idle' && state.data !== undefined ? state.data : null;
  }, [queryClient]);
  const quiet = useCallback((exceptOperationId: string | null): boolean => {
    const ledger = readLedger<InstillAttempt>(instillKey(slug, recipientId), isInstillAttempt);
    if (!ledger.ok || ledger.attempts.length > 0) return false;
    const history = current<{ operations: api.ImportOperation[] }>(['knowledge', slug, 'imports', recipientId]);
    if (!history) return false;
    return !history.operations.some((op) => IMPORT_IN_FLIGHT.has(op.state) && op.operation_id !== exceptOperationId);
  }, [current, slug, recipientId]);
  const canConfirm = useCallback((archiveId: string): boolean => {
    const eligibility = current<api.ImportEligibility>(['knowledge', slug, 'eligibility', recipientId, archiveId]);
    return !!eligibility && eligibility.recipient_eligible === true && quiet(null);
  }, [current, quiet, slug, recipientId]);
  const canRetry = useCallback((operationId: string): boolean => (
    !!current<api.ImportOperation>(['knowledge', slug, 'import', operationId]) && quiet(operationId)
  ), [current, quiet, slug]);
  return { canConfirm, canRetry };
}

/**
 * Local refusals around the Instill POST. `ledger_unavailable`,
 * `ledger_write_failed` and `unresolved_attempt` refuse BEFORE any request.
 * `recovery_refused` means the daemon refused a same-key re-send: that proves
 * nothing about the ORIGINAL confirmation, so its key stays recorded and new
 * confirmations stay blocked. `refusal`/`blockers` carry the daemon's own code.
 */
export type InstillLedgerCode = 'ledger_unavailable' | 'ledger_write_failed' | 'unresolved_attempt' | 'recovery_refused';
export class InstillLedgerError extends Error {
  constructor(
    readonly code: InstillLedgerCode,
    readonly refusal: string | null = null,
    readonly blockers: { kind: string; id: string }[] = [],
  ) {
    super(code);
    this.name = 'InstillLedgerError';
  }
}

export interface InstillSelection { archive_id: string; archive_version: number; item_ids: string[] }

/**
 * Confirmed Instill. With `submissionId` this re-sends that recorded unknown
 * confirmation with its original key and exact body. Without it, a new key is
 * minted only when no unknown confirmation is recorded, and only after the key
 * and body are durably recorded; otherwise nothing is sent.
 */
export function useStartInstill(recipientId: string) {
  const slug = useSlug();
  const queryClient = useQueryClient();
  const settled = useImportSettled(slug, recipientId);
  // The submission ID of the latest send, so an unknown outcome can be matched
  // against the recipient's authoritative history (a read, never a re-send).
  const lastSubmissionId = useRef<string | null>(null);
  const mutation = useMutation({
    mutationFn: async ({ selection, submissionId }: { selection?: InstillSelection; submissionId?: string }) => {
      const key = instillKey(slug, recipientId);
      const ledger = readLedger<InstillAttempt>(key, isInstillAttempt);
      if (!ledger.ok) throw new InstillLedgerError('ledger_unavailable');
      let entry: InstillAttempt | undefined;
      if (submissionId) {
        // Recovery re-sends only a key (and body) this browser recorded.
        entry = ledger.attempts.find((attempt) => attempt.submission_id === submissionId);
        if (!entry) throw new InstillLedgerError('ledger_unavailable');
      } else {
        if (!selection || selection.item_ids.length === 0) throw new InstillLedgerError('ledger_unavailable');
        if (ledger.attempts.length > 0) throw new InstillLedgerError('unresolved_attempt');
        entry = {
          submission_id: newSubmissionId(), operation_id: null, submitted_at: new Date().toISOString(),
          recipient_id: recipientId, archive_id: selection.archive_id, archive_version: selection.archive_version,
          item_ids: [...selection.item_ids],
        };
        const recorded = writeLedger(key, [entry]);
        const reread = readLedger<InstillAttempt>(key, isInstillAttempt);
        if (!recorded || !reread.ok || !reread.attempts.some((attempt) => attempt.submission_id === entry!.submission_id)) {
          throw new InstillLedgerError('ledger_write_failed');
        }
      }
      const confirmation: api.InstillConfirmation = {
        submission_id: entry.submission_id, archive_id: entry.archive_id, archive_version: entry.archive_version,
        item_ids: entry.item_ids, confirmed: true,
      };
      lastSubmissionId.current = entry.submission_id;
      let operation: api.ImportOperation;
      try {
        operation = await api.instillKnowledge(slug, recipientId, confirmation);
      } catch (error) {
        const status = (error as { status?: number }).status;
        if (submissionId) {
          // Only a returned operation settles an unknown confirmation; a refusal
          // of THIS re-send keeps the original key recorded.
          if (typeof status === 'number' && status >= 400 && status < 500) {
            const code = (error as { code?: string | null }).code;
            throw new InstillLedgerError('recovery_refused', code ?? `http_${status}`, blockersOf(error));
          }
          throw error;
        }
        // First send of a fresh key: S3b refuses these statuses before creating
        // any operation (bearer, validation, identity, admission/Busy, in-flight).
        // Anything else (5xx, transport loss) keeps the key for a same-key check.
        if (typeof status === 'number' && NO_OPERATION_STATUSES.has(status)) {
          writeLedger(key, readLedger<InstillAttempt>(key, isInstillAttempt).attempts
            .filter((attempt) => attempt.submission_id !== entry!.submission_id));
        }
        throw error;
      }
      queryClient.setQueryData(['knowledge', slug, 'import', operation.operation_id], operation);
      const current = readLedger<InstillAttempt>(key, isInstillAttempt);
      // A failed removal leaves the entry; history settles it on the next read.
      if (current.ok) writeLedger(key, current.attempts.filter((attempt) => attempt.submission_id !== operation.submission_id));
      return operation;
    },
    onSettled: settled,
  });
  return { mutation, lastSubmissionId };
}

/** Busy blockers from a daemon refusal (`ApiError.detail.blockers`), else []. */
export function blockersOf(error: unknown): { kind: string; id: string }[] {
  const detail = (error as { detail?: unknown } | null)?.detail as { blockers?: unknown } | null | undefined;
  const list: unknown[] = detail && typeof detail === 'object' && Array.isArray(detail.blockers) ? detail.blockers : [];
  return list.filter((b): b is { kind: string; id: string } => !!b && typeof (b as { kind?: unknown }).kind === 'string'
    && typeof (b as { id?: unknown }).id === 'string');
}
