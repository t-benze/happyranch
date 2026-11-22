/**
 * `useConfigOwner` — register one config editor with the owner registry
 * (THR-387 S6-W2a). See `@/lib/owners/ownerRegistry` for the state model and
 * `./OwnerGate` for the gate.
 *
 * The editor reports `dirty` and a `draft` value (any JSON-serializable
 * snapshot of its unsent edits); every change of `draft` is a new draft
 * revision. Saves go through `begin()`/`settle()` or `track()`, which tag the
 * save with the revision it was sent for. `track` resolves `current: false`
 * when the user edited during the save: the caller must then keep the newer
 * draft (never overwrite it with the saved response).
 *
 * A save must be ticketed with the draft it is BUILT from: an editor that
 * awaits anything before sending (identity resolution) calls `begin()` first
 * and passes that ticket to `track`. While a prior result is unknown no
 * ticket is issued (`begin()` returns null, `track` resolves `refused`
 * without running): Check result must reconcile it first. Unmounting the
 * editor releases the owner, but a pending/unknown save keeps holding
 * (detached) until it resolves.
 *
 * Desktop (S6-W2b): a ticketed save is sent only after native acknowledged
 * the owner summary that shows it pending (`@/lib/native/owners`). `track`
 * awaits that receipt before `run()`; an editor that sends on its own after
 * `begin()` awaits `sendable()` first. A refused/lost push settles the
 * ticket `failed` with nothing sent (`OwnersReceiptError`). Browser mode
 * admits at once. `track` is single-flight per owner: from its start until
 * the save settles `busy`/`isBusy()` are true, `begin()` returns null and a
 * second `track` is refused.
 *
 * Scope: an editor inside `<OwnerScope scope="settings" section=...>` (the
 * desktop Settings dialog pane) is a `settings` owner; anywhere else it is a
 * `main` owner.
 */
import { createContext, useCallback, useContext, useEffect, useId, useRef, useState, type ReactNode } from 'react';
import {
  classifySaveError,
  type OwnerPage,
  type OwnerRecord,
  type OwnerScope as OwnerScopeKind,
  type SaveOutcome,
  type SaveTicket,
} from '@/lib/owners/ownerRegistry';
import { awaitOwnersReceipt, OwnersReceiptError } from '@/lib/native/owners';
import { getAppStatus } from '@/lib/native/target';
import { remoteRefusalFor, shownRemoteRefusal, type RemoteRefusal } from '@/lib/remotePolicy';
import { useOwnerGateContext, useOwnerRecords } from './OwnerGate';

interface OwnerScopeValue {
  scope: OwnerScopeKind;
  section: string | null;
  /** Selected remote computer name, or null for This computer. */
  computer: string | null;
}

const OwnerScopeContext = createContext<OwnerScopeValue>({ scope: 'main', section: null, computer: null });

/** Mark editors below as panes of the desktop Settings dialog page `section`. */
export function OwnerScope({
  scope,
  section = null,
  children,
}: {
  scope: OwnerScopeKind;
  section?: string | null;
  children: ReactNode;
}): JSX.Element {
  const parent = useContext(OwnerScopeContext);
  return (
    <OwnerScopeContext.Provider value={{ scope, section, computer: parent.computer }}>
      {children}
    </OwnerScopeContext.Provider>
  );
}

export interface ConfigOwnerOptions {
  page: OwnerPage;
  /** Verbatim agent/skill name shown with the page, if any. */
  subject?: string | null;
  org: string | null;
  dirty: boolean;
  /** Snapshot of the unsent draft; each change is a new draft revision. */
  draft: unknown;
  /** Reset the editor's draft after Discard consent (if it stays mounted). */
  onDiscard?: () => void;
  /**
   * Same-context reconciliation for an unknown result: re-read the saved
   * value and answer whether the last sent save applied.
   */
  checkResult?: () => Promise<'saved' | 'not_saved' | 'unknown'>;
  /** The editor keeps its own router leave guard (see `OwnerIdentity`). */
  ownNavigationGuard?: boolean;
}

export type TrackResult<T> =
  | { ok: true; value: T; current: boolean }
  | {
      ok: false;
      error: unknown;
      outcome: Exclude<SaveOutcome, 'saved'>;
      current: boolean;
      /** True when nothing was sent: a prior result is still unknown, or another save of this owner is in flight. */
      refused: boolean;
    };

