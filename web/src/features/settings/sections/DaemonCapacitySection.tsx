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
import { ApiError } from '@/lib/api';
import type { DaemonCapacitySnapshot } from '@/lib/api/types';
import {
  ackContextOf,
  baseFromSnapshot,
  CAPACITY_FIELD_LABELS,
  classifySnapshot,
  consequenceMessage,
  draftConsequence,
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

const WORKERS_LABEL = CAPACITY_FIELD_LABELS.queue_workers;
const CAP_LABEL = CAPACITY_FIELD_LABELS.host_global_session_cap;

const REPRESENTATION_UNAVAILABLE =
  'Outside the range this editor can represent exactly.';
const READ_UNUSABLE =
  'Cannot read capacity configuration. Editing is unavailable.';
const INCONSISTENT_RESPONSE =
  'Capacity details are inconsistent in this response.';

/** Outcome of a settled save attempt. */
type SaveOutcome =
  | { kind: 'idle' }
  | { kind: 'saving' }
  /** A usable 200 was accepted into base. */
  | { kind: 'saved'; snapshot: DaemonCapacitySnapshot }
  /** A typed rejection: the request did not publish. */
  | { kind: 'rejected'; message: string; focus?: 'queue_workers' | 'host_global_session_cap' | 'rationale' | 'ack' }
  /** Typed publication-uncertain: known replacement wording. */
  | { kind: 'uncertain'; message: string }
  /** Lost response, unclassified 5xx, network error, or an unusable success. */
  | { kind: 'unknown'; message: string };

const UNKNOWN_OUTCOME_COPY =
  'Save result unknown. Your draft is retained. Reconnect and check saved values before trying again.';

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
  const artifact = detail.artifact_state === 'present'
    ? ' A temporary artifact remains; inspect it before cleanup.'
    : detail.artifact_state === 'unknown'
      ? ' Temporary artifact state is unknown; inspect it before cleanup.'
      : '';

  if (error.status === 401 || error.status === 403) {
    return {
      kind: 'rejected',
      message: 'Unauthorized. A valid daemon bearer is required; no values were changed.',
    };
  }
  if (error.code === 'environment_confirmation_required') {
    return {
      kind: 'rejected',
      message: 'Confirm the environment override before saving.',
      focus: 'ack',
    };
  }
  if (error.status === 422) {
    return {
      kind: 'rejected',
      message: `The daemon rejected these values. ${WORKERS_LABEL} and ${CAP_LABEL} must each be a whole number greater than zero, and the reason must not be blank.`,
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
      message: 'Refresh the saved settings before saving again. This request did not publish new values.',
    };
  }
  if (error.code === 'audit_failed') {
    return {
      kind: 'rejected',
      message: 'Could not record the change. This request did not change the configuration.',
    };
  }
  if (error.code === 'config_write_failed') {
    // Pre-publication failure: state what THIS REQUEST did not publish. Making
    // a claim about the file's current contents would exclude external writers.
    return {
      kind: 'rejected',
      message: `Configuration storage failed. This request did not publish new values.${artifact}`,
    };
  }
  if (error.code === 'config_publication_uncertain') {
    return {
      kind: 'uncertain',
      message: `The new configuration was published, but durability, verification, or cleanup did not complete. This is not a confirmation that your values are in effect for the next start. Check the saved values before retrying.${artifact}`,
    };
  }
  return { kind: 'unknown', message: UNKNOWN_OUTCOME_COPY };
}

function SectionLabel({ children, note }: { children: string; note?: string }): JSX.Element {
  return (
    <h3 className="text-text-muted mt-8 mb-3 text-xs font-semibold tracking-wider uppercase">
      {children}
      {note ? <span className="text-text-muted ml-2 font-normal tracking-normal normal-case">{note}</span> : null}
    </h3>
  );
}

