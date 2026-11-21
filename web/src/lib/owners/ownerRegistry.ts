/**
 * Config owner registry (THR-387 S6-W2a; handoff v2.4 §3, corrections C1-C3).
 *
 * One registry per app document. Every config editor that can hold unsent
 * edits or a sent save registers an OWNER while it is mounted. The contextual
 * owner gate (`@/shared/owners`) reads it before a dialog close/switch, a
 * Settings page change, Main navigation or an org change. S6-W2b pushes
 * `selectOwnerBlocking` to native (`app.owners`, commit/close/Quit
 * preconditions); S6-L/S7-L3 rely on the same aggregate. This module is pure
 * (no React, no DOM) so the rules can be checked by a plain Node script.
 *
 * Owner identity: `{ scope, page, org, computer }`.
 * - `scope` — `main` (a Main page editor: Agents, Skills, Work Hours, browser
 *   Settings page) or `settings` (a pane of the desktop Settings dialog).
 * - `page` — a stable page id (see `OwnerPage`); copy is localized by callers.
 * - `org` — the org slug the editor writes to, or null for none.
 * - `computer` — the display name of the selected remote computer the save
 *   goes to, or null for This computer (callers localize it). Until the S5
 *   native status store supplies a target name every owner is null.
 *   Names are presentation only; the slug and page id identify the owner.
 *
 * Owner status (`OwnerStatus`), derived, never stored by callers:
 * - `pending`   — at least one save is sent and has no result yet.
 * - `unknown`   — a sent save's result is unknown (transport/authority lost,
 *                 unreadable answer) and has not been reconciled.
 * - `failed`    — the latest save was refused (confirmed failure) and the draft
 *                 is still unsaved.
 * - `dirty`     — unsent edits exist.
 * - `confirmed` — clean, and the last save was acknowledged for exactly the
 *                 current draft revision.
 * - `clean`     — nothing unsent and nothing outstanding.
 * Precedence: pending > unknown > failed > dirty > confirmed > clean.
 *
 * Draft revisions: the editor reports `{ dirty, revision }` where `revision`
 * changes on every draft edit. `beginSave` tags the save with the revision it
 * was sent for. A save result only ever applies to that revision: a success
 * for revision 3 while the draft is at revision 4 reports `current: false`
 * and the owner stays dirty (newer draft stays unsaved). An older save's
 * failure never marks a newer in-flight save failed. A sent save is never
 * discardable: `discard` refuses while pending or unknown.
 *
 * Unknown results refuse new mutations: `beginSave` returns null while the
 * owner has an unreconciled unknown save. Only same-owner reconciliation
 * (Check result → `reconcile`) can clear it; `not_saved` turns it into a
 * confirmed failure, which permits an ordinary retry.
 *
 * Editor teardown: `release` (the editor unmounted) forgets a clean/dirty
 * owner, but an owner with a pending or unknown save stays registered,
 * DETACHED, until that save settles or is reconciled. It keeps holding the
 * gate (and S6-W2b's native close/Quit) meanwhile; it is then forgotten.
 *
 * Aggregate (`selectOwnerBlocking`): `level` is `hold` when any affected owner
 * is pending/unknown (cannot be discarded or escaped), `prompt` when any is
 * dirty/failed (Keep editing / Discard), else `none`. `owners` lists the
 * affected non-clean owners in registration order.
 */

export type OwnerStatus = 'clean' | 'dirty' | 'pending' | 'failed' | 'unknown' | 'confirmed';
export type OwnerScope = 'main' | 'settings';
export type BlockingLevel = 'none' | 'prompt' | 'hold';
export type SaveOutcome = 'saved' | 'failed' | 'unknown';

/** Stable page ids (copy key suffixes). */
export type OwnerPage =
  | 'settings.organization'
  | 'settings.workHours'
  | 'settings.capacity'
  | 'settings.executors'
  | 'settings.coding'
  | 'agents.agent'
  | 'agents.escalationPolicy'
  | 'skills.assignment'
  | 'skills.customCreate'
  | 'skills.customDetail'
  | 'workHours.tier';