export interface ConfigOwner {
  id: string;
  record: OwnerRecord | null;
  /**
   * Ticket a save for the current draft (or for `revision`, a draft revision
   * captured earlier with `draftRevision`); null while a prior result is
   * unknown or while a `track()` save of this owner is in flight.
   */
  begin: (revision?: number) => SaveTicket | null;
  /** The current draft revision (capture it where a request is built). */
  draftRevision: number;
  /**
   * Settle a ticket. Pass the failure `error` so native's `403
   * remote_read_only` is shown as the remote read-only refusal (S8-2b).
   */
  settle: (ticket: SaveTicket, outcome: SaveOutcome, error?: unknown) => { current: boolean };
  /**
   * Desktop save admission after `begin()`: resolves true once native
   * acknowledged the summary showing the save pending (browser: at once).
   * False: send nothing and settle the ticket `failed`.
   */
  sendable: () => Promise<boolean>;
  /**
   * Run one save under a ticket (a fresh one, or `ticket` from an earlier
   * `begin()`); failures are classified, never thrown.
   */
  track: <T>(
    run: () => Promise<T>,
    classify?: (error: unknown) => 'failed' | 'unknown',
    ticket?: SaveTicket | null,
  ) => Promise<TrackResult<T>>;
  /** True while a prior result is unknown: no new save may be sent. */
  saveRefused: boolean;
  /**
   * True from the moment `track()` starts until its save settles (receipt
   * admission and the request): count it as pending and disable the submit
   * control and the fields it sends.
   */
  busy: boolean;
  /**
   * Synchronous form of `busy` for handlers: check it before replacing any
   * submission record (what a Check result compares against), so a refused
   * reentrant submit leaves the original record intact.
   */
  isBusy: () => boolean;
  /** Run Check result (no-op without `checkResult`). */
  check: () => Promise<void>;
  /** Record an editor-owned reconciliation of an unknown result. */
  reconcile: (outcome: 'saved' | 'not_saved') => void;
  checking: boolean;
  checkFailed: boolean;
  /**
   * S8-2b: the latest request was refused by native's pre-forward `403
   * remote_read_only` (nothing reached the Host; a draft stays). Holds the
   * remote Host name captured at refusal ('' if native did not name one yet);
   * null otherwise. A draft refusal shows while that draft is the failed one;
   * a draftless request's refusal (removal, retire/restore, a toggle) shows
   * on its own. The next request clears it. The save notice shows the
   * read-only copy, never C3.
   */
  remoteRefusal: string | null;
  /** True when the shown refusal was for a draftless request. */
  remoteRefusalDraftless: boolean;
}

function draftKey(draft: unknown): string {
  try {
    return JSON.stringify(draft) ?? '';
  } catch {
    return String(draft);
  }
}

