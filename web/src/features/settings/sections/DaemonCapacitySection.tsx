/**
 * Daemon / Capacity — stage the paired daemon-wide capacity values for a future
 * operator-controlled restart.
 *
 * Saving never applies live and this page cannot restart the daemon. Every
 * number on screen carries a named time/source context, and nothing here
 * promises throughput, free slots, provider ceilings, host safety, a verified
 * audit actor or a complete audit history.
 *
 * State is four independent objects (settled semantics S1): `base` (accepted
 * only from a usable read or a usable success), `draft`, `latest` (an
 * observation that never writes `base` on its own) and `submission` (captured
 * immutably before the request is awaited, so callbacks report what was SENT,
 * never what is in the fields). Only two operator-explicit transitions move
 * `base`: rebase-onto-latest and discard-and-accept-latest. Neither sends a
 * request; the following manual save does.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useBlocker } from 'react-router-dom';
import { RotateCcw } from 'lucide-react';
import { Button } from '@/design-system/primitives/Button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/design-system/primitives/Dialog';
import { Input } from '@/design-system/primitives/Input';
import { Textarea } from '@/design-system/primitives/Textarea';
import { useDaemonCapacity, useUpdateDaemonCapacity } from '@/hooks/settings';
import { useTranslation } from '@/hooks/i18n';
import { ApiError } from '@/lib/api';
import type { DaemonCapacitySnapshot, DaemonCapacityWrite } from '@/lib/api/types';
import { translate, type Locale, type MessageKey, type MessageParams } from '@/lib/i18n';
import {
  ackContextOf,
  baseFromSnapshot,
  capacityFieldLabel,
  classifySnapshot,
  consequenceMessage,
  draftConsequence,
  formatPresenceValue,
  formatReceipt,
  numericTextMessage,
  parseCapacityText,
  REASON_MAX_LENGTH,
  resolvedNextStart,
  sameAckContext,
  sameBaseValues,
  type AckContext,
  type CapacityBase,
  type CapacitySubmission,
} from './capacityModel';

/**
 * A localizable message held in state. It is translated at RENDER time with the
 * current locale, so a locale switch re-translates a banner or field error that
 * is already on screen instead of freezing the language it was raised in.
 */
type Copy = (locale: Locale) => string;

function copy(key: MessageKey, params?: MessageParams): Copy {
  return (locale) => translate(locale, key, params);
}

/** The localized field labels for both capacity keys. */
function fieldLabels(locale: Locale): { workersLabel: string; capLabel: string } {
  return {
    workersLabel: capacityFieldLabel('queue_workers', locale),
    capLabel: capacityFieldLabel('host_global_session_cap', locale),
  };
}

/**
 * Capacity-local focus-ring override (accepted 16.10 / 16.11).
 *
 * The shared `--color-ring` token is the accent at ~45% alpha. Composited over
 * this screen's warm canvas that measures ~1.4:1, which fails the accepted
 * "no low-contrast focus ring" criterion in BOTH themes. The shared token and
 * the shared primitives are out of scope for this bounded screen, so the ring
 * is corrected HERE, on the capacity controls only, using the already-authorized
 * full-opacity accent token. Nothing outside this panel changes.
 */
const FOCUS_RING = 'focus-visible:ring-accent-default';
/** Same ring for a bare element that has no design-system primitive under it. */
const FOCUS_RING_RAW = 'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-default';

/**
 * The same capacity-local ring, reaching the PORTALLED leave dialog.
 *
 * The leave-confirmation dialog renders through a Radix portal, so it sits
 * outside this panel's wrapper and never inherited `FOCUS_RING` above. Its two
 * footer buttons are ours and take `FOCUS_RING` directly. Its Close control is
 * NOT: `DialogContent` renders that button itself, as a direct child of the
 * portalled content element, with the shared translucent `focus:ring-ring`.
 *
 * `DialogContent` forwards `className` onto exactly that content element, so
 * the call site can still reach its own dialog's Close through a child
 * selector — no shared primitive, token or variant is touched, and no other
 * consumer of `Dialog` is affected. `> button` is the Close alone: this
 * dialog's other buttons live inside `DialogFooter`'s div.
 *
 * Two properties are needed, not one:
 *  - the ring COLOUR, for the same reason as `FOCUS_RING` — measured 1.45:1
 *    light / 2.98:1 dark against the dialog surface, below the accepted 3:1;
 *  - the element OPACITY. The Close carries `opacity-70`, which composites the
 *    whole control — its box-shadow ring included — at 70% over the dialog
 *    surface. A full-opacity ring token still lands at ~2.43:1 light while that
 *    is in force, so restoring full opacity for the focused state is part of
 *    the fix rather than a cosmetic extra.
 *
 * The specificity works out in our favour: this compiles to
 * `.<class> > button:focus-visible` (0,2,1), which outranks the primitive's own
 * `.focus\:ring-ring:focus` (0,2,0). Pointer focus keeps the primitive's
 * behaviour untouched. `scripts/screenshot-harness/capacity-states.mjs` walks
 * and GATES all three dialog controls in a real browser at both widths in both
 * themes, so this comment cannot drift from the shipped result.
 */
const DIALOG_FOCUS_RING = '[&>button:focus-visible]:ring-accent-default [&>button:focus-visible]:opacity-100';

/**
 * Capacity-local primary-action tone (accepted 16.10 control-label contrast).
 *
 * The shared `default` Button variant paints `--color-text-inverse` on
 * `--color-accent-default`. Measured in a real browser that is 3.96:1 in light
 * — under the accepted 4.5:1 control-label threshold — and its
 * `hover:bg-primary/90` composites LIGHTER still, so the hover state is worse
 * than the resting one. The shared primitive and the shared token definitions
 * are out of scope for this bounded screen, exactly as with `FOCUS_RING` above,
 * so the tone is corrected HERE on this panel's primary actions only, by
 * selecting darker EXISTING accent tokens through `className`. `cn()` is
 * `twMerge`, so these win the `bg-*` / `hover:bg-*` / `active:bg-*` groups over
 * the variant's defaults; nothing outside this panel changes.
 *
 * Resting tone is `--color-accent-hover`; the pointer states step to
 * `--color-accent-text`, which keeps this system's own directional hover
 * semantics — darker in light, brighter in dark. Browser-computed against
 * `--color-text-inverse`: light 5.07:1 resting / 6.58:1 hover+active, dark
 * 9.22:1 resting / 10.50:1 hover+active, all above the 4.5:1 threshold.
 * `scripts/screenshot-harness/capacity-states.mjs` measures and GATES those
 * three phases with a real pointer at both desktop widths in both themes, so
 * this comment cannot drift from the shipped result without failing the run.
 *
 * The focus ring is untouched: it paints OUTSIDE the control, on the page
 * canvas, so `FOCUS_RING` keeps its already-measured behaviour.
 */
const PRIMARY_TONE = 'bg-accent-hover hover:bg-accent-text active:bg-accent-text';

const READ_BLOCKED_SAVE = copy('settings.capacity.readBlockedSave');

/** Outcome of a settled save attempt. */
type SaveOutcome =
  | { kind: 'idle' }
  | { kind: 'saving' }
  /** A usable 200 was accepted into base. */
  | { kind: 'saved'; snapshot: DaemonCapacitySnapshot }
  /** A typed rejection: the request did not publish. */
  | { kind: 'rejected'; message: Copy; focus?: 'queue_workers' | 'host_global_session_cap' | 'rationale' | 'ack' }
  /** Typed publication-uncertain: known replacement wording. */
  | { kind: 'uncertain'; message: Copy }
  /** Lost response, unclassified 5xx, network error, or an unusable success. */
  | { kind: 'unknown'; message: Copy };

const UNKNOWN_OUTCOME_COPY = copy('settings.capacity.unknownOutcome');

/**
 * An unresolved publication outcome (R2).
 *
 * This is its OWN state, never a reading of the currently rendered banner. A
 * refused second Save replaces the banner with a refusal message, and an
 * ordinary Discard resets the draft — neither of those establishes what the
 * daemon actually persisted, so neither may unlock a write. Only an explicit
 * reconciliation (rebase / accept-latest) or a genuinely accepted PUT clears it.
 */
interface UnresolvedPublication {
  kind: 'uncertain' | 'unknown';
  message: Copy;
}

/**
 * The shape of a settled `refetch()` result that R3 actually has to inspect.
 *
 * React Query RESOLVES this object even when the request failed, and a failed
 * result still carries the PREVIOUS cached `data`. Reading `.data` alone
 * therefore cannot distinguish a fresh successful observation from a stale
 * cached one beside an error.
 */
interface RefetchOutcome {
  status?: string;
  isError?: boolean;
  error?: unknown;
  data?: unknown;
}