export interface OwnerIdentity {
  scope: OwnerScope;
  page: OwnerPage;
  /** Verbatim name shown with the page (an agent or skill name), if any. */
  subject?: string | null;
  org: string | null;
  /** Remote computer display name, or null for This computer. */
  computer: string | null;
  /** For `settings` owners: the Settings dialog page that shows it. */
  section?: string | null;
  /**
   * True for an editor with its own router leave guard (Capacity's documented
   * leave dialog, the team escalation policy page). See
   * `sharedRouterGuardNeeded`; dialog/page/org gates always count it.
   */
  ownNavigationGuard?: boolean;
}

export interface OwnerRecord extends OwnerIdentity {
  id: string;
  status: OwnerStatus;
  /** Current draft revision as last reported by the editor. */
  draftRevision: number;
  /** Revision of the latest sent save, or null. */
  sentRevision: number | null;
  /** Revision the last acknowledged save was sent for, or null. */
  savedRevision: number | null;
  /** True when a Check result reconciliation is available for `unknown`. */
  canCheck: boolean;
  /** True when the editor unmounted while a save was pending/unknown. */
  detached: boolean;
}

export interface SaveTicket {
  readonly ownerId: string;
  readonly revision: number;
  readonly seq: number;
}

export interface SettleResult {
  /** True when the settled save was sent for the CURRENT draft revision. */
  current: boolean;
  /** False when the owner no longer exists (unmounted). */
  known: boolean;
}

export interface OwnerBlocking {
  level: BlockingLevel;
  owners: OwnerRecord[];
}

interface MutableOwner extends OwnerIdentity {
  id: string;
  order: number;
  dirty: boolean;
  draftRevision: number;
  inFlight: Map<number, number>; // seq -> revision
  latestSeq: number;
  sentRevision: number | null;
  savedRevision: number | null;
  failedSeq: number | null;
  unknownSeqs: Set<number>;
  canCheck: boolean;
  detached: boolean;
}

export function deriveStatus(owner: {
  dirty: boolean;
  draftRevision: number;
  inFlight: { size: number };
  unknownSeqs: { size: number };
  failedSeq: number | null;
  savedRevision: number | null;
}): OwnerStatus {
  if (owner.inFlight.size > 0) return 'pending';
  if (owner.unknownSeqs.size > 0) return 'unknown';
  if (owner.dirty && owner.failedSeq !== null) return 'failed';
  if (owner.dirty) return 'dirty';
  if (owner.savedRevision !== null && owner.savedRevision === owner.draftRevision) return 'confirmed';
  return 'clean';
}

export function isHoldStatus(status: OwnerStatus): boolean {
  return status === 'pending' || status === 'unknown';
}

export function isPromptStatus(status: OwnerStatus): boolean {
  return status === 'dirty' || status === 'failed';
}

/**
 * Pure aggregate: blocking level and the affected non-clean owners.
 * `affects` narrows to the owners a requested transition would hide or
 * retire; omitted, every owner counts (native close/Quit/target commit).
 */
export function selectOwnerBlocking(
  owners: readonly OwnerRecord[],
  affects?: (owner: OwnerRecord) => boolean,
): OwnerBlocking {
  const listed = owners.filter(
    (owner) => (affects ? affects(owner) : true) && (isHoldStatus(owner.status) || isPromptStatus(owner.status)),
  );
  const level: BlockingLevel = listed.some((owner) => isHoldStatus(owner.status))
    ? 'hold'
    : listed.length > 0
      ? 'prompt'
      : 'none';
  return { level, owners: listed };
}

/**
 * Whether the shared router blocker must be mounted. React Router consults
 * only the LAST registered blocker, so editors with their own leave guard
 * (`ownNavigationGuard`: Capacity, the team escalation policy page) keep
 * theirs only while exactly one such editor is mounted, it is the only
 * non-clean owner and its state is dirty/failed. In every other case the
 * shared blocker mounts (re-registered last) and evaluates every owner:
 * a sent pending/unknown save is never left to a private Discard, and two
 * private guards never shadow each other.
 */
export function sharedRouterGuardNeeded(owners: readonly OwnerRecord[]): boolean {
  const selfGuarded = owners.filter((owner) => owner.ownNavigationGuard && !owner.detached);
  const sole = selfGuarded.length === 1 ? selfGuarded[0]!.id : null;
  return (
    selectOwnerBlocking(owners, (owner) => !(owner.id === sole && isPromptStatus(owner.status))).level !== 'none'
  );
}