export function useConfigOwner(options: ConfigOwnerOptions): ConfigOwner {
  const { registry, handlers } = useOwnerGateContext();
  const scope = useContext(OwnerScopeContext);
  const id = useId();
  const { page, subject = null, org, dirty, draft, onDiscard, checkResult, ownNavigationGuard = false } = options;

  // Draft revision: bumps whenever the draft snapshot changes.
  const key = draftKey(draft);
  const revision = useRef({ key, value: 0 });
  if (revision.current.key !== key) revision.current = { key, value: revision.current.value + 1 };
  const draftRevision = revision.current.value;

  const identity = {
    scope: scope.scope,
    page,
    subject,
    org,
    computer: scope.computer,
    section: scope.section,
    ownNavigationGuard,
  };
  const identityKey = JSON.stringify(identity);

  useEffect(() => {
    registry.register(id, JSON.parse(identityKey));
  }, [registry, id, identityKey]);
  // Teardown releases the owner; a pending/unknown save stays held.
  useEffect(() => () => registry.release(id), [registry, id]);
  useEffect(() => {
    registry.setDraft(id, { dirty, revision: draftRevision });
  }, [registry, id, dirty, draftRevision, identityKey]);

  const latest = useRef({ onDiscard, checkResult, dirty });
  latest.current = { onDiscard, checkResult, dirty };
  const [checking, setChecking] = useState(false);
  const [checkFailed, setCheckFailed] = useState(false);

  const check = useCallback(async () => {
    const run = latest.current.checkResult;
    if (!run) return;
    setChecking(true);
    setCheckFailed(false);
    try {
      const outcome = await run();
      registry.reconcile(id, outcome);
      if (outcome === 'unknown') {
        setCheckFailed(true);
        throw new Error('still unknown');
      }
    } catch (error) {
      setCheckFailed(true);
      throw error;
    } finally {
      setChecking(false);
    }
  }, [registry, id]);

  useEffect(() => {
    handlers.set(id, {
      discard: () => latest.current.onDiscard?.(),
      check: () => check(),
    });
    return () => {
      // A detached owner keeps its Check result (the provider prunes the
      // handlers of owners that are gone).
      if (!registry.has(id)) handlers.delete(id);
    };
  }, [handlers, id, check, registry]);
  const canCheck = checkResult !== undefined;
  useEffect(() => {
    registry.setCanCheck(id, canCheck);
  }, [registry, id, canCheck]);

  // S6-W2b: one track() save at a time per owner. The latch is set
  // synchronously before the receipt wait and held through the request; it is
  // released once the save settles (held by a refused/lost receipt, saved,
  // failed or unknown). While it is held begin() returns null and track()
  // refuses, so a reentrant submit can start no second ticket and send nothing.
  const inFlight = useRef(false);
  const [busy, setBusy] = useState(false);
  const [remoteRefusal, setRemoteRefusal] = useState<RemoteRefusal | null>(null);
  const isBusy = useCallback(() => inFlight.current, []);
  const begin = useCallback(
    (sentRevision?: number) => {
      if (inFlight.current) return null;
      // Report the current draft first so the registry knows the newest revision.
      registry.setDraft(id, { dirty: latest.current.dirty, revision: revision.current.value });
      const ticket = registry.beginSave(id, sentRevision);
      // A new request replaces the previous request's refusal.
      if (ticket !== null) setRemoteRefusal(null);
      return ticket;
    },
    [registry, id],
  );
  const noteRefusal = useCallback((error: unknown) => {
    // Recorded from the response itself: the refusal can arrive before
    // native's app.stateChanged reports the remote target.
    const refusal = remoteRefusalFor(error, getAppStatus(), !latest.current.dirty);
    if (refusal) setRemoteRefusal(refusal);
  }, []);
  const settle = useCallback(
    (ticket: SaveTicket, outcome: SaveOutcome, error?: unknown) => {
      setRemoteRefusal(null);
      if (outcome === 'failed') noteRefusal(error);
      return { current: registry.settleSave(ticket, outcome).current };
    },
    [registry, noteRefusal],
  );
  const sendable = useCallback(() => awaitOwnersReceipt(registry.getSnapshot()), [registry]);
  const track = useCallback(
    async <T,>(
      run: () => Promise<T>,
      classify: (error: unknown) => 'failed' | 'unknown' = classifySaveError,
      given?: SaveTicket | null,
    ): Promise<TrackResult<T>> => {
      // A given ticket comes from begin() in the same synchronous turn, which
      // already refused while the latch was held.
      const ticket = inFlight.current ? null : given === undefined ? begin() : given;
      if (ticket === null) {
        return { ok: false as const, error: null, outcome: 'unknown' as const, current: true, refused: true };
      }
      inFlight.current = true;
      setBusy(true);
      try {
        // Desktop: the summary showing this save pending must be acknowledged
        // first; otherwise nothing is sent and the draft stays unsaved.
        if (!(await sendable())) {
          const { current } = settle(ticket, 'failed');
          return { ok: false as const, error: new OwnersReceiptError(), outcome: 'failed' as const, current, refused: false };
        }
        try {
          const value = await run();
          const { current } = settle(ticket, 'saved');
          return { ok: true as const, value, current };
        } catch (error) {
          const outcome = classify(error);
          const { current } = settle(ticket, outcome, error);
          return { ok: false as const, error, outcome, current, refused: false };
        }
      } finally {
        inFlight.current = false;
        setBusy(false);
      }
    },
    [begin, settle, sendable],
  );

  const records = useOwnerRecords();
  const record = records.find((owner) => owner.id === id) ?? null;

  const reconcile = useCallback((outcome: 'saved' | 'not_saved') => registry.reconcile(id, outcome), [registry, id]);

  const saveRefused = record?.status === 'unknown';

  // The latest request's refusal: a draft one while that draft is the failed
  // one; a draftless one while nothing is unsent or outstanding.
  const shownRefusal = shownRemoteRefusal(record?.status, remoteRefusal);

  return {
    id, record, begin, draftRevision, settle, sendable, track, saveRefused, busy, isBusy, check, reconcile, checking, checkFailed,
    remoteRefusal: shownRefusal?.host ?? null,
    remoteRefusalDraftless: shownRefusal?.draftless ?? false,
  };
}