/** Where a `latest` observation came from. Presentation only — never priority. */
type LatestOrigin = 'conflict' | 'external' | 'checked';

/**
 * A recorded `latest` observation (R4).
 *
 * `seq` is the ORDER IN WHICH THIS EDITOR ACCEPTED the observation, so a newer
 * successful read supersedes an older 409 snapshot. Selecting by origin slot
 * instead let a stale conflict body win over a later verified read and sent its
 * revision as `If-Match`. Local ordering only: it is never rendered and is not
 * a claim about server time (S5-R6).
 */
interface LatestObservation {
  base: CapacityBase;
  origin: LatestOrigin;
  seq: number;
}

/**
 * Map a rejection to safe fixed copy. No raw exception text, stack, filesystem
 * path or private value ever reaches the DOM.
 *
 * Only outcomes the daemon actually types are called "rejected"; everything
 * else stays UNKNOWN. In particular a generic failure is never described as
 * having "failed safely" — that asserts an outcome the frontend cannot observe.
 */
function classifySaveError(error: unknown): SaveOutcome {
  if (!(error instanceof ApiError)) {
    return { kind: 'unknown', message: UNKNOWN_OUTCOME_COPY };
  }
  const detail = (error.detail ?? {}) as { artifact_state?: 'absent' | 'present' | 'unknown' };
  const artifactKey: MessageKey | null = detail.artifact_state === 'present'
    ? 'settings.capacity.artifact.present'
    : detail.artifact_state === 'unknown'
      ? 'settings.capacity.artifact.unknown'
      : null;
  const withArtifact = (key: MessageKey): Copy => (locale) =>
    translate(locale, key) + (artifactKey === null ? '' : translate(locale, artifactKey));

  if (error.status === 401 || error.status === 403) {
    return {
      kind: 'rejected',
      message: copy('settings.capacity.error.unauthorized'),
    };
  }
  if (error.code === 'environment_confirmation_required') {
    return {
      kind: 'rejected',
      message: copy('settings.capacity.error.confirmOverride'),
      focus: 'ack',
    };
  }
  if (error.status === 422) {
    return {
      kind: 'rejected',
      message: (locale) => translate(locale, 'settings.capacity.error.rejectedValues', fieldLabels(locale)),
      focus: 'queue_workers',
    };
  }
  // 428 if_match_required (header absent) and 400 if_match_invalid (malformed)
  // are rejected BEFORE publication. The revision is never operator-editable,
  // so no hash entry field is offered.
  if (error.code === 'if_match_required' || error.code === 'if_match_invalid'
    || error.status === 428 || (error.status === 400 && error.code !== null)) {
    return {
      kind: 'rejected',
      message: copy('settings.capacity.error.ifMatch'),
    };
  }
  if (error.code === 'audit_failed') {
    return {
      kind: 'rejected',
      message: copy('settings.capacity.error.auditFailed'),
    };
  }
  if (error.code === 'config_write_failed') {
    // Pre-publication failure: state what THIS REQUEST did not publish. Making
    // a claim about the file's current contents would exclude external writers.
    return {
      kind: 'rejected',
      message: withArtifact('settings.capacity.error.configWriteFailed'),
    };
  }
  if (error.code === 'config_publication_uncertain') {
    return {
      kind: 'uncertain',
      message: withArtifact('settings.capacity.error.publicationUncertain'),
    };
  }
  return { kind: 'unknown', message: UNKNOWN_OUTCOME_COPY };
}

function SectionLabel({ children, note }: { children: string; note?: string }): JSX.Element {
  return (
    <h3 className="text-text-secondary mt-8 mb-3 text-xs font-semibold tracking-wider uppercase">
      {children}
      {note ? <span className="text-text-secondary ml-2 font-normal tracking-normal normal-case">{note}</span> : null}
    </h3>
  );
}

function RunningCard({ label, value, description }: { label: string; value: string; description: string }): JSX.Element {
  return (
    <div className="border-border-default bg-surface-raised rounded-md border p-4">
      <p className="text-text-secondary text-sm">{label}</p>
      <p className="font-display text-text-primary mt-1 text-3xl leading-none font-medium">{value}</p>
      <p className="text-text-secondary mt-3 text-sm">{description}</p>
    </div>
  );
}

function Pill({ tone, children }: { tone: 'accent' | 'neutral'; children: React.ReactNode }): JSX.Element {
  const classes = tone === 'accent'
    ? 'bg-accent-muted text-accent-text'
    : 'border-border-default text-text-secondary border';
  return (
    <span className={`inline-flex items-center gap-2 rounded-full px-3 py-1 text-xs font-medium ${classes}`}>
      {children}
    </span>
  );
}