export interface OwnerRegistry {
  register: (id: string, identity: OwnerIdentity) => void;
  /** Update identity fields (e.g. the computer label) of a live owner. */
  identify: (id: string, identity: OwnerIdentity) => void;
  unregister: (id: string) => void;
  /**
   * The editor unmounted: forget the owner unless a save is pending or its
   * result is unknown, in which case it stays (detached) until resolved.
   */
  release: (id: string) => void;
  /** True while the owner (attached or detached) is registered. */
  has: (id: string) => boolean;
  /** Editor report: whether unsent edits exist and the draft revision. */
  setDraft: (id: string, draft: { dirty: boolean; revision: number }) => void;
  setCanCheck: (id: string, canCheck: boolean) => void;
  /**
   * Tag a save with the draft revision it is sent for. Returns null (no
   * ticket: the caller must not send) while a prior result is unknown.
   */
  beginSave: (id: string, revision?: number) => SaveTicket | null;
  settleSave: (ticket: SaveTicket, outcome: SaveOutcome) => SettleResult;
  /**
   * Reconcile an unknown result (Check result). `saved` acknowledges every
   * unknown save; `not_saved` turns them into a confirmed failure (the draft
   * stays unsaved); `unknown` keeps them unknown.
   */
  reconcile: (id: string, outcome: 'saved' | 'not_saved' | 'unknown') => void;
  /**
   * Record discard consent for an owner with only unsent edits. Refused
   * (returns false) while a save is pending or its result is unknown.
   */
  discard: (id: string) => boolean;
  getSnapshot: () => readonly OwnerRecord[];
  subscribe: (listener: () => void) => () => void;
}