function RunningCard({ label, value, description }: { label: string; value: string; description: string }): JSX.Element {
  return (
    <div className="border-border-default bg-surface-raised rounded-md border p-4">
      <p className="text-text-secondary text-sm">{label}</p>
      <p className="font-display text-text-primary mt-1 text-3xl leading-none font-medium">{value}</p>
      <p className="text-text-muted mt-3 text-sm">{description}</p>
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
  const [ackResetNotice, setAckResetNotice] = useState<string | null>(null);
  const [submission, setSubmission] = useState<CapacitySubmission | null>(null);
  const [outcome, setOutcome] = useState<SaveOutcome>({ kind: 'idle' });
  const [conflictLatest, setConflictLatest] = useState<CapacityBase | null>(null);
  /** A usable read whose revision moved while the form was dirty or unresolved. */
  const [externalLatest, setExternalLatest] = useState<CapacityBase | null>(null);
  const [conflictUnusable, setConflictUnusable] = useState<string | null>(null);
  const [checkedRead, setCheckedRead] = useState<CapacityBase | null>(null);
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});
  const [detailsOpen, setDetailsOpen] = useState(false);

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
        setAckResetNotice('The environment override changed; confirm it again before saving.');
      }
      if (next.shadowedKeys.length === 0) {
        setAck(false);
        setAckResetNotice(null);
      }
      return next;
    });
  }, [snapshot]);

  const shadowed = snapshot !== null && snapshot.environment_shadowed.length > 0;

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
  const rationaleOnlyDirty = !valuesChanged && reason.trim().length > 0;
  const unresolved = outcome.kind === 'uncertain' || outcome.kind === 'unknown'
    || conflictLatest !== null || conflictUnusable !== null || externalLatest !== null;
  const dirty = valuesChanged || rationaleOnlyDirty || unresolved;

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

  const acceptBase = useCallback((next: CapacityBase) => {
    setBase(next);
    setWorkersText(String(next.pair.queue_workers));
    setCapText(String(next.pair.host_global_session_cap));
  }, []);

  /**
   * Accept a usable read into `base` ONLY when that cannot lose operator work:
   *
   *  - no base yet            -> seed it;
   *  - same revision          -> observations only, `base` untouched;
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
    if (current.revision === observed.revision) return;
    if (!guardRef.current.dirty && !guardRef.current.writeLocked) {
      acceptBase(observed);
      return;
    }
    setExternalLatest(observed);
  }, [snapshot, acceptBase]);

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

  const clearReconciliation = useCallback(() => {
    setConflictLatest(null);
    setConflictUnusable(null);
    setExternalLatest(null);
    setCheckedRead(null);
    setSubmission(null);
    setOutcome({ kind: 'idle' });
    setAckResetNotice(null);
    queueMicrotask(() => reconciledRef.current?.focus());
  }, []);

  /** Keep my draft, rebase onto latest. Sends NO request. */
  const rebaseOntoLatest = useCallback(() => {
    const latest = conflictLatest ?? externalLatest ?? checkedRead;
    if (latest === null) return;
    setBase(latest);
    clearReconciliation();
  }, [conflictLatest, externalLatest, checkedRead, clearReconciliation]);

  /** Discard draft, accept latest. Sends NO request. */
  const acceptLatest = useCallback(() => {
    const latest = conflictLatest ?? externalLatest ?? checkedRead;
    if (latest === null) return;
    acceptBase(latest);
    setReason('');
    setAck(false);
    clearReconciliation();
  }, [conflictLatest, externalLatest, checkedRead, acceptBase, clearReconciliation]);

  const discardDraft = useCallback(() => {
    if (base === null) return;
    acceptBase(base);
    setReason('');
    setAck(false);
    setFieldErrors({});
    setOutcome({ kind: 'idle' });
  }, [base, acceptBase]);

  /** "Check saved values" / "Refresh running state" — a read, never a write. */
  const refreshObservations = useCallback(async () => {
    const result = await query.refetch();
    const fresh = (result as { data?: unknown } | undefined)?.data;
    const classified = classifySnapshot(fresh);
    if (classified.status === 'unusable') return;
    const observed = baseFromSnapshot(classified.snapshot);
    // A read after a conflict or an uncertain outcome is recorded as a `latest`
    // observation only. It never moves base and never clears the write lock.
    if (writeLocked) setCheckedRead(observed);
  }, [query, writeLocked]);

  async function submit(event: React.FormEvent): Promise<void> {
    event.preventDefault();
    if (base === null || snapshot === null) return;
    if (writeLocked) {
      setOutcome({
        kind: 'rejected',
        message: 'Reconcile the saved values before saving again. Choose to rebase onto the latest saved values or to discard your draft and accept them.',
      });
      return;
    }

    // Validate input TEXT before any Number conversion (G3).
    const errors: Record<string, string> = {};
    const workers = parseCapacityText(workersText);
    const cap = parseCapacityText(capText);
    if (!workers.ok) errors.queue_workers = numericTextMessage(workers.reason, WORKERS_LABEL);
    if (!cap.ok) errors.host_global_session_cap = numericTextMessage(cap.reason, CAP_LABEL);
    const trimmedReason = reason.trim();
    if (trimmedReason.length === 0) {
      errors.rationale = 'Reason for change is required.';
    } else if (trimmedReason.length > REASON_MAX_LENGTH) {
      errors.rationale = `Reason for change must be ${REASON_MAX_LENGTH} characters or fewer.`;
    }
    if (shadowed && !ack) {
      errors.ack = 'Confirm the environment override before saving.';
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
        message: 'Check the highlighted fields before saving. Nothing was sent.',
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
    try {
      const result = await save.mutateAsync({
        revision: record.baseRevision,
        queue_workers: record.pair.queue_workers,
        host_global_session_cap: record.pair.host_global_session_cap,
        rationale: record.reason,
        confirm_environment_shadow: record.ack,
      });
      const classified = classifySnapshot(result);
      if (classified.status === 'unusable') {
        // A 200 we cannot read is an UNKNOWN outcome, not a success.
        setOutcome({ kind: 'unknown', message: UNKNOWN_OUTCOME_COPY });
        return;
      }
      acceptBase(baseFromSnapshot(classified.snapshot));
      setReason('');
      setFieldErrors({});
      setSubmission(null);
      setOutcome({ kind: 'saved', snapshot: classified.snapshot });
    } catch (error) {
      const classified = classifySaveError(error);
      setOutcome(classified);
      if (classified.kind === 'rejected' && classified.focus) focusField(classified.focus);
      if (error instanceof ApiError && error.code === 'stale_revision') {
        const latest = (error.detail as { latest?: unknown } | null)?.latest;
        const classifiedLatest = classifySnapshot(latest);
        if (classifiedLatest.status === 'unusable') {
          setConflictUnusable(
            classifiedLatest.reason === 'representation'
              ? `Latest saved values are ${REPRESENTATION_UNAVAILABLE.toLowerCase()}`
              : 'Latest saved values could not be read.',
          );
          setConflictLatest(null);
        } else {
          setConflictLatest(baseFromSnapshot(classifiedLatest.snapshot));
          setConflictUnusable(null);
        }
        setOutcome({
          kind: 'rejected',
          message: 'Saved settings changed elsewhere. Your draft is preserved.',
        });
      }
    }
  }

  // The page header is rendered in EVERY state. A loading, denied or unusable
  // read must not blank the surface: the operator still needs to know which
  // page they are on and what the failure was.
  const header = (
    <header>
      <h2 id="capacity-panel-heading" className="font-display text-text-primary text-2xl font-medium">Capacity</h2>
      <p className="text-text-secondary mt-1 max-w-prose text-sm">
        Set how many sessions this daemon can admit. Changes are saved for the next daemon start.
      </p>
      <div className="mt-3 flex flex-wrap items-center gap-2">
        <Pill tone="neutral">All organizations</Pill>
        <Pill tone="neutral">Changes require restart</Pill>
      </div>
      <p className="text-text-muted mt-3 text-sm">
        Daemon bearer required. This bearer-based authorization cannot be attributed to a verified
        person. This resource affects every org.
      </p>
    </header>
  );

  if (query.isLoading) {
    return (
      <div className="space-y-2">
        {header}
        <p role="status" className="text-text-secondary mt-4 text-sm">Loading daemon capacity…</p>
      </div>
    );
  }
  if ((query.isError || query.data === undefined) && base === null) {
    return (
      <div className="space-y-2">
        {header}
        <p role="alert" className="border-border-default bg-danger-soft text-feedback-danger mt-4 rounded-md border p-3 text-sm">
          Could not load daemon capacity. No values are displayed. {query.error?.message}
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
            ? `Capacity values are ${REPRESENTATION_UNAVAILABLE.toLowerCase()} No values are displayed and editing is unavailable.`
            : READ_UNUSABLE}
        </p>
      </div>
    );
  }

  const receipt = formatReceipt(query.observation?.receiptAt ?? null);
  const pending = save.isPending;
  const reconciliationNeeded = conflictLatest !== null || conflictUnusable !== null
    || externalLatest !== null;
  const canReconcile = conflictLatest !== null || externalLatest !== null || checkedRead !== null;
  // Deliberately NOT disabled by `writeLocked`: a second Save attempt must be
  // explicitly REFUSED with a reason, not silently inert — and never a silent
  // last-write-wins.
  const saveDisabled = pending || snapshot === null
    || readUnusableReason !== null || inconsistentRead;

  return (
    <div className="space-y-2">
      {header}

      {readUnusableReason !== null && (
        <p role="alert" className="border-border-default bg-attention-soft text-attention-text mt-4 rounded-md border p-3 text-sm">
          {readUnusableReason === 'representation'
            ? `Latest capacity values are ${REPRESENTATION_UNAVAILABLE.toLowerCase()} Editing is unavailable against this read.`
            : READ_UNUSABLE}
          {' '}Previously received values are shown below under “Last known”.
          {receipt ? ` ${receipt}` : ''}
        </p>
      )}
      {inconsistentRead && (
        <p role="alert" className="border-border-default bg-attention-soft text-attention-text mt-4 rounded-md border p-3 text-sm">
          {INCONSISTENT_RESPONSE} Editing is unavailable against this read.
        </p>
      )}

      {snapshot !== null && (
        <>
          <SectionLabel note="— observed from the daemon; not changed by saving">Running now</SectionLabel>
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <RunningCard
              label="Task session slots running"
              value={String(snapshot.running_at_daemon_start.queue_workers)}
              description="Workers started with the daemon. Shared across all organizations."
            />
            <RunningCard
              label="Host session admission limit running"
              value={
                snapshot.effective_admission_cap === null
                  ? 'Unavailable'
                  : String(snapshot.effective_admission_cap)
              }
              description={
                snapshot.effective_admission_cap === null
                  ? `Unavailable — ${snapshot.effective_admission_reason}. The runtime effect of a saved value cannot be verified from this page.`
                  : 'Ceiling on admitted sessions. A ceiling, not a count of active or free sessions.'
              }
            />
          </div>
          {/* The reason is shown where it ADDS something: when the cap is
              unavailable it is already in the card, and when the cap simply
              matches the startup value there is nothing to explain. */}
          {snapshot.effective_admission_cap !== null
            && snapshot.effective_admission_cap !== snapshot.running_at_daemon_start.host_global_session_cap && (
            <p className="text-text-secondary mt-2 text-sm">
              Startup configured host limit {snapshot.running_at_daemon_start.host_global_session_cap};
              running effective {snapshot.effective_admission_cap} — {snapshot.effective_admission_reason}
              {' '}The startup value is not a count of available slots.
            </p>
          )}

          <SectionLabel>Startup, saved and next start</SectionLabel>
          <table className="border-border-default w-full border-collapse overflow-hidden rounded-md border text-sm">
            <thead>
              <tr className="text-text-muted bg-surface-sunken text-left text-xs tracking-wide uppercase">
                <th scope="col" className="border-border-default border-b p-3 font-semibold">Setting</th>
                <th scope="col" className="border-border-default border-b p-3 font-semibold">Running at startup</th>
                <th scope="col" className="border-border-default border-b p-3 font-semibold">Saved in file</th>
                <th scope="col" className="border-border-default border-b p-3 font-semibold">Expected next start</th>
              </tr>
            </thead>
            <tbody>
              {([
                ['queue_workers', WORKERS_LABEL] as const,
                ['host_global_session_cap', CAP_LABEL] as const,
              ]).map(([key, label]) => (
                <tr key={key}>
                  <th scope="row" className="border-border-default border-b p-3 text-left font-medium">
                    {label}
                    <code className="text-text-muted ml-2 font-mono text-xs font-normal">{key}</code>
                  </th>
                  <td className="border-border-default border-b p-3 font-mono">
                    {snapshot.running_at_daemon_start[key]}
                  </td>
                  <td className="border-border-default border-b p-3 font-mono">
                    {snapshot.persisted_yaml[key] === null ? 'Not set in file' : snapshot.persisted_yaml[key]}
                  </td>
                  <td className="border-border-default border-b p-3 font-mono">
                    {snapshot.next_start[key]}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="text-text-muted mt-2 text-sm">
            Expected next start is best effort, based on the configuration observed by this daemon and
            assuming an unchanged environment and worker topology. It is not a guarantee.
          </p>
          <div className="mt-3">
            <Pill tone="accent">
              <span aria-hidden="true">●</span>
              {snapshot.restart_pending ? 'Restart pending' : 'No restart pending'}
            </Pill>
          </div>
          {snapshot.restart_pending && (
            <p role="status" className="text-text-secondary mt-2 text-sm">
              A persisted next-start value differs from the running startup snapshot. Saving never applies
              live and this page cannot restart the daemon.
            </p>
          )}
          {snapshot.warnings.map((warning) => (
            <p key={warning} role="alert" className="border-border-default bg-attention-soft text-attention-text mt-2 rounded-md border p-3 text-sm">
              {warning}
            </p>
          ))}
        </>
      )}

      {receipt && (
        <p className="text-text-muted mt-3 text-xs">{receipt}</p>
      )}

      <form onSubmit={submit} noValidate>
        <SectionLabel>Change saved settings</SectionLabel>

        <div className="border-border-default border-b pb-5">
          <label htmlFor="capacity-workers" className="text-text-primary text-sm font-semibold">
            {WORKERS_LABEL}
            <code className="text-text-muted ml-2 font-mono text-xs font-normal">queue_workers</code>
          </label>
          <p id="capacity-workers-help" className="text-text-secondary mt-1 max-w-prose text-sm">
            Maximum task-worker slots across all organizations. Other limits can keep fewer sessions
            running.
          </p>
          <div className="mt-2 flex flex-wrap items-center gap-3">
            <Input
              id="capacity-workers"
              ref={workersRef}
              type="text"
              inputMode="numeric"
              autoComplete="off"
              className="w-28 font-mono"
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
            <p id="capacity-workers-guidance" className="text-text-muted text-sm">
              {snapshot ? `${snapshot.guidance.queue_workers} ` : ''}Guidance only, not an enforced range.
            </p>
            {fieldErrors.queue_workers && (
              <p id="capacity-workers-error" role="alert" className="text-feedback-danger text-sm">
                {fieldErrors.queue_workers}
              </p>
            )}
          </div>
        </div>

        <div className="border-border-default border-b py-5">
          <label htmlFor="capacity-cap" className="text-text-primary text-sm font-semibold">
            {CAP_LABEL}
            <code className="text-text-muted ml-2 font-mono text-xs font-normal">host_global_session_cap</code>
          </label>
          <p id="capacity-cap-help" className="text-text-secondary mt-1 max-w-prose text-sm">
            Shared by task, thread, dream, wake and schedule sessions. Does not cap every process on the
            machine, and excludes headless System Assistant and job processes.
          </p>
          <div className="mt-2 flex flex-wrap items-center gap-3">
            <Input
              id="capacity-cap"
              ref={capRef}
              type="text"
              inputMode="numeric"
              autoComplete="off"
              className="w-28 font-mono"
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
            <p id="capacity-cap-guidance" className="text-text-muted text-sm">
              {snapshot ? `${snapshot.guidance.host_global_session_cap} ` : ''}Guidance only, not an enforced range.
            </p>
            {fieldErrors.host_global_session_cap && (
              <p id="capacity-cap-error" role="alert" className="text-feedback-danger text-sm">
                {fieldErrors.host_global_session_cap}
              </p>
            )}
          </div>
        </div>

        {/* Draft consequence panel */}
        {consequence !== null && (
          <div className="border-border-default bg-surface-raised mt-5 rounded-md border">
            <p className="border-border-default text-text-primary border-b p-3 text-sm font-medium">
              {valuesChanged ? 'Draft changes the saved configuration' : 'Draft matches the saved configuration'}
            </p>
            {consequence.status === 'ok' && draftPair !== null ? (
              <>
                <div className="flex flex-wrap gap-8 p-4">
                  <div>
                    <p className="text-text-secondary text-sm">{WORKERS_LABEL}</p>
                    <p className="font-display text-text-primary mt-1 text-xl">{draftPair.queue_workers}</p>
                  </div>
                  <div>
                    <p className="text-text-secondary text-sm">Host admission limit</p>
                    <p className="font-display text-text-primary mt-1 text-xl">{draftPair.host_global_session_cap}</p>
                  </div>
                  <div>
                    <p className="text-text-secondary text-sm">Worker-pool total</p>
                    <p className="font-display text-text-primary mt-1 text-xl">
                      {consequence.workerPoolTotal}
                      <span className="text-text-muted ml-2 font-sans text-sm">
                        {draftPair.queue_workers} task + {consequence.nonTaskContribution} other producers
                      </span>
                    </p>
                  </div>
                </div>
                <p className="border-border-default bg-surface-sunken text-text-secondary m-4 mt-0 rounded-md border p-3 text-sm">
                  {consequenceMessage(
                    consequence.direction,
                    draftPair.host_global_session_cap,
                    consequence.workerPoolTotal,
                  )}
                </p>
              </>
            ) : (
              <p role="alert" className="text-attention-text p-4 text-sm">
                {consequence.status === 'inconsistent'
                  ? INCONSISTENT_RESPONSE
                  : `Worker-pool total is ${REPRESENTATION_UNAVAILABLE.toLowerCase()}`}
              </p>
            )}
          </div>
        )}

        {rationaleOnlyDirty && (
          <p role="status" className="text-text-secondary mt-3 text-sm">
            The values are unchanged from the saved file; only the reason differs.
          </p>
        )}

        {/* Environment override */}
        {shadowed && snapshot !== null && preview !== null && (
          <div role="group" aria-labelledby="capacity-override-heading" className="border-border-default bg-attention-soft mt-5 rounded-md border p-4">
            <p id="capacity-override-heading" className="text-attention-text text-sm font-semibold">
              Environment override in effect
            </p>
            <p className="text-text-secondary mt-1 text-sm">{snapshot.environment_warning}</p>
            <p className="text-text-secondary mt-2 text-sm">
              {preview.shadowedKeys
                .map((key) => CAPACITY_FIELD_LABELS[key] ?? key)
                .join(' and ')}
              {preview.shadowedKeys.length === 1 ? ' is' : ' are'} set by the environment.
              Expected next start with this draft: {WORKERS_LABEL} {preview.pair.queue_workers},{' '}
              {CAP_LABEL} {preview.pair.host_global_session_cap}. Assumes unchanged environment and
              worker topology.
            </p>
            {ackResetNotice && (
              <p role="alert" className="text-attention-text mt-2 text-sm font-medium">{ackResetNotice}</p>
            )}
            <label className="text-text-primary mt-3 flex items-start gap-2 text-sm">
              <input
                ref={ackRef}
                type="checkbox"
                checked={ack}
                disabled={pending}
                aria-describedby={fieldErrors.ack ? 'capacity-ack-error' : undefined}
                aria-invalid={fieldErrors.ack ? true : undefined}
                onChange={(event) => {
                  setAck(event.target.checked);
                  if (event.target.checked) setAckResetNotice(null);
                }}
              />
              <span>I understand a restart alone will not make the saved file win over the environment.</span>
            </label>
            {fieldErrors.ack && (
              <p id="capacity-ack-error" role="alert" className="text-feedback-danger mt-1 text-sm">
                {fieldErrors.ack}
              </p>
            )}
          </div>
        )}

        {/* Reason */}
        <div className="mt-5">
          <div className="flex items-baseline justify-between">
            <label htmlFor="capacity-reason" className="text-text-primary text-sm font-semibold">
              Reason for change
            </label>
            <span
              className="text-text-muted font-mono text-xs"
              role="status"
              aria-live="polite"
            >
              {reason.length} / {REASON_MAX_LENGTH}
              {reason.length >= REASON_MAX_LENGTH ? ' — limit reached' : ''}
            </span>
          </div>
          <p id="capacity-reason-help" className="text-text-secondary mt-1 max-w-prose text-sm">
            Briefly explain the intended adjustment. Reason included in the save request.
          </p>
          <Textarea
            id="capacity-reason"
            ref={reasonRef}
            className="mt-2"
            value={reason}
            disabled={pending}
            placeholder="e.g. Queue delay grew after adding the second team; raising task slots."
            aria-describedby={
              fieldErrors.rationale ? 'capacity-reason-help capacity-reason-error' : 'capacity-reason-help'
            }
            aria-invalid={fieldErrors.rationale ? true : undefined}
            onChange={(event) => setReason(event.target.value)}
          />
          {fieldErrors.rationale && (
            <p id="capacity-reason-error" role="alert" className="text-feedback-danger mt-1 text-sm">
              {fieldErrors.rationale}
            </p>
          )}
        </div>

        <div className="border-border-default mt-5 flex flex-wrap items-center gap-3 border-t pt-5">
          <Button type="submit" disabled={saveDisabled} loading={pending}>
            {pending ? 'Saving…' : 'Save for next restart'}
          </Button>
          <Button type="button" variant="outline" disabled={pending} onClick={discardDraft}>
            Discard draft
          </Button>
          <Button type="button" variant="ghost" disabled={pending} onClick={() => void refreshObservations()}>
            <RotateCcw aria-hidden="true" />
            Refresh running state
          </Button>
        </div>
        {dirty && (
          <p role="status" className="text-text-secondary mt-3 text-sm">
            Unsaved changes. Leaving or reloading will discard this draft.
          </p>
        )}

        {/* Outcome */}
        <div ref={reconciledRef} tabIndex={-1} className="mt-4 space-y-3 outline-none">
          {outcome.kind === 'saving' && (
            <p role="status" aria-live="polite" className="text-text-secondary text-sm">
              Saving for next restart…
            </p>
          )}
          {outcome.kind === 'saved' && (
            <div role="status" aria-live="polite" className="border-border-default bg-accent-muted text-accent-text rounded-md border p-3 text-sm">
              <p className="font-medium">
                {outcome.snapshot.restart_pending
                  ? 'Saved for next restart. Running limits are unchanged.'
                  : 'Saved. No restart is pending for these values.'}
              </p>
              {snapshot !== null && snapshot.environment_shadowed.length > 0 && preview !== null && (
                <p className="mt-1">
                  Saved value overridden: {preview.shadowedKeys
                    .map((key) => CAPACITY_FIELD_LABELS[key] ?? key).join(' and ')}
                  {preview.shadowedKeys.length === 1 ? ' is' : ' are'} set by the environment.
                  Expected next start: {WORKERS_LABEL}{' '}
                  {outcome.snapshot.next_start.queue_workers}, {CAP_LABEL}{' '}
                  {outcome.snapshot.next_start.host_global_session_cap}.
                </p>
              )}
              {outcome.snapshot.message && <p className="mt-1">{outcome.snapshot.message}</p>}
            </div>
          )}
          {outcome.kind === 'rejected' && (
            <p role="alert" className="border-border-default bg-danger-soft text-feedback-danger rounded-md border p-3 text-sm">
              {outcome.message}
            </p>
          )}
          {(outcome.kind === 'uncertain' || outcome.kind === 'unknown') && (
            <p role="alert" className="border-border-default bg-attention-soft text-attention-text rounded-md border p-3 text-sm">
              {outcome.message}
            </p>
          )}

          {submission !== null && outcome.kind !== 'saving' && (
            <div className="border-border-default rounded-md border p-3 text-sm">
              <p className="text-text-primary font-medium">
                You submitted {WORKERS_LABEL} {submission.pair.queue_workers}, {CAP_LABEL}{' '}
                {submission.pair.host_global_session_cap} against revision{' '}
                <code className="font-mono text-xs break-all">{submission.baseRevision}</code>.
              </p>
              {valuesChanged && (
                <p className="text-text-secondary mt-1">
                  Your current draft is {WORKERS_LABEL} {workersText}, {CAP_LABEL} {capText} and is
                  still unsaved. It is held separately from the submitted values.
                </p>
              )}
              <Button
                type="button"
                variant="outline"
                size="sm"
                className="mt-2"
                onClick={() => void refreshObservations()}
              >
                Check saved values
              </Button>
            </div>
          )}

          {conflictUnusable !== null && (
            <p role="alert" className="border-border-default rounded-md border p-3 text-sm">
              {conflictUnusable} No latest values are shown and no rebase is offered. Read the saved
              values successfully before saving again.
            </p>
          )}

          {(reconciliationNeeded || checkedRead !== null) && base !== null && (
            <div className="border-border-default rounded-md border p-3 text-sm">
              <p className="text-text-primary font-medium">
                {checkedRead !== null && submission !== null
                  ? comparisonHeadline(submission, base, checkedRead)
                  : 'Configuration changed elsewhere.'}
              </p>
              <dl className="mt-2 grid grid-cols-1 gap-1 sm:grid-cols-3">
                <div>
                  <dt className="text-text-muted text-xs tracking-wide uppercase">Accepted base</dt>
                  <dd className="font-mono">
                    {WORKERS_LABEL} {base.keyPresence.queue_workers ? base.pair.queue_workers : 'Not set in file'},{' '}
                    {CAP_LABEL} {base.keyPresence.host_global_session_cap ? base.pair.host_global_session_cap : 'Not set in file'}
                  </dd>
                </div>
                <div>
                  <dt className="text-text-muted text-xs tracking-wide uppercase">Your draft</dt>
                  <dd className="font-mono">
                    {WORKERS_LABEL} {workersText}, {CAP_LABEL} {capText}
                  </dd>
                </div>
                <div>
                  <dt className="text-text-muted text-xs tracking-wide uppercase">Currently saved</dt>
                  <dd className="font-mono">
                    {(() => {
                      const latest = conflictLatest ?? externalLatest ?? checkedRead;
                      if (latest === null) return 'Could not be read';
                      return `${WORKERS_LABEL} ${latest.keyPresence.queue_workers ? latest.pair.queue_workers : 'Not set in file'}, ${CAP_LABEL} ${latest.keyPresence.host_global_session_cap ? latest.pair.host_global_session_cap : 'Not set in file'}`;
                    })()}
                  </dd>
                </div>
              </dl>
              {canReconcile && (
                <div className="mt-3 flex flex-wrap gap-2">
                  <Button type="button" size="sm" variant="outline" onClick={rebaseOntoLatest}>
                    Keep my draft, rebase onto latest
                  </Button>
                  <Button type="button" size="sm" variant="outline" onClick={acceptLatest}>
                    Discard draft, accept latest
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
        <summary className="text-text-primary cursor-pointer p-4 text-sm font-medium">Capacity details</summary>
        {snapshot !== null && (
          <dl className="text-text-secondary grid grid-cols-1 gap-2 p-4 pt-0 text-sm sm:grid-cols-2">
            <dt>Producer envelope</dt>
            <dd className="font-mono">{snapshot.producer_envelope}</dd>
            <dt>Producer components</dt>
            <dd className="font-mono">
              {snapshot.producer_components.task_workers} task, {snapshot.producer_components.thread_workers} thread,{' '}
              {snapshot.producer_components.dream_workers} dream, {snapshot.producer_components.wake_workers} wake,{' '}
              {snapshot.producer_components.schedule_workers} schedule
            </dd>
            <dt>Running provenance</dt>
            <dd>{snapshot.running_provenance}</dd>
            <dt>Revision</dt>
            <dd className="font-mono text-xs break-all">{snapshot.revision}</dd>
            <dt>Audit</dt>
            <dd>
              The reason is included in the save request. Auditing is addressed org-locally and
              attributed to the daemon bearer; terminal completion of the audit entry is not
              guaranteed by this page.
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
        <DialogContent aria-label="discard capacity draft confirmation">
          <DialogHeader>
            <DialogTitle>Discard unsaved capacity changes?</DialogTitle>
            <DialogDescription>
              Your draft has not been saved. Stay to keep editing, or discard it and continue
              navigating.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button
              size="sm"
              variant="ghost"
              onClick={() => {
                if (blocker.state === 'blocked') blocker.reset();
                const restore = restoreFocusRef.current;
                restoreFocusRef.current = null;
                queueMicrotask(() => restore?.focus());
              }}
            >
              Stay on page
            </Button>
            <Button
              size="sm"
              onClick={() => {
                restoreFocusRef.current = null;
                if (blocker.state === 'blocked') blocker.proceed();
              }}
            >
              Discard and continue
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
): string {
  const matches = (pair: { queue_workers: number; host_global_session_cap: number }) =>
    read.pair.queue_workers === pair.queue_workers
    && read.pair.host_global_session_cap === pair.host_global_session_cap
    && read.keyPresence.queue_workers && read.keyPresence.host_global_session_cap;
  if (!read.keyPresence.queue_workers || !read.keyPresence.host_global_session_cap) {
    return 'The saved values are Not set in file. That is not equality with any of the values below.';
  }
  if (matches(submission.pair)) {
    return `Saved values now match what you submitted (${submission.pair.queue_workers} / ${submission.pair.host_global_session_cap}). This does not confirm your request caused it.`;
  }
  if (matches(base.pair)) {
    return 'The saved values are unchanged from your accepted base. The outcome of your request is still unknown.';
  }
  return 'The saved values still differ from what you submitted.';
}