export function DaemonCapacitySection(): JSX.Element {
  const { t, render, locale } = useTranslation();
  const { workersLabel, capLabel } = fieldLabels(locale);
  const query = useDaemonCapacity();
  const save = useUpdateDaemonCapacity();

  const [base, setBase] = useState<CapacityBase | null>(null);
  const [workersText, setWorkersText] = useState('');
  const [capText, setCapText] = useState('');
  const [reason, setReason] = useState('');
  const [ack, setAck] = useState(false);
  // Read only inside the updater below — the previous context is what the
  // identity comparison needs, never a render input.
  const [, setAckContext] = useState<AckContext | null>(null);
  const [ackResetNotice, setAckResetNotice] = useState<MessageKey | null>(null);
  const [submission, setSubmission] = useState<CapacitySubmission | null>(null);
  const [outcome, setOutcome] = useState<SaveOutcome>({ kind: 'idle' });
  /** R2: survives refused Save clicks, banner changes and ordinary Discard. */
  const [unresolvedPublication, setUnresolvedPublication] =
    useState<UnresolvedPublication | null>(null);
  /** R4: ONE latest slot, ordered by the order this editor accepted it. */
  const [latest, setLatest] = useState<LatestObservation | null>(null);
  /** A 409 has been seen and not yet reconciled. */
  const [conflictSeen, setConflictSeen] = useState(false);
  /** A usable read whose revision moved while the form was dirty or unresolved. */
  const [externalSeen, setExternalSeen] = useState(false);
  const [conflictUnusable, setConflictUnusable] = useState<MessageKey | null>(null);
  /**
   * The last snapshot that classified USABLE, retained TOGETHER with the
   * receipt of the response that actually produced it. When the current read is
   * unusable or failed there is still something honest to show, labelled
   * "Last known" with its OWN receipt (accepted 14.1 / 14.2 / 14.3).
   *
   * C1: a successful HTTP response with an unusable body still advances the
   * PROVIDER receipt (S5-R5 — correctly, it identifies a real response), but it
   * did not produce these values. Storing only the snapshot and rendering
   * `query.observation.receiptAt` beside it attributed the new response's time
   * to the old values. The receipt is therefore part of the retained record.
   */
  const [lastUsable, setLastUsable] = useState<{
    snapshot: DaemonCapacitySnapshot;
    receiptAt: number | null;
  } | null>(null);
  /**
   * R5: the TEXT the accepted base seeded, so raw operator edits are tracked
   * independently of whether the draft currently parses into a valid pair.
   * Invalid, unsafe or blank text is still unsaved work.
   */
  const [baseText, setBaseText] = useState({ workers: '', cap: '' });
  const [fieldErrors, setFieldErrors] = useState<Record<string, Copy>>({});
  const [detailsOpen, setDetailsOpen] = useState(false);
  /** Monotonic local order for `latest` acceptance. Never rendered (S5-R6). */
  const latestSeqRef = useRef(0);

  const workersRef = useRef<HTMLInputElement>(null);
  const capRef = useRef<HTMLInputElement>(null);
  const reasonRef = useRef<HTMLTextAreaElement>(null);
  const ackRef = useRef<HTMLInputElement>(null);
  const reconciledRef = useRef<HTMLDivElement>(null);
  const restoreFocusRef = useRef<HTMLElement | null>(null);

  const classification = useMemo(
    () => (query.data === undefined ? null : classifySnapshot(query.data)),
    [query.data],
  );
  /** The snapshot we are willing to read values from. */
  const snapshot = classification && classification.status !== 'unusable'
    ? classification.snapshot
    : null;
  const readUnusableReason = classification?.status === 'unusable' ? classification.reason : null;
  const inconsistentRead = classification?.status === 'inconsistent';

  // Acknowledgment identity (S8): reset if and only if the shadowed key set OR
  // a resolved override value changes. Base, draft and reason are preserved.
  useEffect(() => {
    if (!snapshot) return;
    const next = ackContextOf(snapshot);
    setAckContext((current) => {
      if (sameAckContext(current, next)) return current;
      if (current !== null && next.shadowedKeys.length > 0) {
        setAck(false);
        setAckResetNotice('settings.capacity.ackReset');
      }
      if (next.shadowedKeys.length === 0) {
        setAck(false);
        setAckResetNotice(null);
      }
      return next;
    });
  }, [snapshot]);

  /**
   * The provider-owned observation, read by the retention and read-acceptance
   * effects below without making them depend on its identity.
   */
  const observationRef = useRef(query.observation);
  observationRef.current = query.observation;
  /**
   * C3: THIS editor's own most recent write REQUEST.
   *
   * The capacity observation is module-scoped by org, so `origin: 'write'`
   * cannot by itself tell the SUBMITTING editor apart from a SECOND mounted
   * editor observing that same accepted write. Timing cannot tell them apart
   * either: `useEffect` is a PASSIVE effect, so in a real browser the cache
   * notification of the editor's own write can be flushed before OR after the
   * awaited submit continuation. Nor can the revision: it is a content hash, so
   * another editor restoring the same bytes produces the same revision again.
   *
   * Ownership is therefore the exact request. The provider records which
   * settlement each request object produced BEFORE it writes the cache
   * (`save.settlementOf`), so whenever this editor can render its own write's
   * settlement it can also identify it — and nothing else. Every other
   * observation (another editor's write, a write of equal revision bytes, a
   * read) is processed immediately by the ordinary acceptance rules below.
   * Nothing is provisionally suppressed, so nothing can be lost when this
   * request is later rejected, fails, or turns out unknown.
   *
   * `pending` is true until the submit continuation has handled the result.
   * `observedDuring` is the newest external observation recorded while it was
   * pending, so an accepted continuation can keep one that settled AFTER its
   * own write instead of fencing it with the obsolete ones.
   */
  const ownWriteRef = useRef<{
    request: DaemonCapacityWrite;
    pending: boolean;
    observedDuring: { settledSeq: number; base: CapacityBase } | null;
  } | null>(null);
  const settlementOf = save.settlementOf;

  // Retain the last USABLE observation — snapshot AND its receipt — so an
  // unusable or failed read can still show labelled prior values, each with the
  // time of the response that actually produced it, instead of blanking the
  // surface or borrowing a later response's receipt.
  //
  // C1: `classification` is memoized on `query.data`, and React Query's
  // structural sharing returns a REFERENCE-IDENTICAL object for a byte-identical
  // successful response. Depending on `classification` alone therefore MISSES a
  // real, usable observation whose values did not change, leaving the retained
  // receipt behind the provider's. The observation's own settlement sequence is
  // the stable marker of "a genuine usable response produced a snapshot": it
  // advances only when the provider publishes a successful read or accepts a
  // write — never on a cache reread, remount or dropped read. Depending on the
  // always-new `query.observation` OBJECT would instead re-run this effect on
  // every render, so the scalar sequence is used.
  const usableObservationSeq = query.observation?.outcome === 'usable'
    ? query.observation.settledSeq
    : null;
  useEffect(() => {
    const observation = observationRef.current;
    // Only a genuine USABLE observation may (re)date the retained values. A
    // failed, unusable or dropped read carries its own outcome, so it can never
    // attach its timestamp to values it did not produce.
    if (classification?.status !== 'usable') return;
    if (observation === null || observation.outcome !== 'usable') return;
    setLastUsable({
      snapshot: classification.snapshot,
      receiptAt: observation.receiptAt,
    });
  }, [classification, usableObservationSeq]);

  /**
   * The current read FAILED after a successful one (R1). React Query keeps the
   * previous `data`, so `query.data` alone cannot distinguish "confirmed" from
   * "last known and unverified" — `isError` is the only honest signal, and a
   * refetch result that merely carries cached `.data` beside an error is NOT a
   * success.
   */
  const refreshFailed = query.isError;
  /**
   * The snapshot the surface DISPLAYS: the current usable read, or the last
   * usable observation when the current read is unusable or failed.
   */
  const displayedSnapshot = snapshot ?? lastUsable?.snapshot ?? null;
  /** No write may be built against a read we cannot trust. */
  const readBlocked = refreshFailed || readUnusableReason !== null || inconsistentRead;

  // Sourced from the DISPLAYED snapshot, so an unusable or failed read does not
  // make the acknowledgment control vanish while its value is still held
  // (accepted 14.1: base, draft, reason and ack are ALL preserved).
  const shadowed = displayedSnapshot !== null
    && displayedSnapshot.environment_shadowed.length > 0;

  const draftPair = useMemo(() => {
    const workers = parseCapacityText(workersText);
    const cap = parseCapacityText(capText);
    if (!workers.ok || !cap.ok) return null;
    return { queue_workers: workers.value, host_global_session_cap: cap.value };
  }, [workersText, capText]);

  const draftBase: CapacityBase | null = useMemo(() => {
    if (draftPair === null) return null;
    return {
      pair: draftPair,
      // Saving always writes both keys explicitly, so a staged draft is
      // present-present by construction.
      keyPresence: { queue_workers: true, host_global_session_cap: true },
      revision: base?.revision ?? '',
    };
  }, [draftPair, base]);

  const valuesChanged = base !== null && draftBase !== null && !sameBaseValues(base, draftBase);
  /**
   * R5: a raw text edit is unsaved operator work even when it does not parse
   * into a valid pair. `9007199254740993`, `abc` and `` are all work a reload
   * or a route change would destroy, and a later changed-revision read must not
   * treat the editor as clean and overwrite them.
   */
  const rawEdited = base !== null
    && (workersText !== baseText.workers || capText !== baseText.cap);
  const rationaleOnlyDirty = !valuesChanged && !rawEdited && reason.trim().length > 0;
  const unresolved = unresolvedPublication !== null
    || conflictSeen || conflictUnusable !== null || externalSeen;
  const dirty = rawEdited || valuesChanged || rationaleOnlyDirty || unresolved;

  // A write lock engages on a conflict or an uncertain/unknown outcome. Until an
  // explicit reconciliation clears it, no PUT is issued under any circumstance.
  const writeLocked = unresolved;

  const blocker = useBlocker(dirty);

  // "Latest value" refs so the read-acceptance effect can consult the CURRENT
  // dirty/lock state without re-running whenever the operator types.
  const baseRef = useRef<CapacityBase | null>(null);
  baseRef.current = base;
  const guardRef = useRef({ dirty: false, writeLocked: false });
  guardRef.current = { dirty, writeLocked };
  const latestRef = useRef<LatestObservation | null>(null);
  latestRef.current = latest;

  const acceptBase = useCallback((next: CapacityBase) => {
    const workers = String(next.pair.queue_workers);
    const cap = String(next.pair.host_global_session_cap);
    setBase(next);
    setWorkersText(workers);
    setCapText(cap);
    setBaseText({ workers, cap });
  }, []);

  /**
   * Record a usable observation as `latest`, ordered by acceptance (R4). A
   * later observation always supersedes an earlier one whatever its origin, so
   * a verified Check read replaces an older conflict body rather than losing to
   * it.
   */
  const recordLatest = useCallback((observed: CapacityBase, origin: LatestOrigin) => {
    latestSeqRef.current += 1;
    const seq = latestSeqRef.current;
    setLatest((current) => {
      if (current !== null && current.seq > seq) return current;
      // ONE observation can reach here twice — the read-acceptance effect sees
      // the new snapshot and an explicit "Check saved values" classifies the
      // same response. They are the same observation, so the OPERATOR-EXPLICIT
      // origin is kept: the comparison panel must keep naming what the check
      // found rather than degrading to the generic changed-elsewhere headline.
      const explicit = current !== null
        && current.base.revision === observed.revision
        && current.origin === 'checked';
      return { base: observed, origin: explicit ? 'checked' : origin, seq };
    });
  }, []);

  /**
   * Accept a usable read into `base` ONLY when that cannot lose operator work:
   *
   *  - no base yet            -> seed it;
   *  - same revision          -> observations only, `base` untouched — unless
   *                              an older `latest` names another revision
   *                              (see C3-A below);
   *  - revision moved, clean  -> adopt it (there is no draft to protect);
   *  - revision moved, dirty
   *    or unresolved          -> record it as `latest` and require an EXPLICIT
   *                              choice. Never a silent rebase.
   */
  useEffect(() => {
    if (snapshot === null) return;
    const observed = baseFromSnapshot(snapshot);
    const current = baseRef.current;
    if (current === null) {
      acceptBase(observed);
      return;
    }
    const observation = observationRef.current;
    const own = ownWriteRef.current;
    const ownSettlement = own === null ? null : settlementOf(own.request);
    if (observation !== null && ownSettlement !== null) {
      // Our OWN write's settlement is not an external change. The mutation's
      // `onSuccess` writes the cache, which notifies this query's observer; in
      // a real browser that notification can be delivered BEFORE the submit
      // handler's continuation runs, so this effect would otherwise see the
      // saved snapshot beside the still-dirty pre-save state and record a
      // phantom "changed elsewhere". The submit handler owns its own result.
      // C3: only that exact settlement is skipped — a second editor, or a later
      // write restoring the same revision, is a different settlement.
      if (observation.origin === 'write' && observation.settledSeq === ownSettlement.settledSeq) {
        return;
      }
      // S5-R1/R8: an observation that settled BEFORE this editor's accepted
      // write is obsolete. Only a stale render flushed late can still carry
      // one; it must not re-arm the clean state the accepted write finished.
      if (ownSettlement.outcome === 'usable' && observation.settledSeq < ownSettlement.settledSeq) {
        return;
      }
    }
    if (current.revision === observed.revision) {
      // An ordinary receipt of the revision this editor is based on changes
      // nothing: no target, no notice. C3-A: equality to the base does NOT mean
      // nothing happened since, though. When a pending reconciliation target
      // names ANOTHER revision, this later observation (another editor
      // restoring the base bytes) supersedes it like any later observation,
      // so neither explicit choice acts on an obsolete target.
      const target = latestRef.current;
      if (target === null || target.base.revision === observed.revision) return;
    }
    if (!guardRef.current.dirty && !guardRef.current.writeLocked) {
      acceptBase(observed);
      return;
    }
    recordLatest(observed, 'external');
    setExternalSeen(true);
    if (own?.pending === true && observation !== null) {
      own.observedDuring = { settledSeq: observation.settledSeq, base: observed };
    }
  }, [snapshot, acceptBase, recordLatest, settlementOf]);

  useEffect(() => {
    const warn = (event: BeforeUnloadEvent) => {
      if (!dirty) return;
      event.preventDefault();
      event.returnValue = '';
    };
    window.addEventListener('beforeunload', warn);
    return () => window.removeEventListener('beforeunload', warn);
  }, [dirty]);

  useEffect(() => {
    if (blocker.state === 'blocked' && restoreFocusRef.current === null) {
      restoreFocusRef.current = document.activeElement as HTMLElement | null;
    }
  }, [blocker.state]);

  const consequence = useMemo(() => {
    if (snapshot === null || draftPair === null) return null;
    if (inconsistentRead) return { status: 'inconsistent' as const };
    return draftConsequence(snapshot, draftPair);
  }, [snapshot, draftPair, inconsistentRead]);

  const preview = useMemo(() => {
    if (snapshot === null || draftPair === null) return null;
    return resolvedNextStart(snapshot, draftPair);
  }, [snapshot, draftPair]);

  const focusField = useCallback((field: string) => {
    queueMicrotask(() => {
      if (field === 'queue_workers') workersRef.current?.focus();
      else if (field === 'host_global_session_cap') capRef.current?.focus();
      else if (field === 'rationale') reasonRef.current?.focus();
      else if (field === 'ack') ackRef.current?.focus();
    });
  }, []);

  /**
   * The ONLY operator-explicit exit from an unresolved state. Clearing the
   * publication flag here — and nowhere else except an accepted write — is what
   * makes R2 hold: a banner change or an ordinary Discard cannot reach it.
   */
  const clearReconciliation = useCallback(() => {
    setLatest(null);
    setConflictSeen(false);
    setExternalSeen(false);
    setConflictUnusable(null);
    setUnresolvedPublication(null);
    setSubmission(null);
    setOutcome({ kind: 'idle' });
    setAckResetNotice(null);
    queueMicrotask(() => reconciledRef.current?.focus());
  }, []);

  /** Keep my draft, rebase onto latest. Sends NO request. */
  const rebaseOntoLatest = useCallback(() => {
    if (latest === null) return;
    // Only `base` moves: the draft text is kept verbatim, and `baseText` keeps
    // tracking the ORIGINAL seeded text so a rebased draft still reads dirty.
    setBase(latest.base);
    clearReconciliation();
  }, [latest, clearReconciliation]);

  /** Discard draft, accept latest. Sends NO request. */
  const acceptLatest = useCallback(() => {
    if (latest === null) return;
    acceptBase(latest.base);
    setReason('');
    setAck(false);
    clearReconciliation();
  }, [latest, acceptBase, clearReconciliation]);

  /**
   * Ordinary "Discard draft": reset the EDITOR only.
   *
   * R2: it deliberately does not touch `unresolvedPublication`, the pinned
   * submission or the reconciliation observations. Resetting the fields says
   * nothing about what the daemon persisted, so it must not unlock a write or
   * disarm the navigation guard while the outcome is unresolved.
   */
  const discardDraft = useCallback(() => {
    if (base === null) return;
    acceptBase(base);
    setReason('');
    setAck(false);
    setFieldErrors({});
    setOutcome({ kind: 'idle' });
  }, [base, acceptBase]);

  /**
   * "Check saved values" / "Refresh running state" — a read, never a write.
   *
   * R3: `refetch()` RESOLVES a `QueryObserverResult` even when the request
   * failed, and that result still carries the previous cached `.data`. Reading
   * `.data` alone therefore promoted a stale cached snapshot into a
   * reconciliation target and offered rebase against values nothing had
   * verified. Only a genuinely successful, semantically USABLE observation may
   * become `latest`; a failed, malformed or inconsistent check leaves the
   * uncertainty in place and offers retry.
   */
  const refreshObservations = useCallback(async () => {
    const result = (await query.refetch()) as RefetchOutcome | undefined;
    if (result === undefined) return;
    if (result.isError === true || result.status === 'error'
      || (result.error ?? null) !== null) {
      return;
    }
    const classified = classifySnapshot(result.data);
    if (classified.status !== 'usable') return;
    const observed = baseFromSnapshot(classified.snapshot);
    // A read after a conflict or an uncertain outcome is recorded as a `latest`
    // observation only. It never moves base and never clears the write lock.
    if (guardRef.current.writeLocked) recordLatest(observed, 'checked');
  }, [query, recordLatest]);

  async function submit(event: React.FormEvent): Promise<void> {
    event.preventDefault();
    if (base === null) return;
    // R1: refusal happens at the HANDLER as well as the control. A disabled
    // attribute is a render-time property, not a write guard — an unverified
    // or unreadable current state must never produce a PUT, whatever reached
    // the submit event.
    if (readBlocked || snapshot === null) {
      setOutcome({ kind: 'rejected', message: READ_BLOCKED_SAVE });
      return;
    }
    if (writeLocked) {
      setOutcome({
        kind: 'rejected',
        message: copy('settings.capacity.writeLocked'),
      });
      return;
    }

    // Validate input TEXT before any Number conversion (G3).
    const errors: Record<string, Copy> = {};
    const workers = parseCapacityText(workersText);
    const cap = parseCapacityText(capText);
    if (!workers.ok) {
      const rejection = workers.reason;
      errors.queue_workers = (at) =>
        numericTextMessage(rejection, capacityFieldLabel('queue_workers', at), at);
    }
    if (!cap.ok) {
      const rejection = cap.reason;
      errors.host_global_session_cap = (at) =>
        numericTextMessage(rejection, capacityFieldLabel('host_global_session_cap', at), at);
    }
    const trimmedReason = reason.trim();
    if (trimmedReason.length === 0) {
      errors.rationale = copy('settings.capacity.reasonRequired');
    } else if (trimmedReason.length > REASON_MAX_LENGTH) {
      errors.rationale = copy('settings.capacity.reasonTooLong', { max: String(REASON_MAX_LENGTH) });
    }
    if (shadowed && !ack) {
      errors.ack = copy('settings.capacity.error.confirmOverride');
    }
    setFieldErrors(errors);
    if (Object.keys(errors).length > 0) {
      const first = ['queue_workers', 'host_global_session_cap', 'rationale', 'ack']
        .find((field) => field in errors) as string;
      // The summary announces THAT something is wrong; the field-associated
      // error says WHAT. Repeating the same sentence twice would make the
      // announcement redundant with the control's own description.
      setOutcome({
        kind: 'rejected',
        message: copy('settings.capacity.checkFields'),
      });
      focusField(first);
      return;
    }
    if (!workers.ok || !cap.ok) return;

    // Capture the submission immutably BEFORE the await, so the callback reports
    // what was sent — never what is in the fields when the response lands.
    const record: CapacitySubmission = {
      pair: { queue_workers: workers.value, host_global_session_cap: cap.value },
      baseRevision: base.revision,
      reason: trimmedReason,
      ack,
    };
    setSubmission(record);
    setOutcome({ kind: 'saving' });
    // C3: THIS request object is what identifies this editor's own write; the
    // provider records the settlement it produced under it.
    const request: DaemonCapacityWrite = {
      revision: record.baseRevision,
      queue_workers: record.pair.queue_workers,
      host_global_session_cap: record.pair.host_global_session_cap,
      rationale: record.reason,
      confirm_environment_shadow: record.ack,
    };
    ownWriteRef.current = { request, pending: true, observedDuring: null };
    let accepted = false;
    try {
      const result = await save.mutateAsync(request);
      const classified = classifySnapshot(result);
      if (classified.status !== 'usable') {
        // A 200 we cannot read — or one whose arithmetic contradicts itself —
        // is an UNKNOWN outcome, not a success, and it stays unresolved until
        // an explicit reconciliation.
        setUnresolvedPublication({ kind: 'unknown', message: UNKNOWN_OUTCOME_COPY });
        setOutcome({ kind: 'idle' });
        return;
      }
      // R8: an accepted write finishes the whole transition. Reconciliation
      // state recorded while it was pending from an observation that settled
      // BEFORE this write is obsolete by the accepted ordering rule (S5-R1/R8)
      // and is fenced here. One that settled AFTER this write is genuinely
      // newer: the editor is clean once this write is accepted, so it adopts
      // that observation instead of losing it. Anything settling later still is
      // handled by the read-acceptance effect, as for any clean editor.
      const own = ownWriteRef.current;
      const ownSettlement = own?.request === request ? settlementOf(request) : null;
      const newer = own?.request === request && own.observedDuring !== null
        && ownSettlement !== null && own.observedDuring.settledSeq > ownSettlement.settledSeq
        ? own.observedDuring.base
        : null;
      accepted = true;
      acceptBase(baseFromSnapshot(classified.snapshot));
      setReason('');
      setFieldErrors({});
      setSubmission(null);
      setLatest(null);
      setConflictSeen(false);
      setExternalSeen(false);
      setConflictUnusable(null);
      setUnresolvedPublication(null);
      setAckResetNotice(null);
      setOutcome({ kind: 'saved', snapshot: classified.snapshot });
      if (newer !== null) acceptBase(newer);
    } catch (error) {
      if (error instanceof ApiError && error.code === 'stale_revision') {
        const conflictBody = (error.detail as { latest?: unknown } | null)?.latest;
        const classifiedLatest = classifySnapshot(conflictBody);
        if (classifiedLatest.status !== 'usable') {
          setConflictUnusable(
            classifiedLatest.status === 'unusable' && classifiedLatest.reason === 'representation'
              ? 'settings.capacity.conflict.representation'
              : 'settings.capacity.conflict.unreadable',
          );
        } else {
          recordLatest(baseFromSnapshot(classifiedLatest.snapshot), 'conflict');
          setConflictUnusable(null);
        }
        setConflictSeen(true);
        setOutcome({
          kind: 'rejected',
          message: copy('settings.capacity.conflict.changed'),
        });
        return;
      }
      const classified = classifySaveError(error);
      if (classified.kind === 'uncertain' || classified.kind === 'unknown') {
        // R2: the unresolved fact is stored separately from the banner, so a
        // later refusal message cannot erase it and unlock the next write.
        setUnresolvedPublication({ kind: classified.kind, message: classified.message });
        setOutcome({ kind: 'idle' });
        return;
      }
      setOutcome(classified);
      if (classified.kind === 'rejected' && classified.focus) focusField(classified.focus);
    } finally {
      // A request that produced no accepted settlement owns nothing: every
      // external observation it overlapped was already processed normally, so
      // releasing it loses nothing. An accepted one keeps identifying exactly
      // its own settlement for a late-flushed render.
      if (ownWriteRef.current?.request === request) {
        if (accepted) {
          ownWriteRef.current.pending = false;
          ownWriteRef.current.observedDuring = null;
        } else {
          ownWriteRef.current = null;
        }
      }
    }
  }

  // The page header is rendered in EVERY state. A loading, denied or unusable
  // read must not blank the surface: the operator still needs to know which
  // page they are on and what the failure was.
  const header = (
    <header>
      <h2 id="capacity-panel-heading" className="font-display text-text-primary text-2xl font-medium">{t('settings.capacity.title')}</h2>
      <p className="text-text-secondary mt-1 max-w-prose text-sm">
        {t('settings.capacity.description')}
      </p>
      <div className="mt-3 flex flex-wrap items-center gap-2">
        <Pill tone="neutral">{t('settings.capacity.pill.allOrgs')}</Pill>
        <Pill tone="neutral">{t('settings.capacity.pill.restartRequired')}</Pill>
      </div>
      {/* 16.10: informational prose uses the secondary text token, which
          measures >= 4.5:1 in both themes. The muted token is retained only
          for decorative overlines and key names. */}
      <p className="text-text-secondary mt-3 text-sm">
        {t('settings.capacity.bearer')}
      </p>
    </header>
  );

  if (query.isLoading) {
    return (
      <div className="space-y-2">
        {header}
        <p role="status" className="text-text-secondary mt-4 text-sm">{t('settings.capacity.loading')}</p>
      </div>
    );
  }
  if ((query.isError || query.data === undefined) && base === null) {
    return (
      <div className="space-y-2">
        {header}
        {/* 16.10: `text-feedback-danger` measures 4.3:1 on `bg-danger-soft`,
            just under AA. The shared token and the shared danger surface are
            out of scope for this bounded screen, so the capacity alerts carry
            their text in the already-authorized primary text token while the
            danger surface and border keep signalling severity. 16.9 still
            holds: the meaning is carried by words, never by colour alone. */}
        <p role="alert" className="border-border-default bg-danger-soft text-text-primary mt-4 rounded-md border p-3 text-sm">
          {t('settings.capacity.loadError', { detail: query.error?.message ?? '' })}
        </p>
      </div>
    );
  }
  if (snapshot === null && base === null) {
    // No previously accepted values exist, so there is nothing to fall back to.
    // The reason still has to be truthful: a representation failure is not the
    // same defect as a malformed shape.
    return (
      <div className="space-y-2">
        {header}
        <p role="alert" className="border-border-default bg-attention-soft text-attention-text mt-4 rounded-md border p-3 text-sm">
          {readUnusableReason === 'representation'
            ? t('settings.capacity.initialRepresentation')
            : t('settings.capacity.readUnusable')}
        </p>
      </div>
    );
  }

  // C1: the receipt rendered beside the values must belong to the response that
  // produced THOSE values. The provider's `receiptAt` advances on every genuine
  // successful response (S5-R5), including one whose body is unusable, so while
  // retained values are displayed the RETAINED receipt is the honest one — the
  // later response's receipt must not be attributed to them. The two facts stay
  // distinguishable: the unusable-response banner names the current read's
  // failure, and the retained values carry their own "Last received" time.
  const currentReceipt = formatReceipt(query.observation?.receiptAt ?? null, locale);
  const retainedReceipt = formatReceipt(lastUsable?.receiptAt ?? null, locale);
  const pending = save.isPending;
  const reconciliationNeeded = conflictSeen || conflictUnusable !== null || externalSeen;
  // R3: reconciliation may only be offered against an observation that was
  // actually read successfully and classified usable. A failed or unusable
  // read promotes nothing.
  const canReconcile = latest !== null && !readBlocked;
  /**
   * The values to display. When the current read is unusable or failed, the
   * last USABLE observation is still shown — labelled, with its own receipt —
   * rather than blanking the surface (14.1 / 14.2 / 14.3).
   */
  const displaySnapshot = displayedSnapshot;
  const showingLastKnown = displaySnapshot !== null && (snapshot === null || refreshFailed);
  /** The receipt of the response that produced the values currently displayed. */
  const valueReceipt = showingLastKnown ? retainedReceipt : currentReceipt;
  // Deliberately NOT disabled by `writeLocked`: a second Save attempt must be
  // explicitly REFUSED with a reason, not silently inert — and never a silent
  // last-write-wins. It IS disabled while the current state is unreadable or
  // unverified (R1) and while a required acknowledgment is missing (4.1b).
  const saveDisabled = pending || snapshot === null || readBlocked
    || (shadowed && !ack);

  return (
    <div className="space-y-2">
      {header}

      {readUnusableReason !== null && (
        <p role="alert" className="border-border-default bg-attention-soft text-attention-text mt-4 rounded-md border p-3 text-sm">
          {readUnusableReason === 'representation'
            ? t('settings.capacity.latestRepresentation')
            : t('settings.capacity.readUnusable')}
          {' '}{t('settings.capacity.lastKnownPointer')}
          {valueReceipt ? ` ${valueReceipt}` : ''}
        </p>
      )}
      {inconsistentRead && (
        <p role="alert" className="border-border-default bg-attention-soft text-attention-text mt-4 rounded-md border p-3 text-sm">
          {t('settings.capacity.inconsistentResponse')} {t('settings.capacity.editingUnavailableRead')}
        </p>
      )}
      {refreshFailed && (
        <p role="alert" className="border-border-default bg-attention-soft text-attention-text mt-4 rounded-md border p-3 text-sm">
          {t('settings.capacity.refreshFailed')} {t('settings.capacity.lastKnownPointer')}
          {valueReceipt ? ` ${valueReceipt}` : ''}
          {' '}{t('settings.capacity.refreshFailedKept')}
        </p>
      )}

      {displaySnapshot !== null && (
        <>
          <SectionLabel
            note={showingLastKnown
              ? t('settings.capacity.running.noteLastKnown')
              : t('settings.capacity.running.noteObserved')}
          >
            {showingLastKnown ? t('settings.capacity.running.lastKnown') : t('settings.capacity.running.now')}
          </SectionLabel>
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <RunningCard
              label={t('settings.capacity.card.workersLabel')}
              value={String(displaySnapshot.running_at_daemon_start.queue_workers)}
              description={t('settings.capacity.card.workersDescription')}
            />
            <RunningCard
              label={t('settings.capacity.card.capLabel')}
              value={
                displaySnapshot.effective_admission_cap === null
                  ? t('settings.capacity.card.unavailable')
                  : String(displaySnapshot.effective_admission_cap)
              }
              description={
                displaySnapshot.effective_admission_cap === null
                  ? t('settings.capacity.card.capUnavailableDescription', {
                    reason: displaySnapshot.effective_admission_reason,
                  })
                  : t('settings.capacity.card.capDescription')
              }
            />
          </div>
          {/* The reason is shown where it ADDS something: when the cap is
              unavailable it is already in the card, and when the cap simply
              matches the startup value there is nothing to explain. */}
          {displaySnapshot.effective_admission_cap !== null
            && displaySnapshot.effective_admission_cap !== displaySnapshot.running_at_daemon_start.host_global_session_cap && (
            <p className="text-text-secondary mt-2 text-sm">
              {t('settings.capacity.effectiveDiffers', {
                startup: String(displaySnapshot.running_at_daemon_start.host_global_session_cap),
                effective: String(displaySnapshot.effective_admission_cap),
                reason: displaySnapshot.effective_admission_reason,
              })}
            </p>
          )}

          <SectionLabel>{t('settings.capacity.table.title')}</SectionLabel>
          <table className="border-border-default w-full border-collapse overflow-hidden rounded-md border text-sm">
            <thead>
              <tr className="text-text-secondary bg-surface-sunken text-left text-xs tracking-wide uppercase">
                <th scope="col" className="border-border-default border-b p-3 font-semibold">{t('settings.capacity.table.setting')}</th>
                <th scope="col" className="border-border-default border-b p-3 font-semibold">{t('settings.capacity.table.running')}</th>
                <th scope="col" className="border-border-default border-b p-3 font-semibold">{t('settings.capacity.table.saved')}</th>
                <th scope="col" className="border-border-default border-b p-3 font-semibold">{t('settings.capacity.table.next')}</th>
              </tr>
            </thead>
            <tbody>
              {([
                ['queue_workers', workersLabel] as const,
                ['host_global_session_cap', capLabel] as const,
              ]).map(([key, label]) => (
                <tr key={key}>
                  <th scope="row" className="border-border-default border-b p-3 text-left font-medium">
                    {label}
                  </th>
                  <td className="border-border-default border-b p-3 font-mono">
                    {displaySnapshot.running_at_daemon_start[key]}
                  </td>
                  <td className="border-border-default border-b p-3 font-mono">
                    {displaySnapshot.persisted_yaml[key] === null ? t('settings.capacity.notSetInFile') : displaySnapshot.persisted_yaml[key]}
                  </td>
                  <td className="border-border-default border-b p-3 font-mono">
                    {displaySnapshot.next_start[key]}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="text-text-secondary mt-2 text-sm">
            {t('settings.capacity.table.note')}
          </p>
          <div className="mt-3">
            <Pill tone="accent">
              <span aria-hidden="true">●</span>
              {displaySnapshot.restart_pending ? t('settings.capacity.restartPending') : t('settings.capacity.noRestartPending')}
            </Pill>
          </div>
          {displaySnapshot.restart_pending && (
            <p role="status" className="text-text-secondary mt-2 text-sm">
              {t('settings.capacity.restartPendingNote')}
            </p>
          )}
          {/* 18.5: an equality STATEMENT about the observed values. It never
              says a restart occurred, succeeded, or was caused by this page. */}
          {!displaySnapshot.restart_pending && runningMatchesSaved(displaySnapshot) && (
            <p role="status" className="text-text-secondary mt-2 text-sm">
              {t('settings.capacity.runningMatches')}
            </p>
          )}
          {displaySnapshot.warnings.map((warning) => (
            <p key={warning} role="alert" className="border-border-default bg-attention-soft text-attention-text mt-2 rounded-md border p-3 text-sm">
              {warning}
            </p>
          ))}
        </>
      )}

      {/* The receipt footer names the response behind the DISPLAYED values. While
          retained values are shown it repeats their own retained receipt (the
          banner already names it too); the newer unusable response's receipt is
          deliberately NOT substituted for it (C1). */}
      {valueReceipt && (
        <p className="text-text-secondary mt-3 text-xs">{valueReceipt}</p>
      )}

      <form onSubmit={submit} noValidate>
        <SectionLabel>{t('settings.capacity.change.title')}</SectionLabel>

        <div className="border-border-default border-b pb-5">
          <label htmlFor="capacity-workers" className="text-text-primary text-sm font-semibold">
            {workersLabel}
          </label>
          <p id="capacity-workers-help" className="text-text-secondary mt-1 max-w-prose text-sm">
            {t('settings.capacity.workersHelp')}
          </p>
          <div className="mt-2 flex flex-wrap items-center gap-3">
            <Input
              id="capacity-workers"
              ref={workersRef}
              type="text"
              inputMode="numeric"
              autoComplete="off"
              className={`w-28 font-mono ${FOCUS_RING}`}
              value={workersText}
              disabled={pending}
              aria-describedby={
                fieldErrors.queue_workers
                  ? 'capacity-workers-help capacity-workers-guidance capacity-workers-error'
                  : 'capacity-workers-help capacity-workers-guidance'
              }
              aria-invalid={fieldErrors.queue_workers ? true : undefined}
              onChange={(event) => setWorkersText(event.target.value)}
            />
            <p id="capacity-workers-guidance" className="text-text-secondary text-sm">
              {displayedSnapshot?.guidance.queue_workers}
            </p>
            {fieldErrors.queue_workers && (
              <p id="capacity-workers-error" role="alert" className="text-feedback-danger text-sm">
                {fieldErrors.queue_workers(locale)}
              </p>
            )}
          </div>
        </div>

        <div className="border-border-default border-b py-5">
          <label htmlFor="capacity-cap" className="text-text-primary text-sm font-semibold">
            {capLabel}
          </label>
          <p id="capacity-cap-help" className="text-text-secondary mt-1 max-w-prose text-sm">
            {t('settings.capacity.capHelp')}
          </p>
          <div className="mt-2 flex flex-wrap items-center gap-3">
            <Input
              id="capacity-cap"
              ref={capRef}
              type="text"
              inputMode="numeric"
              autoComplete="off"
              className={`w-28 font-mono ${FOCUS_RING}`}
              value={capText}
              disabled={pending}
              aria-describedby={
                fieldErrors.host_global_session_cap
                  ? 'capacity-cap-help capacity-cap-guidance capacity-cap-error'
                  : 'capacity-cap-help capacity-cap-guidance'
              }
              aria-invalid={fieldErrors.host_global_session_cap ? true : undefined}
              onChange={(event) => setCapText(event.target.value)}
            />
            <p id="capacity-cap-guidance" className="text-text-secondary text-sm">
              {displayedSnapshot?.guidance.host_global_session_cap}
            </p>
            {fieldErrors.host_global_session_cap && (
              <p id="capacity-cap-error" role="alert" className="text-feedback-danger text-sm">
                {fieldErrors.host_global_session_cap(locale)}
              </p>
            )}
          </div>
        </div>

        {/* Draft consequence panel. 6.3: rationale-only dirty gets NO
            value-comparison/consequence panel — only the copy below. */}
        {consequence !== null && !rationaleOnlyDirty && (
          <div className="border-border-default bg-surface-raised mt-5 rounded-md border">
            <p className="border-border-default text-text-primary border-b p-3 text-sm font-medium">
              {valuesChanged ? t('settings.capacity.draft.changes') : t('settings.capacity.draft.matches')}
            </p>
            {consequence.status === 'ok' && draftPair !== null ? (
              <>
                <div className="flex flex-wrap gap-8 p-4">
                  <div>
                    <p className="text-text-secondary text-sm">{workersLabel}</p>
                    <p className="font-display text-text-primary mt-1 text-xl">{draftPair.queue_workers}</p>
                  </div>
                  <div>
                    <p className="text-text-secondary text-sm">{t('settings.capacity.draft.capLabel')}</p>
                    <p className="font-display text-text-primary mt-1 text-xl">{draftPair.host_global_session_cap}</p>
                  </div>
                  <div>
                    <p className="text-text-secondary text-sm">{t('settings.capacity.draft.poolTotal')}</p>
                    <p className="font-display text-text-primary mt-1 text-xl">
                      {consequence.workerPoolTotal}
                      {/* R6: the arithmetic and its explanation use the RESOLVED
                          per-key next-start values. Under a W override the pool
                          is built from the environment-resolved W, not the draft
                          the environment will shadow. */}
                      <span className="text-text-secondary ml-2 font-sans text-sm">
                        {t('settings.capacity.draft.poolBreakdown', {
                          task: String(consequence.resolved.queue_workers),
                          other: String(consequence.nonTaskContribution),
                        })}
                      </span>
                    </p>
                  </div>
                </div>
                <p className="border-border-default bg-surface-sunken text-text-secondary m-4 mt-0 rounded-md border p-3 text-sm">
                  {consequenceMessage(
                    consequence.direction,
                    consequence.resolved.host_global_session_cap,
                    consequence.workerPoolTotal,
                    locale,
                  )}
                </p>
              </>
            ) : (
              <p role="alert" className="text-attention-text p-4 text-sm">
                {consequence.status === 'inconsistent'
                  ? t('settings.capacity.inconsistentResponse')
                  : t('settings.capacity.draft.poolRepresentation')}
              </p>
            )}
          </div>
        )}

        {rationaleOnlyDirty && (
          <p role="status" className="text-text-secondary mt-3 text-sm">
            {t('settings.capacity.rationaleOnly')}
          </p>
        )}

        {/* Reason */}
        <div className="mt-5">
          <div className="flex items-baseline justify-between">
            <label htmlFor="capacity-reason" className="text-text-primary text-sm font-semibold">
              {t('settings.capacity.reason.label')}
            </label>
            <span
              className="text-text-secondary font-mono text-xs"
              role="status"
              aria-live="polite"
            >
              {reason.length} / {REASON_MAX_LENGTH}
              {reason.length >= REASON_MAX_LENGTH ? t('settings.capacity.reason.limitReached') : ''}
            </span>
          </div>
          <p id="capacity-reason-help" className="text-text-secondary mt-1 max-w-prose text-sm">
            {t('settings.capacity.reason.help')}
          </p>
          <Textarea
            id="capacity-reason"
            ref={reasonRef}
            className={`mt-2 ${FOCUS_RING}`}
            value={reason}
            disabled={pending}
            placeholder={t('settings.capacity.reason.placeholder')}
            aria-describedby={
              fieldErrors.rationale ? 'capacity-reason-help capacity-reason-error' : 'capacity-reason-help'
            }
            aria-invalid={fieldErrors.rationale ? true : undefined}
            onChange={(event) => setReason(event.target.value)}
          />
          {fieldErrors.rationale && (
            <p id="capacity-reason-error" role="alert" className="text-feedback-danger mt-1 text-sm">
              {fieldErrors.rationale(locale)}
            </p>
          )}
        </div>

        {/* Environment override.

            Placed AFTER the reason so the keyboard tab order is the accepted
            16.6 order: W -> H -> reason -> acknowledgment -> Save -> Discard ->
            Refresh -> Capacity details. */}
        {shadowed && displayedSnapshot !== null && (
          <div id="capacity-override" role="group" aria-labelledby="capacity-override-heading" className="border-border-default bg-attention-soft mt-5 rounded-md border p-4">
            <p id="capacity-override-heading" className="text-attention-text text-sm font-semibold">
              {t('settings.capacity.override.heading')}
            </p>
            <p className="text-text-secondary mt-1 text-sm">{displayedSnapshot.environment_warning}</p>
            {preview !== null && snapshot !== null ? (
              <p className="text-text-secondary mt-2 text-sm">
                {t('settings.capacity.override.shadowed', {
                  count: preview.shadowedKeys.length,
                  fields: preview.shadowedKeys
                    .map((key) => capacityFieldLabel(key, locale))
                    .join(t('settings.capacity.fieldJoiner')),
                })}
                {' '}{t('settings.capacity.override.preview', {
                  workersLabel,
                  workers: String(preview.pair.queue_workers),
                  capLabel,
                  cap: String(preview.pair.host_global_session_cap),
                })}
              </p>
            ) : (
              <p className="text-text-secondary mt-2 text-sm">
                {t('settings.capacity.override.noPreview')}
              </p>
            )}
            {ackResetNotice && (
              <p role="alert" className="text-attention-text mt-2 text-sm font-medium">{t(ackResetNotice)}</p>
            )}
            <label className="text-text-primary mt-3 flex items-start gap-2 text-sm">
              <input
                ref={ackRef}
                type="checkbox"
                className={FOCUS_RING_RAW}
                checked={ack}
                disabled={pending}
                aria-describedby={fieldErrors.ack ? 'capacity-ack-error' : undefined}
                aria-invalid={fieldErrors.ack ? true : undefined}
                onChange={(event) => {
                  setAck(event.target.checked);
                  if (event.target.checked) setAckResetNotice(null);
                }}
              />
              <span>{t('settings.capacity.override.ack')}</span>
            </label>
            {fieldErrors.ack && (
              <p id="capacity-ack-error" role="alert" className="text-feedback-danger mt-1 text-sm">
                {fieldErrors.ack(locale)}
              </p>
            )}
          </div>
        )}

        <div className="border-border-default mt-5 flex flex-wrap items-center gap-3 border-t pt-5">
          <Button
            type="submit"
            className={`${PRIMARY_TONE} ${FOCUS_RING}`}
            disabled={saveDisabled}
            loading={pending}
          >
            {pending ? t('settings.capacity.saving') : t('settings.capacity.save')}
          </Button>
          <Button type="button" variant="outline" className={FOCUS_RING} disabled={pending} onClick={discardDraft}>
            {t('settings.capacity.discard')}
          </Button>
          <Button type="button" variant="ghost" className={FOCUS_RING} disabled={pending} onClick={() => void refreshObservations()}>
            <RotateCcw aria-hidden="true" />
            {t('settings.capacity.refresh')}
          </Button>
        </div>
        {dirty && (
          <p role="status" className="text-text-secondary mt-3 text-sm">
            {t('settings.capacity.unsaved')}
          </p>
        )}

        {/* Outcome */}
        <div id="capacity-outcome" ref={reconciledRef} tabIndex={-1} className="mt-4 space-y-3 outline-none">
          {outcome.kind === 'saving' && (
            <p role="status" aria-live="polite" className="text-text-secondary text-sm">
              {t('settings.capacity.outcome.saving')}
            </p>
          )}
          {outcome.kind === 'saved' && (
            <div role="status" aria-live="polite" className="border-border-default bg-accent-muted text-accent-text rounded-md border p-3 text-sm">
              <p className="font-medium">
                {outcome.snapshot.restart_pending
                  ? t('settings.capacity.outcome.savedPending')
                  : t('settings.capacity.outcome.savedNoPending')}
              </p>
              {snapshot !== null && snapshot.environment_shadowed.length > 0 && preview !== null && (
                <p className="mt-1">
                  {t('settings.capacity.outcome.overridden', {
                    count: preview.shadowedKeys.length,
                    fields: preview.shadowedKeys
                      .map((key) => capacityFieldLabel(key, locale))
                      .join(t('settings.capacity.fieldJoiner')),
                    workersLabel,
                    workers: String(outcome.snapshot.next_start.queue_workers),
                    capLabel,
                    cap: String(outcome.snapshot.next_start.host_global_session_cap),
                  })}
                </p>
              )}
              {outcome.snapshot.message && <p className="mt-1">{outcome.snapshot.message}</p>}
            </div>
          )}
          {outcome.kind === 'rejected' && (
            <p role="alert" className="border-border-default bg-danger-soft text-text-primary rounded-md border p-3 text-sm">
              {outcome.message(locale)}
            </p>
          )}
          {/* R2: rendered from its own state, so it stays on screen through a
              refused Save and an ordinary Discard. The refusal message above
              appears BESIDE it, never instead of it. */}
          {unresolvedPublication !== null && (
            <p role="alert" className="border-border-default bg-attention-soft text-attention-text rounded-md border p-3 text-sm">
              {unresolvedPublication.message(locale)}
            </p>
          )}

          {submission !== null && outcome.kind !== 'saving' && (
            <div className="border-border-default rounded-md border p-3 text-sm">
              <p className="text-text-primary font-medium">
                {render('settings.capacity.submitted', {
                  workersLabel,
                  workers: String(submission.pair.queue_workers),
                  capLabel,
                  cap: String(submission.pair.host_global_session_cap),
                  revision: <code className="font-mono text-xs break-all">{submission.baseRevision}</code>,
                })}
              </p>
              {valuesChanged && (
                <p className="text-text-secondary mt-1">
                  {t('settings.capacity.draftHeld', { workersLabel, workers: workersText, capLabel, cap: capText })}
                </p>
              )}
              <Button
                type="button"
                variant="outline"
                size="sm"
                className={`mt-2 ${FOCUS_RING}`}
                onClick={() => void refreshObservations()}
              >
                {t('settings.capacity.checkSaved')}
              </Button>
            </div>
          )}

          {conflictUnusable !== null && (
            <p role="alert" className="border-border-default rounded-md border p-3 text-sm">
              {t(conflictUnusable)} {t('settings.capacity.conflictUnusableSuffix')}
            </p>
          )}

          {(reconciliationNeeded || latest !== null) && base !== null && (
            <div className="border-border-default rounded-md border p-3 text-sm">
              <p className="text-text-primary font-medium">
                {latest !== null && latest.origin === 'checked' && submission !== null
                  ? comparisonHeadline(submission, base, latest.base, draftPair, locale)
                  : t('settings.capacity.changedElsewhere')}
              </p>
              <dl className="mt-2 grid grid-cols-1 gap-1 sm:grid-cols-3">
                <div>
                  <dt className="text-text-secondary text-xs tracking-wide uppercase">{t('settings.capacity.compare.acceptedBase')}</dt>
                  <dd className="font-mono">
                    {t('settings.capacity.compare.pair', {
                      workersLabel,
                      workers: formatPresenceValue(base.pair.queue_workers, base.keyPresence.queue_workers, locale),
                      capLabel,
                      cap: formatPresenceValue(
                        base.pair.host_global_session_cap,
                        base.keyPresence.host_global_session_cap,
                        locale,
                      ),
                    })}
                  </dd>
                </div>
                <div>
                  <dt className="text-text-secondary text-xs tracking-wide uppercase">{t('settings.capacity.compare.yourDraft')}</dt>
                  <dd className="font-mono">
                    {t('settings.capacity.compare.pair', { workersLabel, workers: workersText, capLabel, cap: capText })}
                  </dd>
                </div>
                <div>
                  <dt className="text-text-secondary text-xs tracking-wide uppercase">{t('settings.capacity.compare.currentlySaved')}</dt>
                  <dd className="font-mono">
                    {(() => {
                      // R4: the NEWEST accepted observation, whatever produced
                      // it — never a stale conflict body preferred by slot.
                      if (latest === null) return t('settings.capacity.compare.couldNotBeRead');
                      const observed = latest.base;
                      return t('settings.capacity.compare.pair', {
                        workersLabel,
                        workers: formatPresenceValue(
                          observed.pair.queue_workers,
                          observed.keyPresence.queue_workers,
                          locale,
                        ),
                        capLabel,
                        cap: formatPresenceValue(
                          observed.pair.host_global_session_cap,
                          observed.keyPresence.host_global_session_cap,
                          locale,
                        ),
                      });
                    })()}
                  </dd>
                </div>
              </dl>
              {canReconcile && (
                <div className="mt-3 flex flex-wrap gap-2">
                  <Button type="button" size="sm" variant="outline" className={FOCUS_RING} onClick={rebaseOntoLatest}>
                    {t('settings.capacity.rebase')}
                  </Button>
                  <Button type="button" size="sm" variant="outline" className={FOCUS_RING} onClick={acceptLatest}>
                    {t('settings.capacity.acceptLatest')}
                  </Button>
                </div>
              )}
            </div>
          )}
        </div>
      </form>

      {/* Collapsed details. Every important warning above stays outside this. */}
      <details
        open={detailsOpen}
        onToggle={(event) => setDetailsOpen((event.target as HTMLDetailsElement).open)}
        className="border-border-default bg-surface-raised mt-6 rounded-md border"
      >
        <summary className={`text-text-primary cursor-pointer p-4 text-sm font-medium ${FOCUS_RING_RAW}`}>{t('settings.capacity.details.summary')}</summary>
        {snapshot !== null && (
          <dl className="text-text-secondary grid grid-cols-1 gap-2 p-4 pt-0 text-sm sm:grid-cols-2">
            <dt>{t('settings.capacity.details.configurationKeys')}</dt>
            <dd className="font-mono text-xs">queue_workers · host_global_session_cap</dd>
            <dt>{t('settings.capacity.details.producerEnvelope')}</dt>
            <dd className="font-mono">{snapshot.producer_envelope}</dd>
            <dt>{t('settings.capacity.details.producerComponents')}</dt>
            <dd className="font-mono">
              {t('settings.capacity.details.componentsValue', {
                task: String(snapshot.producer_components.task_workers),
                thread: String(snapshot.producer_components.thread_workers),
                dream: String(snapshot.producer_components.dream_workers),
                wake: String(snapshot.producer_components.wake_workers),
                schedule: String(snapshot.producer_components.schedule_workers),
              })}
            </dd>
            <dt>{t('settings.capacity.details.runningProvenance')}</dt>
            <dd>{snapshot.running_provenance}</dd>
            <dt>{t('settings.capacity.details.revision')}</dt>
            <dd className="font-mono text-xs break-all">{snapshot.revision}</dd>
            <dt>{t('settings.capacity.details.audit')}</dt>
            <dd>
              {t('settings.capacity.details.auditBody')}
            </dd>
          </dl>
        )}
      </details>

      <Dialog
        open={blocker.state === 'blocked'}
        onOpenChange={(open) => {
          if (!open && blocker.state === 'blocked') blocker.reset();
        }}
      >
        <DialogContent className={DIALOG_FOCUS_RING}>
          <DialogHeader>
            <DialogTitle>{t('settings.capacity.dialog.title')}</DialogTitle>
            <DialogDescription>
              {t('settings.capacity.dialog.description')}
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button
              size="sm"
              variant="ghost"
              className={FOCUS_RING}
              onClick={() => {
                if (blocker.state === 'blocked') blocker.reset();
                const restore = restoreFocusRef.current;
                restoreFocusRef.current = null;
                queueMicrotask(() => restore?.focus());
              }}
            >
              {t('settings.capacity.dialog.stay')}
            </Button>
            <Button
              size="sm"
              className={`${PRIMARY_TONE} ${FOCUS_RING}`}
              onClick={() => {
                restoreFocusRef.current = null;
                if (blocker.state === 'blocked') blocker.proceed();
              }}
            >
              {t('settings.capacity.dialog.discardContinue')}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

/**
 * Name the record the read actually matches — submission, draft or base — and
 * never collapse a match into a claim that the save succeeded.
 */
function comparisonHeadline(
  submission: CapacitySubmission,
  base: CapacityBase,
  read: CapacityBase,
  draft: { queue_workers: number; host_global_session_cap: number } | null,
  locale: Locale,
): string {
  const matches = (pair: { queue_workers: number; host_global_session_cap: number }) =>
    read.pair.queue_workers === pair.queue_workers
    && read.pair.host_global_session_cap === pair.host_global_session_cap
    && read.keyPresence.queue_workers && read.keyPresence.host_global_session_cap;
  if (!read.keyPresence.queue_workers || !read.keyPresence.host_global_session_cap) {
    return translate(locale, 'settings.capacity.headline.notSet');
  }
  if (matches(submission.pair)) {
    return translate(locale, 'settings.capacity.headline.matchesSubmitted', {
      workers: String(submission.pair.queue_workers),
      cap: String(submission.pair.host_global_session_cap),
    });
  }
  // 11.4: a reread matching the CURRENT DRAFT is a different fact from a
  // reread matching the submission. Both relations are stated, and neither is
  // collapsed into "saved".
  if (draft !== null && matches(draft)) {
    return translate(locale, 'settings.capacity.headline.matchesDraft', {
      draftWorkers: String(draft.queue_workers),
      draftCap: String(draft.host_global_session_cap),
      workers: String(submission.pair.queue_workers),
      cap: String(submission.pair.host_global_session_cap),
    });
  }
  if (matches(base.pair)) {
    return translate(locale, 'settings.capacity.headline.unchanged');
  }
  return translate(locale, 'settings.capacity.headline.differs');
}

/**
 * 18.5: does the observed running startup pair equal the saved-in-file pair?
 * Absent keys are never equality with a present value.
 */
function runningMatchesSaved(snapshot: DaemonCapacitySnapshot): boolean {
  return snapshot.persisted_yaml.queue_workers !== null
    && snapshot.persisted_yaml.host_global_session_cap !== null
    && snapshot.persisted_yaml.queue_workers === snapshot.running_at_daemon_start.queue_workers
    && snapshot.persisted_yaml.host_global_session_cap
      === snapshot.running_at_daemon_start.host_global_session_cap;
}