export function createOwnerRegistry(): OwnerRegistry {
  const owners = new Map<string, MutableOwner>();
  const listeners = new Set<() => void>();
  let snapshot: readonly OwnerRecord[] = [];
  let order = 0;
  let seq = 0;

  const publish = (): void => {
    snapshot = [...owners.values()]
      .sort((a, b) => a.order - b.order)
      .map((owner) => ({
        id: owner.id,
        scope: owner.scope,
        page: owner.page,
        subject: owner.subject ?? null,
        org: owner.org,
        computer: owner.computer,
        section: owner.section ?? null,
        ownNavigationGuard: owner.ownNavigationGuard ?? false,
        status: deriveStatus(owner),
        draftRevision: owner.draftRevision,
        sentRevision: owner.sentRevision,
        savedRevision: owner.savedRevision,
        canCheck: owner.canCheck,
        detached: owner.detached,
      }));
    for (const listener of [...listeners]) listener();
  };

  const sameIdentity = (owner: MutableOwner, identity: OwnerIdentity): boolean =>
    owner.scope === identity.scope &&
    owner.page === identity.page &&
    (owner.subject ?? null) === (identity.subject ?? null) &&
    owner.org === identity.org &&
    owner.computer === identity.computer &&
    (owner.section ?? null) === (identity.section ?? null) &&
    (owner.ownNavigationGuard ?? false) === (identity.ownNavigationGuard ?? false);

  /** Forget a detached owner once nothing is outstanding. */
  const dropResolved = (owner: MutableOwner): void => {
    if (owner.detached && owner.inFlight.size === 0 && owner.unknownSeqs.size === 0) owners.delete(owner.id);
  };

  return {
    register(id, identity) {
      const existing = owners.get(id);
      if (existing) {
        if (!sameIdentity(existing, identity)) {
          Object.assign(existing, identity);
          publish();
        }
        return;
      }
      owners.set(id, {
        ...identity,
        id,
        order: order++,
        dirty: false,
        draftRevision: 0,
        inFlight: new Map(),
        latestSeq: 0,
        sentRevision: null,
        savedRevision: null,
        failedSeq: null,
        unknownSeqs: new Set(),
        canCheck: false,
        detached: false,
      });
      publish();
    },
    identify(id, identity) {
      const owner = owners.get(id);
      if (!owner || sameIdentity(owner, identity)) return;
      Object.assign(owner, identity);
      publish();
    },
    unregister(id) {
      if (owners.delete(id)) publish();
    },
    release(id) {
      const owner = owners.get(id);
      if (!owner) return;
      if (owner.inFlight.size === 0 && owner.unknownSeqs.size === 0) {
        owners.delete(id);
      } else {
        // The sent save outlives its editor: keep holding until resolved.
        owner.detached = true;
        owner.dirty = false;
        owner.failedSeq = null;
      }
      publish();
    },
    has: (id) => owners.has(id),
    setDraft(id, draft) {
      const owner = owners.get(id);
      if (!owner) return;
      if (owner.dirty === draft.dirty && owner.draftRevision === draft.revision) return;
      owner.dirty = draft.dirty;
      owner.draftRevision = draft.revision;
      // A clean draft has nothing left to fail; a sent-but-unknown save stays.
      if (!draft.dirty) owner.failedSeq = null;
      publish();
    },
    setCanCheck(id, canCheck) {
      const owner = owners.get(id);
      if (!owner || owner.canCheck === canCheck) return;
      owner.canCheck = canCheck;
      publish();
    },
    beginSave(id, revision) {
      const owner = owners.get(id);
      // An unknown result is not retry authority: reconcile it first.
      if (owner && owner.unknownSeqs.size > 0) return null;
      // `revision`: the draft the request was BUILT from, when the editor
      // awaited something (identity resolution) before sending.
      const ticket: SaveTicket = { ownerId: id, revision: revision ?? owner?.draftRevision ?? 0, seq: ++seq };
      if (!owner) return ticket;
      owner.inFlight.set(ticket.seq, ticket.revision);
      owner.latestSeq = ticket.seq;
      owner.sentRevision = ticket.revision;
      owner.failedSeq = null;
      publish();
      return ticket;
    },
    settleSave(ticket, outcome) {
      const owner = owners.get(ticket.ownerId);
      if (!owner || !owner.inFlight.has(ticket.seq)) {
        return { current: owner?.draftRevision === ticket.revision, known: !!owner };
      }
      owner.inFlight.delete(ticket.seq);
      const current = owner.draftRevision === ticket.revision;
      const newest = ticket.seq === owner.latestSeq;
      if (outcome === 'unknown') {
        owner.unknownSeqs.add(ticket.seq);
      } else if (outcome === 'failed') {
        // An older refusal never marks a newer sent save failed.
        if (newest) owner.failedSeq = ticket.seq;
      } else {
        // Saved applies only to the acknowledged revision. A newer draft
        // stays dirty (the editor keeps reporting dirty: true).
        if (owner.savedRevision === null || ticket.revision >= owner.savedRevision) {
          owner.savedRevision = ticket.revision;
        }
        // The acknowledged revision IS the current draft: nothing is unsent
        // (the editor's next edit reports a new revision and its own dirty).
        if (current && owner.inFlight.size === 0) {
          owner.dirty = false;
          owner.failedSeq = null;
        }
      }
      dropResolved(owner);
      publish();
      return { current, known: true };
    },
    reconcile(id, outcome) {
      const owner = owners.get(id);
      if (!owner || owner.unknownSeqs.size === 0 || outcome === 'unknown') return;
      const latestUnknown = Math.max(...owner.unknownSeqs);
      owner.unknownSeqs.clear();
      if (outcome === 'not_saved') {
        if (latestUnknown >= owner.latestSeq) owner.failedSeq = latestUnknown;
      } else if (owner.sentRevision !== null && latestUnknown >= owner.latestSeq) {
        owner.savedRevision = owner.sentRevision;
        if (owner.draftRevision === owner.sentRevision) owner.dirty = false;
      }
      dropResolved(owner);
      publish();
    },
    discard(id) {
      const owner = owners.get(id);
      if (!owner) return true;
      if (owner.inFlight.size > 0 || owner.unknownSeqs.size > 0) return false;
      if (!owner.dirty && owner.failedSeq === null) return true;
      owner.dirty = false;
      owner.failedSeq = null;
      publish();
      return true;
    },
    getSnapshot: () => snapshot,
    subscribe(listener) {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
  };
}

/**
 * Classify a rejected save. Only an answer the daemon actually returned as a
 * client-side refusal (4xx other than 408/409 target_changed) is a CONFIRMED
 * failure. No response, a non-API error, 408, 409 target_changed after send
 * and any 5xx are UNKNOWN: the save may or may not have applied.
 */
export function classifySaveError(error: unknown): 'failed' | 'unknown' {
  if (!error || typeof error !== 'object') return 'unknown';
  const { status, code } = error as { status?: unknown; code?: unknown };
  if (typeof status !== 'number') return 'unknown';
  if (status === 408 || status >= 500) return 'unknown';
  if (status === 409 && code === 'target_changed') return 'unknown';
  if (status >= 400) return 'failed';
  return 'unknown';
}
